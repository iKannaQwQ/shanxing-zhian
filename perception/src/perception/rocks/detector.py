"""落石检测器封装（M3 的模型侧）。

原版 YOLOP 没有 rockfall 类，因此落石检测是**独立模型**，与道路分割解耦：
  * 生产者可以是任意 YOLO 系列权重（本项目用 ultralytics 训练/推理）；
  * 未安装 ultralytics 时不影响道路与侵入模块的单元测试；
  * 检测结果一律带置信度，低于阈值的框在 `RockDetector.predict` 里被丢弃，
    不会以 0 置信度混进后续判定。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

from ..types import BBox, RockObservation


class DetectorBackend(Protocol):
    def predict(self, rgb_bgr: np.ndarray) -> list[RockObservation]: ...


class YoloRockDetector:
    """ultralytics YOLO 封装。"""

    def __init__(self, weights: str | Path, conf: float = 0.35, iou: float = 0.45,
                 device: str | None = None, imgsz: int = 640):
        try:
            from ultralytics import YOLO  # noqa: WPS433
        except ImportError as e:  # pragma: no cover
            raise ImportError("需要 ultralytics：pip install ultralytics") from e

        self.model = YOLO(str(weights))
        self.conf = conf
        self.iou = iou
        self.imgsz = imgsz
        self.device = device
        self.names = self.model.names

    def predict(self, rgb_bgr: np.ndarray) -> list[RockObservation]:
        if rgb_bgr is None or rgb_bgr.ndim != 3:
            raise ValueError("输入应为 HxWx3 BGR 图像")
        h, w = rgb_bgr.shape[:2]
        res = self.model.predict(rgb_bgr, conf=self.conf, iou=self.iou,
                                 imgsz=self.imgsz, device=self.device, verbose=False)
        out: list[RockObservation] = []
        for i, r in enumerate(res):
            boxes = getattr(r, "boxes", None)
            if boxes is None:
                continue
            for j in range(len(boxes)):
                xyxy = boxes.xyxy[j].tolist()
                score = float(boxes.conf[j])
                cls = int(boxes.cls[j])
                b = BBox(max(0.0, xyxy[0]), max(0.0, xyxy[1]),
                         min(float(w), xyxy[2]), min(float(h), xyxy[3]))
                truncated = (xyxy[0] <= 1 or xyxy[1] <= 1
                             or xyxy[2] >= w - 1 or xyxy[3] >= h - 1)
                out.append(RockObservation(rock_id=f"R{i}_{j}", bbox=b, score=score,
                                           label=str(self.names.get(cls, cls)),
                                           truncated=truncated))
        out.sort(key=lambda o: o.score, reverse=True)
        return out


class StaticDetector:
    """测试/回放用：返回预设检测框。"""

    def __init__(self, rocks: list[RockObservation]):
        self.rocks = rocks

    def predict(self, rgb_bgr: np.ndarray) -> list[RockObservation]:  # noqa: ARG002
        return list(self.rocks)
