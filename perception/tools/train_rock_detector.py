#!/usr/bin/env python3
"""训练落石检测器（ultralytics YOLO）。

用法:
    perception/.venv/bin/python perception/tools/train_rock_detector.py \
        --dataset data/sandbox/dataset --epochs 80 --imgsz 960
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, default=Path("data/sandbox/dataset"))
    ap.add_argument("--base", default="yolo11n.pt", help="预训练权重或模型名")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="auto", help="auto / cpu / mps / 0")
    ap.add_argument("--project", type=Path, default=Path("perception/results/train"))
    ap.add_argument("--name", default="rock_sandbox")
    args = ap.parse_args()

    data_yaml = args.dataset / "data.yaml"
    if not data_yaml.exists():
        raise SystemExit(f"缺少 {data_yaml}，先运行 prepare_sandbox_dataset.py")

    from ultralytics import YOLO

    device = args.device
    if device == "auto":
        import torch
        if torch.cuda.is_available():
            device = "0"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

    print(f"数据 {data_yaml}  底座 {args.base}  设备 {device}  imgsz {args.imgsz}")
    started = time.time()
    model = YOLO(args.base)
    model.train(data=str(data_yaml), epochs=args.epochs, imgsz=args.imgsz,
                batch=args.batch, device=device, project=str(args.project),
                name=args.name, exist_ok=True, workers=0, plots=False, seed=0)

    # 训练结束用验证集再评一次，指标写进报告
    metrics = model.val(data=str(data_yaml), imgsz=args.imgsz, device=device,
                        project=str(args.project), name=f"{args.name}_val",
                        exist_ok=True, plots=False)
    report = {
        "dataset": str(data_yaml),
        "base": args.base,
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "device": device,
        "seconds": round(time.time() - started, 1),
        "mAP50": round(float(metrics.box.map50), 4),
        "mAP50-95": round(float(metrics.box.map), 4),
        "precision": round(float(metrics.box.mp), 4),
        "recall": round(float(metrics.box.mr), 4),
    }
    out = Path(args.project) / f"{args.name}_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n最佳权重: {args.project}/{args.name}/weights/best.pt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
