"""基于沙盘微调 U-Net 的道路分割器。

与 `YolopRoadSegmenter` 保持相同的 `predict(rgb, rotation_cw)` 接口，
后端只需改一行配置就能切换（见 docs/09-sandbox-pipeline.md）。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .unet import UNet


class UNetRoadSegmenter:
    def __init__(self, weights: str | Path, device: str = "auto",
                 input_size: tuple[int, int] | None = None, threshold: float = 0.5):
        import torch

        self.torch = torch
        self.device = self._resolve(device)
        self.rotation_cw = 0
        self.threshold = threshold
        self.weights = Path(weights)

        checkpoint = torch.load(str(weights), map_location=self.device, weights_only=False)
        base = int(checkpoint.get("base", 32))
        self.model = UNet(base=base)
        state = checkpoint["model"] if isinstance(checkpoint, dict) else checkpoint
        self.model.load_state_dict(state)
        self.model = self.model.to(self.device).eval()

        # 输入尺寸必须与训练时一致：优先用权重里记录的，其次用显式传入，最后才兜底
        trained_size = checkpoint.get("input_size") if isinstance(checkpoint, dict) else None
        if trained_size:
            self.input_size = (int(trained_size[0]), int(trained_size[1]))
            if input_size and tuple(input_size) != self.input_size:
                print(f"[unet] 忽略传入的 input_size={input_size}，"
                      f"使用权重记录的 {self.input_size}", flush=True)
        elif input_size:
            self.input_size = (int(input_size[0]), int(input_size[1]))
        else:
            self.input_size = (512, 512)
            print("[unet] 权重未记录 input_size，回退 512x512", flush=True)

        config = checkpoint.get("config", {}) if isinstance(checkpoint, dict) else {}
        self.model_info = {
            "road_segmenter": self.weights.stem,
            "input_size": [self.input_size[0], self.input_size[1]],
            "params": self.model.parameter_count,
            "device": str(self.device),
        }
        if isinstance(config.get("dataset"), dict):
            self.model_info["dataset_digest"] = config["dataset"].get("digest")

    def _resolve(self, name: str):
        import torch
        if name != "auto":
            return torch.device(name)
        if torch.cuda.is_available():
            return torch.device("cuda:0")
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    def predict(self, rgb_bgr: np.ndarray, rotation_cw: int = 0
                ) -> tuple[np.ndarray, float]:
        if rgb_bgr is None or rgb_bgr.ndim != 3:
            raise ValueError("输入应为 HxWx3 BGR 图像")

        rotation = rotation_cw or getattr(self, "rotation_cw", 0)
        work = rgb_bgr
        if rotation == 90:
            work = cv2.rotate(rgb_bgr, cv2.ROTATE_90_CLOCKWISE)
        elif rotation == 180:
            work = cv2.rotate(rgb_bgr, cv2.ROTATE_180)
        elif rotation == 270:
            work = cv2.rotate(rgb_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)

        height, width = work.shape[:2]
        resized = cv2.resize(work, self.input_size, interpolation=cv2.INTER_AREA)
        tensor = self.torch.from_numpy(resized.astype(np.float32) / 255.0)
        tensor = tensor.permute(2, 0, 1).unsqueeze(0).to(self.device)

        with self.torch.no_grad():
            logits = self.model(tensor)
            probability = self.torch.sigmoid(logits)

        road_probability = probability[0, 0].float().cpu().numpy()
        road_probability = cv2.resize(road_probability, (width, height),
                                      interpolation=cv2.INTER_LINEAR)
        mask = (road_probability >= self.threshold).astype(np.uint8)
        confidence = float(road_probability[mask == 1].mean()) if mask.any() else 0.0

        if rotation == 90:
            mask = cv2.rotate(mask, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif rotation == 180:
            mask = cv2.rotate(mask, cv2.ROTATE_180)
        elif rotation == 270:
            mask = cv2.rotate(mask, cv2.ROTATE_90_CLOCKWISE)

        return mask, confidence
