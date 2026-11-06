#!/usr/bin/env python3
"""U-Net 道路分割：独立测试集评估与可视化。

输出：
  * 指标：Road IoU / Background IoU / mIoU / Dice / Precision / Recall / Pixel Accuracy
  * 混淆矩阵
  * 逐图指标（便于找出最差的样本）
  * RGB / Ground Truth / Prediction 三联图

用法:
    perception/.venv/bin/python perception/tools/evaluate_unet.py \
        --weights perception/weights/aeroscapes_unet_512_expA.pt \
        --dataset data/aeroscapes/processed --split test \
        --out-dir perception/results/aeroscapes_expA --vis-count 24
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perception.road.unet import UNet  # noqa: E402

IMAGE_EXT = (".jpg", ".jpeg", ".png")


def resolve_device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class EvalDataset(Dataset):
    """返回原图、resize 后输入、以及原始分辨率 GT，便于可视化。"""

    def __init__(self, root: Path, split: str, size: tuple[int, int]):
        self.image_dir = root / "images" / split
        self.mask_dir = root / "masks" / split
        self.size = size
        self.items = sorted(p for p in self.image_dir.iterdir()
                            if p.suffix.lower() in IMAGE_EXT)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int):
        path = self.items[index]
        rgb = cv2.imread(str(path))
        gt = cv2.imread(str(self.mask_dir / f"{path.stem}.png"), cv2.IMREAD_GRAYSCALE)
        resized = cv2.resize(rgb, self.size, interpolation=cv2.INTER_AREA)
        gt_small = cv2.resize(gt, self.size, interpolation=cv2.INTER_NEAREST)
        tensor = torch.from_numpy(resized[:, :, ::-1].copy()).permute(2, 0, 1).float() / 255.0
        return tensor, torch.from_numpy((gt_small > 127).astype(np.float32)).unsqueeze(0), \
            path.stem, rgb.shape[:2], torch.from_numpy((gt > 127).astype(np.float32))


def metrics_from_confusion(matrix: np.ndarray) -> dict:
    tn, fp = matrix[0, 0], matrix[0, 1]
    fn, tp = matrix[1, 0], matrix[1, 1]
    union_road = tp + fp + fn
    union_bg = tn + fn + fp
    iou_road = tp / union_road if union_road else float("nan")
    iou_bg = tn / union_bg if union_bg else float("nan")
    return {
        "road_iou": iou_road,
        "background_iou": iou_bg,
        "mean_iou": float(np.nanmean([iou_road, iou_bg])),
        "dice": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else float("nan"),
        "precision": tp / (tp + fp) if (tp + fp) else float("nan"),
        "recall": tp / (tp + fn) if (tp + fn) else float("nan"),
        "pixel_accuracy": (tp + tn) / matrix.sum(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", type=Path, required=True)
    ap.add_argument("--dataset", type=Path, default=Path("data/aeroscapes/processed"))
    ap.add_argument("--split", default="test")
    ap.add_argument("--out-dir", type=Path, default=Path("perception/results/aeroscapes"))
    ap.add_argument("--vis-count", type=int, default=24)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    device = resolve_device(args.device)
    checkpoint = torch.load(args.weights, map_location=device, weights_only=False)
    base = int(checkpoint.get("base", 32))
    width, height = checkpoint.get("input_size", [512, 512])

    model = UNet(base=base).to(device)
    model.load_state_dict(checkpoint["model"])
    model.eval()

    dataset = EvalDataset(args.dataset, args.split, (width, height))
    loader = DataLoader(dataset, batch_size=args.batch, shuffle=False)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    confusion = np.zeros((2, 2), dtype=np.int64)
    per_image = []
    picked = max(1, len(dataset) // max(1, args.vis_count)) if args.vis_count else 0

    with torch.no_grad():
        for index, (tensor, target, stems, shapes, full_gt) in enumerate(loader):
            logits = model(tensor.to(device))
            preds = (torch.sigmoid(logits) >= 0.5).long().cpu().squeeze(1)
            targets = target.long().squeeze(1)

            for i in range(preds.shape[0]):
                pred = preds[i].numpy()
                gt = targets[i].numpy()
                matrix = np.bincount((gt * 2 + pred).ravel(), minlength=4).reshape(2, 2)
                confusion += matrix
                row = metrics_from_confusion(matrix)
                per_image.append({"stem": stems[i], **{k: round(float(v), 4)
                                                       for k, v in row.items()}})

                if args.vis_count and (index * args.batch + i) % picked == 0 and \
                        len(per_image) <= args.vis_count * 2:
                    rgb = cv2.imread(str(dataset.image_dir / f"{stems[i]}.jpg"))
                    h, w = rgb.shape[:2]
                    pred_full = cv2.resize(pred.astype(np.uint8), (w, h),
                                           interpolation=cv2.INTER_NEAREST)
                    gt_full = (full_gt[i].numpy() > 0.5).astype(np.uint8)
                    _write_triplet(args.out_dir / f"{args.split}_{stems[i]}.jpg",
                                   rgb, gt_full, pred_full)

    summary = metrics_from_confusion(confusion)
    report = {
        "weights": str(args.weights),
        "dataset": str(args.dataset),
        "split": args.split,
        "images": len(dataset),
        "input_size": [width, height],
        "device": str(device),
        "metrics": {k: round(float(v), 4) for k, v in summary.items()},
        "confusion_matrix": confusion.tolist(),
        "confusion_labels": ["pred_nonroad", "pred_road"],
        "worst_road_iou": sorted(per_image, key=lambda r: r["road_iou"])[:10],
        "per_image": per_image,
    }
    (args.out_dir / "test_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "test_report.md").write_text(_markdown(report), encoding="utf-8")

    print(f"\n== {args.split} 集评估（{len(dataset)} 张，输入 {width}x{height}）==")
    for key, value in report["metrics"].items():
        print(f"  {key:<16} {value:.4f}")
    tn, fp = confusion[0]
    fn, tp = confusion[1]
    print(f"\n  混淆矩阵（行=真实，列=预测）")
    print(f"  {'':>10}{'预测非道路':>12}{'预测道路':>12}")
    print(f"  {'真实非道路':>10}{tn:>12,}{fp:>12,}")
    print(f"  {'真实道路':>10}{fn:>12,}{tp:>12,}")
    print(f"\n报告: {args.out_dir/'test_report.json'}")
    return 0


def _write_triplet(path: Path, rgb: np.ndarray, gt: np.ndarray, pred: np.ndarray) -> None:
    gt_vis = (rgb * 0.25).astype(np.uint8)
    gt_vis[gt == 1] = (255, 255, 255)

    pred_vis = rgb.copy()
    road = pred == 1
    pred_vis[road] = (0.45 * pred_vis[road] + 0.55 * np.array([0, 255, 0])).astype(np.uint8)
    # 标出错误：GT 有而预测没有 = 红；预测有而 GT 没有 = 蓝
    miss = (gt == 1) & ~road
    extra = (gt == 0) & road
    pred_vis[miss] = (0.3 * pred_vis[miss] + 0.7 * np.array([0, 0, 255])).astype(np.uint8)
    pred_vis[extra] = (0.3 * pred_vis[extra] + 0.7 * np.array([255, 0, 0])).astype(np.uint8)

    panel = np.hstack([rgb, gt_vis, pred_vis])
    cv2.putText(panel, "RGB | GT | Prediction (red=miss, blue=false road)",
                (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    cv2.imwrite(str(path), panel, [cv2.IMWRITE_JPEG_QUALITY, 88])


def _markdown(report: dict) -> str:
    m = report["metrics"]
    matrix = report["confusion_matrix"]
    lines = [
        f"# 评估报告 · {report['split']}",
        "",
        f"- 权重：`{report['weights']}`",
        f"- 数据集：`{report['dataset']}`（{report['images']} 张）",
        f"- 输入尺寸：{report['input_size']}",
        f"- 设备：{report['device']}",
        "",
        "## 指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
    ]
    for key, value in m.items():
        lines.append(f"| {key} | {value:.4f} |")
    lines += [
        "",
        "## 混淆矩阵（行=真实，列=预测）",
        "",
        "| | 预测非道路 | 预测道路 |",
        "|---|---|---|",
        f"| 真实非道路 | {matrix[0][0]:,} | {matrix[0][1]:,} |",
        f"| 真实道路 | {matrix[1][0]:,} | {matrix[1][1]:,} |",
        "",
        "## Road IoU 最差的样本",
        "",
        "| 图像 | Road IoU | Recall | Precision |",
        "|---|---|---|---|",
    ]
    for row in report["worst_road_iou"]:
        lines.append(f"| {row['stem']} | {row['road_iou']:.4f} | "
                     f"{row['recall']:.4f} | {row['precision']:.4f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
