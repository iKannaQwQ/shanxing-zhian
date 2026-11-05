#!/usr/bin/env python3
"""M5/M6 开发期接收入口（最小实现）。

用途：在真正的后端（FastAPI + SQLite + WebSocket）完成前，提供一个
**符合契约**的落点，让 iOS 采集端的上传链路可以被真实验证。

接口：
    GET  /api/v1/health          健康检查（iOS 的"连接测试"打这个）
    POST /api/v1/ingest/frame    multipart/form-data
                                 parts: meta(表单字段) / rgb / depth / confidence
    GET  /api/v1/stats           累计统计

校验：metadata 必须符合 contracts/schemas/frame_bundle.schema.json，
      rgb/depth/confidence 的字节数必须与 metadata 一致。

用法:
    perception/.venv/bin/python scripts/dev_receiver.py --port 8000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "schemas" / "frame_bundle.schema.json"
OUT_DIR = ROOT / "data" / "incoming"

app = FastAPI(title="mountain-dev-receiver")

_validator = Draft202012Validator(
    json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
    format_checker=FormatChecker())

stats: Counter[str] = Counter()
LAST_ERROR: dict[str, str] = {}


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok", "service": "mountain-dev-receiver", "epoch": time.time()}


@app.get("/api/v1/stats")
async def get_stats() -> dict:
    return {"received": stats["ok"], "rejected": stats["rejected"],
            "bytes": stats["bytes"], "last_error": LAST_ERROR.get("message")}


@app.post("/api/v1/ingest/frame")
async def ingest(meta: str = Form(...),
                 rgb: UploadFile = File(...),
                 depth: UploadFile = File(...),
                 confidence: UploadFile | None = File(default=None)):
    started = time.perf_counter()
    try:
        metadata = json.loads(meta)
    except json.JSONDecodeError as exc:
        return _reject("BAD_JSON", f"meta 不是合法 JSON: {exc}")

    errors = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
              for e in _validator.iter_errors(metadata)]
    if errors:
        return _reject("SCHEMA_INVALID", "; ".join(errors[:5]))

    rgb_bytes = await rgb.read()
    depth_bytes = await depth.read()
    conf_bytes = await confidence.read() if confidence is not None else None

    problems: list[str] = []
    if len(rgb_bytes) != metadata["capture"]["bytes"]:
        problems.append(f"rgb {len(rgb_bytes)}B != capture.bytes {metadata['capture']['bytes']}B")
    expected_depth = metadata["depth"]["width"] * metadata["depth"]["height"] * 4
    if len(depth_bytes) != expected_depth:
        problems.append(f"depth {len(depth_bytes)}B != {expected_depth}B")
    if conf_bytes is not None:
        conf_meta = metadata.get("confidence")
        if conf_meta is None:
            problems.append("收到 confidence 但 metadata 未声明")
        else:
            expected_conf = conf_meta["width"] * conf_meta["height"]
            if len(conf_bytes) != expected_conf:
                problems.append(f"confidence {len(conf_bytes)}B != {expected_conf}B")

    if problems:
        return _reject("SIZE_MISMATCH", "; ".join(problems))

    frame_id = metadata["frame_id"]
    target = OUT_DIR / frame_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "metadata.json").write_text(meta, encoding="utf-8")
    (target / "rgb.jpg").write_bytes(rgb_bytes)
    (target / "depth.bin").write_bytes(depth_bytes)
    if conf_bytes is not None:
        (target / "confidence.bin").write_bytes(conf_bytes)

    total = len(rgb_bytes) + len(depth_bytes) + (len(conf_bytes) if conf_bytes else 0)
    stats["ok"] += 1
    stats["bytes"] += total
    elapsed_ms = (time.perf_counter() - started) * 1000

    if stats["ok"] % 10 == 0:
        mb = stats["bytes"] / 1024 / 1024
        print(f"[{stats['ok']:>5}] seq={metadata['seq']:<5} "
              f"rgb={len(rgb_bytes)//1024}KB depth={len(depth_bytes)//1024}KB "
              f"total={total//1024}KB  {elapsed_ms:.1f}ms  累计 {mb:.0f}MB")

    return {"ok": True, "frame_id": frame_id, "total_bytes": total,
            "sha256": {"rgb": hashlib.sha256(rgb_bytes).hexdigest()[:16],
                       "depth": hashlib.sha256(depth_bytes).hexdigest()[:16]},
            "elapsed_ms": round(elapsed_ms, 2)}


def _reject(code: str, message: str) -> JSONResponse:
    stats["rejected"] += 1
    LAST_ERROR["message"] = f"{code}: {message}"
    print(f"[REJECT] {code}: {message}")
    return JSONResponse(status_code=422, content={"ok": False, "code": code,
                                                  "message": message})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"dev receiver: http://{args.host}:{args.port}")
    print("  健康检查: GET  /api/v1/health")
    print("  上传:     POST /api/v1/ingest/frame")
    print(f"  落盘目录: {OUT_DIR}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
