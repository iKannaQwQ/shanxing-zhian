#!/usr/bin/env python3
"""道路分割 U-Net 训练（AeroScapes / 沙盘通用）。

相对旧版补齐的缺口：
  * 同步几何增强（图像与 mask 用同一变换，mask 最近邻）
  * 断点续训（last.pt 含优化器/调度器/epoch/随机状态）
  * 独立 test 集评估 + 完整指标（Road IoU / mIoU / Dice / P / R / PixelAcc / 混淆矩阵）
  * 实验配置落盘（seed、torch 版本、数据集指纹、增强参数），保证可复现

用法:
    # 训 AeroScapes
    perception/.venv/bin/python perception/tools/train_road_unet.py \
        --dataset data/aeroscapes/processed --size 512x512 --epochs 40 \
        --out perception/weights/aeroscapes_unet_512.pt

    # 断点续训
    ... --resume perception/weights/aeroscapes_unet_512_last.pt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perception.road.unet import UNet, combined_loss  # noqa: E402

IMAGE_EXT = (".jpg", ".jpeg", ".png")
IGNORE_VALUE = 255


def resolve_device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class SegDataset(Dataset):
    """images/{split} + masks/{split}。mask 约定：>127 为道路，其余为非道路。"""

    def __init__(self, root: Path, split: str, size: tuple[int, int], augment: bool,
                 seed: int = 0):
        self.image_dir = root / "images" / split
        self.mask_dir = root / "masks" / split
        self.size = size
        self.augment = augment
        self.seed = seed
        if not self.image_dir.exists():
            raise FileNotFoundError(f"{self.image_dir} 不存在")
        self.items = sorted(p for p in self.image_dir.iterdir()
                            if p.suffix.lower() in IMAGE_EXT)
        if not self.items:
            raise FileNotFoundError(f"{self.image_dir} 下没有图像")

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int):
        image_path = self.items[index]
        image = cv2.imread(str(image_path))
        mask = cv2.imread(str(self.mask_dir / f"{image_path.stem}.png"),
                          cv2.IMREAD_GRAYSCALE)
        if image is None or mask is None:
            raise ValueError(f"读取失败: {image_path.name}")

        image = cv2.resize(image, self.size, interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, self.size, interpolation=cv2.INTER_NEAREST)

        if self.augment:
            rng = random.Random((self.seed + index) * 1_000_003 + int(time.time_ns() % 1_000_000))
            image, mask = self._geometric(image, mask, rng)
            image = self._photometric(image, rng)

        x = torch.from_numpy(image[:, :, ::-1].copy()).permute(2, 0, 1).float() / 255.0
        y = torch.from_numpy((mask > 127).astype(np.float32)).unsqueeze(0)
        return x, y

    def _geometric(self, image: np.ndarray, mask: np.ndarray, rng: random.Random):
        """几何变换：图像与 mask 用完全相同的参数，mask 一律最近邻。"""
        height, width = self.size[1], self.size[0]

        if rng.random() < 0.5:
            image = cv2.flip(image, 1)
            mask = cv2.flip(mask, 1)

        angle = rng.uniform(-10, 10)
        if abs(angle) > 0.5:
            matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
            image = cv2.warpAffine(image, matrix, (width, height),
                                   flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            mask = cv2.warpAffine(mask, matrix, (width, height),
                                  flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REFLECT)

        scale = rng.uniform(0.85, 1.15)
        if abs(scale - 1.0) > 0.02:
            resized = (max(8, int(width * scale)), max(8, int(height * scale)))
            image = cv2.resize(image, resized, interpolation=cv2.INTER_LINEAR)
            mask = cv2.resize(mask, resized, interpolation=cv2.INTER_NEAREST)
            if scale >= 1.0:
                x0 = rng.randint(0, resized[0] - width)
                y0 = rng.randint(0, resized[1] - height)
            else:
                pad_w, pad_h = width - resized[0], height - resized[1]
                x0, y0 = -rng.randint(0, pad_w), -rng.randint(0, pad_h)
                canvas = np.zeros((height, width, 3), dtype=np.uint8)
                canvas_mask = np.zeros((height, width), dtype=np.uint8)
                src_x0, src_y0 = max(0, -x0), max(0, -y0)
                dst_x0, dst_y0 = max(0, x0), max(0, y0)
                w = min(resized[0] - src_x0, width - dst_x0)
                h = min(resized[1] - src_y0, height - dst_y0)
                canvas[dst_y0:dst_y0 + h, dst_x0:dst_x0 + w] = \
                    image[src_y0:src_y0 + h, src_x0:src_x0 + w]
                canvas_mask[dst_y0:dst_y0 + h, dst_x0:dst_x0 + w] = \
                    mask[src_y0:src_y0 + h, src_x0:src_x0 + w]
                return canvas, canvas_mask
            image = image[y0:y0 + height, x0:x0 + width]
            mask = mask[y0:y0 + height, x0:x0 + width]

        return image, mask

    def _photometric(self, image: np.ndarray, rng: random.Random) -> np.ndarray:
        out = image.astype(np.float32)
        out *= rng.uniform(0.8, 1.2)                      # 亮度
        mean = out.mean()
        out = (out - mean) * rng.uniform(0.8, 1.2) + mean  # 对比度
        out += np.array([rng.uniform(-8, 8) for _ in range(3)], dtype=np.float32)  # 色彩
        return np.clip(out, 0, 255).astype(np.uint8)


def confusion_matrix(pred: torch.Tensor, target: torch.Tensor, num_classes: int = 2):
    index = (target * num_classes + pred).view(-1)
    return torch.bincount(index, minlength=num_classes ** 2).reshape(num_classes, num_classes)


def metrics_from_confusion(matrix: torch.Tensor) -> dict:
    matrix = matrix.double()
    tp_road = matrix[1, 1].item()
    fp_road = matrix[0, 1].item()
    fn_road = matrix[1, 0].item()
    tn = matrix[0, 0].item()

    union_road = tp_road + fp_road + fn_road
    union_bg = tn + fn_road + fp_road
    iou_road = tp_road / union_road if union_road else float("nan")
    iou_bg = tn / union_bg if union_bg else float("nan")
    precision = tp_road / (tp_road + fp_road) if (tp_road + fp_road) else float("nan")
    recall = tp_road / (tp_road + fn_road) if (tp_road + fn_road) else float("nan")
    dice = (2 * tp_road / (2 * tp_road + fp_road + fn_road)
            if (2 * tp_road + fp_road + fn_road) else float("nan"))
    total = matrix.sum().item()
    pixel_acc = (tp_road + tn) / total if total else float("nan")

    return {
        "road_iou": iou_road,
        "background_iou": iou_bg,
        "mean_iou": float(np.nanmean([iou_road, iou_bg])),
        "dice": dice,
        "precision": precision,
        "recall": recall,
        "pixel_accuracy": pixel_acc,
    }


@torch.no_grad()
def evaluate(model: UNet, loader: DataLoader, device: torch.device) -> tuple[dict, torch.Tensor]:
    model.eval()
    total = torch.zeros(2, 2, dtype=torch.int64)
    for x, y in loader:
        x, y = x.to(device), y.to(device)
        logits = model(x)
        pred = (torch.sigmoid(logits) >= 0.5).long().squeeze(1)
        total += confusion_matrix(pred.cpu(), y.long().squeeze(1).cpu())
    return metrics_from_confusion(total), total


def dataset_fingerprint(root: Path) -> dict:
    hasher = hashlib.sha256()
    for split in ("train", "val", "test"):
        directory = root / "masks" / split
        if directory.exists():
            for path in sorted(directory.glob("*.png")):
                hasher.update(path.stem.encode())
    return {
        "root": str(root),
        "digest": hasher.hexdigest()[:16],
        "splits": {split: len(list((root / "images" / split).glob("*")))
                   for split in ("train", "val", "test")
                   if (root / "images" / split).exists()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, default=Path("data/aeroscapes/processed"))
    ap.add_argument("--out", type=Path, default=Path("perception/weights/road_unet.pt"))
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--base", type=int, default=32)
    ap.add_argument("--size", default="512x512")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--augment", action="store_true", help="启用几何+光度增强（实验 B）")
    ap.add_argument("--resume", type=Path, default=None)
    ap.add_argument("--eval-test", action="store_true", help="训练结束后在 test 集评估")
    args = ap.parse_args()

    width, height = (int(v) for v in args.size.lower().split("x"))
    device = resolve_device(args.device)

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    train_set = SegDataset(args.dataset, "train", (width, height), args.augment, args.seed)
    val_set = SegDataset(args.dataset, "val", (width, height), False, args.seed)
    train_loader = DataLoader(train_set, batch_size=args.batch, shuffle=True,
                              num_workers=args.workers, drop_last=len(train_set) > args.batch)
    val_loader = DataLoader(val_set, batch_size=args.batch, shuffle=False,
                            num_workers=args.workers)

    model = UNet(base=args.base).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    start_epoch, best_iou = 1, 0.0
    if args.resume and args.resume.exists():
        checkpoint = torch.load(args.resume, map_location=device, weights_only=False)
        model.load_state_dict(checkpoint["model"])
        if "optimizer" in checkpoint:
            optimizer.load_state_dict(checkpoint["optimizer"])
        if "scheduler" in checkpoint:
            scheduler.load_state_dict(checkpoint["scheduler"])
        start_epoch = int(checkpoint.get("epoch", 0)) + 1
        best_iou = float(checkpoint.get("best_iou", 0.0))
        print(f"[resume] 从 epoch {start_epoch} 继续，best IoU {best_iou:.4f}")

    config = {
        "dataset": dataset_fingerprint(args.dataset),
        "model": {"name": "UNet", "base": args.base, "in_channels": 3, "out_channels": 1,
                  "params": UNet(base=args.base).parameter_count},
        "input_size": [width, height],
        "loss": "BCEWithLogits + Dice",
        "optimizer": f"Adam(lr={args.lr})",
        "scheduler": f"CosineAnnealingLR(T_max={args.epochs})",
        "batch": args.batch, "epochs": args.epochs, "seed": args.seed,
        "augment": args.augment, "device": str(device),
        "torch": torch.__version__,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    (args.out.parent / f"{args.out.stem}_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"train {len(train_set)} | val {len(val_set)} | 参数 {config['model']['params']:,} "
          f"| 设备 {device} | 输入 {width}x{height} | 增强 {'开' if args.augment else '关'}")

    history, started = [], time.time()
    last_path = args.out.with_name(f"{args.out.stem}_last.pt")

    for epoch in range(start_epoch, args.epochs + 1):
        model.train()
        losses, epoch_started = [], time.time()
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            loss = combined_loss(model(x), y)
            loss.backward()
            optimizer.step()
            losses.append(loss.item())
        scheduler.step()

        val_metrics, _ = evaluate(model, val_loader, device)
        seconds = time.time() - epoch_started
        history.append({"epoch": epoch, "loss": round(float(np.mean(losses)), 4),
                        "seconds": round(seconds, 1),
                        **{k: round(v, 4) for k, v in val_metrics.items()}})

        marker = ""
        if val_metrics["road_iou"] > best_iou:
            best_iou = val_metrics["road_iou"]
            torch.save({"model": model.state_dict(), "base": args.base,
                        "input_size": [width, height], "val_road_iou": best_iou,
                        "config": config}, args.out)
            marker = "  *保存最佳"
        torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(),
                    "scheduler": scheduler.state_dict(), "epoch": epoch,
                    "best_iou": best_iou, "base": args.base,
                    "input_size": [width, height], "config": config}, last_path)

        print(f"epoch {epoch:>3}/{args.epochs}  loss {np.mean(losses):.4f}  "
              f"RoadIoU {val_metrics['road_iou']:.4f}  mIoU {val_metrics['mean_iou']:.4f}  "
              f"{seconds:.0f}s{marker}")

    test_report = None
    if args.eval_test:
        try:
            test_set = SegDataset(args.dataset, "test", (width, height), False, args.seed)
        except FileNotFoundError:
            print("[warn] 没有 test 划分，跳过测试集评估")
        else:
            test_loader = DataLoader(test_set, batch_size=args.batch, shuffle=False)
            best = torch.load(args.out, map_location=device, weights_only=False)
            model.load_state_dict(best["model"])
            test_metrics, test_confusion = evaluate(model, test_loader, device)
            test_report = {"images": len(test_set), "metrics": test_metrics,
                           "confusion_matrix": test_confusion.tolist()}
            print("\n== 独立 Test 集 ==")
            for key, value in test_metrics.items():
                print(f"  {key:<16} {value:.4f}")

    report = {
        "config": config,
        "best_val_road_iou": round(best_iou, 4),
        "total_seconds": round(time.time() - started, 1),
        "history": history,
        "test": test_report,
        "weights": str(args.out),
    }
    (args.out.parent / f"{args.out.stem}_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n最佳验证 Road IoU {best_iou:.4f}  权重 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
