#!/usr/bin/env python3
"""独立 AI worker：轮询后端待处理帧 → 感知 → 回传结果。

它与后端是**两个进程**：
  * 后端不加载任何模型；
  * 本进程不碰数据库、不服务 HTTP，只做推理。

失败处理：本帧不回传，由后端在 `ROAD_FRAME_EXPIRE_S` 秒后标记为 expired 并推事件。
这样"AI 挂掉"不会让帧悄悄消失，而是变成一条可见的告警。

用法:
    # 常驻
    perception/.venv/bin/python scripts/ai_worker.py --backend http://127.0.0.1:8000

    # 处理一轮就退出（用于测试）
    perception/.venv/bin/python scripts/ai_worker.py --once

    # 注入合成落石验证中高风险路径
    perception/.venv/bin/python scripts/ai_worker.py --simulate-rock high
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "perception" / "src"))

from perception.service import PerceptionService  # noqa: E402


def http_json(url: str, method: str = "GET", payload: dict | None = None,
              timeout: float = 10.0) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body or "{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="http://127.0.0.1:8000")
    ap.add_argument("--interval", type=float, default=0.2, help="空闲时轮询间隔（秒）")
    ap.add_argument("--batch", type=int, default=2, help="每次拉取的帧数")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--rock-weights", default=None)
    ap.add_argument("--road-model", default=None,
                    help="道路模型：yolop 或 U-Net 权重路径（默认取 ROAD_ROAD_MODEL 或 expE）")
    ap.add_argument("--simulate-rock", default="none",
                    choices=["none", "medium", "high"])
    import os as _os
    ap.add_argument("--road-source", default=_os.environ.get("ROAD_ROAD_SOURCE", "road_model"),
                    choices=["road_model", "plane"],
                    help="可通行区来源：road_model=U-Net，plane=深度平面（固定俯视）")
    ap.add_argument("--obstacle-mode",
                    default=_os.environ.get("ROAD_OBSTACLE_MODE", "geometric"),
                    choices=["none", "geometric"],
                    help="障碍来源：geometric=深度几何（默认），none=关闭")
    ap.add_argument("--once", action="store_true", help="处理一轮后退出")
    ap.add_argument("--max-frames", type=int, default=0, help="处理 N 帧后退出，0=不限")
    args = ap.parse_args()

    base = args.backend.rstrip("/")
    print(f"[ai-worker] 后端 {base}", flush=True)
    import os
    road_model = args.road_model or os.environ.get("ROAD_ROAD_MODEL") or str(
        ROOT / "perception" / "weights" / "aeroscapes_unet_512_bs8_expE.pt")

    service = PerceptionService(device=args.device, rock_weights=args.rock_weights,
                                simulate_rock=args.simulate_rock,
                                road_model=road_model,
                                obstacle_mode=args.obstacle_mode,
                                road_source=args.road_source)
    print("[ai-worker] 模型就绪，开始轮询", flush=True)

    processed = failed = 0
    started = time.time()
    try:
        while True:
            try:
                status, body = http_json(f"{base}/api/v1/frames/pending?limit={args.batch}")
            except Exception as exc:  # noqa: BLE001
                print(f"[ai-worker] 后端不可达: {exc}", flush=True)
                time.sleep(2.0)
                continue
            if status != 200:
                print(f"[ai-worker] 拉取失败 HTTP {status}", flush=True)
                time.sleep(1.0)
                continue

            items = body.get("items") or []
            if not items:
                if args.once:
                    break
                time.sleep(args.interval)
                continue

            for item in items:
                frame_id = item["frame_id"]
                try:
                    report = service.process_files(
                        item["metadata"], item["rgb_path"],
                        item["depth_path"], item.get("confidence_path"))
                    # 把可视化需要的产物写进该帧目录，供管理后台读取
                    artifact_dir = Path(item["rgb_path"]).parent
                    mask = getattr(service, "last_road_mask", None)
                    if mask is not None:
                        import cv2
                        cv2.imwrite(str(artifact_dir / "road_mask.png"),
                                    (mask > 0).astype("uint8") * 255)
                    (artifact_dir / "perception.json").write_text(
                        json.dumps(report, ensure_ascii=False), encoding="utf-8")

                    code, response = http_json(
                        f"{base}/api/v1/ingest/perception",
                        method="POST",
                        payload={"frame_id": frame_id, "perception": report})
                    if code in (200, 202):
                        processed += 1
                        risk = response.get("overall_risk", "?")
                        print(f"[ai-worker] {frame_id[:8]} -> {risk} "
                              f"({report['metrics'].get('timings_ms', {}).get('perception_total')}ms)", flush=True)
                    else:
                        failed += 1
                        print(f"[ai-worker] 回传失败 HTTP {code}: {response}", flush=True)
                        # 契约拒绝说明这一帧永远过不了，必须标记失败，否则会被无限重试
                        try:
                            http_json(f"{base}/api/v1/ingest/perception", method="POST",
                                      payload={"frame_id": frame_id,
                                               "error": f"report rejected: {str(response)[:200]}"})
                        except Exception:  # noqa: BLE001
                            pass
                        import time as _t
                        _t.sleep(0.2)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"[ai-worker] 处理 {frame_id[:8]} 出错: {exc}", flush=True)
                    # 上报失败，让后端把这一帧标成 failed；否则它会被反复拉取
                    try:
                        http_json(f"{base}/api/v1/ingest/perception", method="POST",
                                  payload={"frame_id": frame_id,
                                           "error": f"{type(exc).__name__}: {exc}"})
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(0.2)

                if args.max_frames and processed >= args.max_frames:
                    print(f"[ai-worker] 已达 {args.max_frames} 帧，退出", flush=True)
                    return 0
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.time() - started
        print(f"[ai-worker] 结束：成功 {processed}，失败 {failed}，"
              f"用时 {elapsed:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
