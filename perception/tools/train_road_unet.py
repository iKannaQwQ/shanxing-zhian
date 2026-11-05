#!/usr/bin/env python3
"""在沙盘数据上训练道路分割 U-Net。

用法:
    perception/.venv/bin/python perception/tools/train_road_unet.py \
        --dataset data/sandbox/dataset --epochs 40 --out perception/weights/road_unet.pt
"""
from __future__ import annotations

import argparse
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


class SegDataset(Dataset):
    def __init__(self, root: Path, split: str, size: tuple[int, int], augment: bool):
        self.image_dir = root / "images" / split
        self.mask_dir = root / "masks" / split
        self.size = size
        self.augment = augment
        self.items = sorted(p for p in self.image_dir.iterdir()
                            if p.suffix.lower() in IMAGE_EXT)
        if not self.items:
            raise FileNotFoundError(f"{self.image_dir} 下没有图像")

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int):
        image_path = self.items[index]
        image = cv2.imread(str(image_path))
        mask = cv2.imread(str(self.mask_dir / f"{image_path.stem}.png"), cv2.IMREAD_GRAYSCALE)
        if image is None or mask is None:
            raise ValueError(f"读取失败: {image_path.name}")

        image = cv2.resize(image, self.size, interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, self.size, interpolation=cv2.INTER_NEAREST)

        if self.augment:
            if random.random() < 0.5:
                image = cv2.flip(image, 1)
                mask = cv2.flip(mask, 1)
            if random.random() < 0.3:
                factor = random.uniform(0.8, 1.2)
                image = np.clip(image.astype(np.float32) * factor, 0, 255).astype(np.uint8)

        x = torch.from_numpy(image[:, :, ::-1].copy()).permute(2, 0, 1).float() / 255.0
        y = torch.from_numpy((mask > 127).astype(np.float32)).unsqueeze(0)
        return x, y


def iou_dice(prediction: torch.Tensor, target: torch.Tensor) -> tuple[float, float]:
    pred = (torch.sigmoid(prediction) >= 0.5).float()
    intersection = (pred * target).sum().item()
    union = pred.sum().item() + target.sum().item() - intersection
    iou = intersection / union if union > 0 else 1.0
    dice = (2 * intersection) / (pred.sum().item() + target.sum().item() + 1e-6)
    return iou, dice


def evaluate(model: UNet, loader: DataLoader, device: torch.device) -> tuple[float, float]:
    model.eval()
    ious, dices = [], []
    with torch.no_grad():
        for x, y in loader:
            x, y = x.to(device), y.to(device)
            logits = model(x)
            iou, dice = iou_dice(logits, y)
            ious.append(iou)
            dices.append(dice)
    return float(np.mean(ious)), float(np.mean(dices))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, default=Path("data/sandbox/dataset"))
    ap.add_argument("--out", type=Path, default=Path("perception/weights/road_unet.pt"))
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--base", type=int, default=32)
    ap.add_argument("--size", default="480x320")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    width, height = (int(v) for v in args.size.lower().split("x"))
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu") \
        if args.device == "auto" else torch.device(args.device)

    train_set = SegDataset(args.dataset, "train", (width, height), augment=True)
    val_set = SegDataset(args.dataset, "val", (width, height), augment=False)
    train_loader = DataLoader(train_set, batch_size=args.batch, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_set, batch_size=args.batch, shuffle=False, num_workers=0)

    model = UNet(base=args.base).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)

    print(f"训练集 {len(train_set)}  验证集 {len(val_set)}  参数 {model.parameter_count:,}  "
          f"设备 {device}  输入 {width}x{height}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    best_iou, history, started = 0.0, [], time.time()

    for epoch in range(1, args.epochs + 1):
        model.train()
        losses = []
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            loss = combined_loss(model(x), y)
            loss.backward()
            optimizer.step()
            losses.append(loss.item())
        scheduler.step()

        val_iou, val_dice = evaluate(model, val_loader, device)
        history.append({"epoch": epoch, "loss": round(float(np.mean(losses)), 4),
                        "val_iou": round(val_iou, 4), "val_dice": round(val_dice, 4)})
        marker = ""
        if val_iou > best_iou:
            best_iou = val_iou
            torch.save({"model": model.state_dict(), "base": args.base,
                        "input_size": [width, height], "val_iou": val_iou}, args.out)
            marker = "  *保存"
        print(f"epoch {epoch:>3}/{args.epochs}  loss {np.mean(losses):.4f}  "
              f"IoU {val_iou:.4f}  Dice {val_dice:.4f}{marker}")

    report = {
        "dataset": str(args.dataset),
        "train": len(train_set), "val": len(val_set),
        "epochs": args.epochs, "best_val_iou": round(best_iou, 4),
        "seconds": round(time.time() - started, 1),
        "weights": str(args.out), "history": history,
    }
    (args.out.parent / "road_unet_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n最佳验证 IoU {best_iou:.4f}  权重 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
