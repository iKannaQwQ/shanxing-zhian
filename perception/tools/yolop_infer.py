#!/usr/bin/env python3
"""YOLOP 推理封装（不改动 third_party 原始代码）。

与官方 tools/demo.py 的差异仅在于**设备选择与权重加载**：
  1. 官方 lib/utils/utils.py::select_device 对非 'cpu' 输入硬断言 CUDA 可用，
     在 Apple Silicon 上无法选择 MPS；
  2. torch>=2.6 的 torch.load 默认 weights_only=True，旧 checkpoint 会加载失败。
除此之外的前处理 / 前向 / 后处理 / 可视化逻辑逐行对齐官方 demo，保证数值可比。

用法:
    python perception/tools/yolop_infer.py --device cpu  --source <dir> --save-dir <dir>
    python perception/tools/yolop_infer.py --device mps  --source <dir> --save-dir <dir>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / "third_party" / "YOLOP"
sys.path.insert(0, str(REPO))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
import torchvision.transforms as transforms  # noqa: E402

from lib.config import cfg  # noqa: E402
from lib.models import get_net  # noqa: E402
from lib.dataset import LoadImages  # noqa: E402
from lib.core.general import non_max_suppression, scale_coords  # noqa: E402
from lib.utils import plot_one_box, show_seg_result  # noqa: E402

CLASS_NAMES = ["vehicle"]  # 原版 nc=1，训练时 single_cls 合并为单一类


def resolve_device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def load_state_dict(path: str, device: torch.device) -> dict:
    """兼容 torch 2.6+ 的 weights_only 默认变更。"""
    try:
        ckpt = torch.load(path, map_location=device, weights_only=True)
    except Exception:
        # 官方 checkpoint 含非张量元数据（epoch / optimizer 等），需放宽
        ckpt = torch.load(path, map_location=device, weights_only=False)
    sd = ckpt["state_dict"] if isinstance(ckpt, dict) and "state_dict" in ckpt else ckpt
    return {k.replace("module.", "", 1): v for k, v in sd.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=str(REPO / "weights" / "End-to-end.pth"))
    ap.add_argument("--source", default=str(REPO / "inference" / "images"))
    ap.add_argument("--img-size", type=int, default=640)
    ap.add_argument("--conf-thres", type=float, default=0.25)
    ap.add_argument("--iou-thres", type=float, default=0.45)
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"])
    ap.add_argument("--save-dir", default="perception/results/demo")
    ap.add_argument("--half", action="store_true", help="FP16（仅 CUDA 建议开启）")
    args = ap.parse_args()

    device = resolve_device(args.device)
    half = args.half and device.type == "cuda"
    print(f"[i] torch={torch.__version__} device={device} mps_available="
          f"{getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available()}")

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    model = get_net(cfg)
    sd = load_state_dict(args.weights, device)
    model.load_state_dict(sd, strict=True)
    model = model.to(device)
    model.eval()
    if half:
        model.half()

    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    dataset = LoadImages(args.source, img_size=args.img_size)
    summary = {"device": str(device), "torch": torch.__version__, "weights": str(args.weights),
               "images": []}

    with torch.no_grad():
        for path, img_, img0, _cap, shapes in dataset:
            t0 = time.perf_counter()
            img = transform(img_).to(device)
            if img.ndimension() == 3:
                img = img.unsqueeze(0)
            img = img.half() if half else img.float()

            det_out, da_seg_out, ll_seg_out = model(img)
            inf_out, _ = det_out
            det = non_max_suppression(inf_out, conf_thres=args.conf_thres,
                                      iou_thres=args.iou_thres, classes=None,
                                      agnostic=False)[0]
            _, _, height, width = img.shape
            pad_w, pad_h = shapes[1][1]
            pad_w, pad_h = int(pad_w), int(pad_h)
            ratio = shapes[1][0][1]
            up = max(1, int(1 / ratio))

            da = F.interpolate(da_seg_out[:, :, pad_h:(height - pad_h), pad_w:(width - pad_w)],
                               scale_factor=up, mode="bilinear")
            _, da_seg_mask = torch.max(da, 1)
            da_seg_mask = da_seg_mask.int().squeeze().cpu().numpy()

            ll = F.interpolate(ll_seg_out[:, :, pad_h:(height - pad_h), pad_w:(width - pad_w)],
                               scale_factor=up, mode="bilinear")
            _, ll_seg_mask = torch.max(ll, 1)
            ll_seg_mask = ll_seg_mask.int().squeeze().cpu().numpy()

            # 顺序与官方 demo 一致：先叠加分割色，再在结果图上画框
            # 注意 show_seg_result(is_demo=True) 会强制 resize 到 1280x720
            out = show_seg_result(img0, (da_seg_mask, ll_seg_mask), _, _, is_demo=True)

            dets = []
            if len(det):
                det[:, :4] = scale_coords(img.shape[2:], det[:, :4], out.shape).round()
                for *xyxy, conf, cls in reversed(det):
                    dets.append({"bbox_xyxy": [float(v) for v in xyxy], "score": float(conf),
                                 "cls": int(cls), "name": CLASS_NAMES[int(cls)]})
                    plot_one_box(xyxy, out, label=f"{CLASS_NAMES[int(cls)]} {conf:.2f}",
                                 color=(0, 0, 255), line_thickness=2)

            dst = save_dir / Path(path).name
            cv2.imwrite(str(dst), out)

            dt = (time.perf_counter() - t0) * 1000
            total = da_seg_mask.size
            summary["images"].append({
                "file": Path(path).name,
                "output": str(dst),
                "infer_ms": round(dt, 1),
                "drivable_ratio": round(float((da_seg_mask > 0).sum()) / total, 4),
                "lane_pixels": int((ll_seg_mask > 0).sum()),
                "lane_ratio": round(float((ll_seg_mask > 0).sum()) / total, 6),
                "detections": dets,
            })
            print(f"[+] {Path(path).name}: {dt:.0f} ms, det={len(dets)}, "
                  f"drivable={summary['images'][-1]['drivable_ratio']}, "
                  f"lane_px={summary['images'][-1]['lane_pixels']}")

    (save_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    print(f"[i] 结果写入 {save_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
