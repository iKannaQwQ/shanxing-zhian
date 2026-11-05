"""深度数据模型与解码。

iPhone 侧提供 uint16 毫米或 float32 米两种编码；无效值必须显式识别，
禁止把 0 当作"距离很近"。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Intrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    ref_width: int
    ref_height: int

    def scaled_to(self, width: int, height: int) -> "Intrinsics":
        """ARKit 的内参对应一个参考分辨率，必须按目标分辨率换算。"""
        sx = width / float(self.ref_width)
        sy = height / float(self.ref_height)
        return Intrinsics(self.fx * sx, self.fy * sy, self.cx * sx, self.cy * sy,
                          width, height)

    def as_dict(self) -> dict:
        return {"fx": self.fx, "fy": self.fy, "cx": self.cx, "cy": self.cy,
                "width": self.ref_width, "height": self.ref_height}


@dataclass
class DepthMap:
    meters: np.ndarray          # float32，无效处为 np.nan
    valid: np.ndarray           # bool
    source: str = "arkit.sceneDepth"
    aligned_to_rgb: bool = False
    scale: float = 1.0

    @property
    def shape(self) -> tuple[int, int]:
        return self.meters.shape  # (h, w)

    @property
    def valid_ratio(self) -> float:
        return float(self.valid.mean()) if self.valid.size else 0.0


def decode_depth(raw: np.ndarray,
                 encoding: str = "uint16_mm",
                 scale_factor: float | None = None,
                 invalid_value: float = 0.0,
                 aligned_to_rgb: bool = False,
                 source: str = "arkit.sceneDepth") -> DepthMap:
    """把原始深度数组解码成米制 + 有效性掩码。

    有效性判据：有限值、>0、非 invalid_value。
    """
    arr = np.asarray(raw)
    if arr.ndim != 2:
        raise ValueError(f"深度图应为二维，得到 {arr.shape}")

    if encoding == "uint16_mm":
        sf = 0.001 if scale_factor is None else float(scale_factor)
    elif encoding == "float32_m":
        sf = 1.0 if scale_factor is None else float(scale_factor)
    else:
        raise ValueError(f"未知深度编码: {encoding}")

    meters = arr.astype(np.float32) * sf
    valid = np.isfinite(meters) & (meters > 0.0)
    if invalid_value:
        valid &= ~np.isclose(meters, float(invalid_value) * sf)
    meters = np.where(valid, meters, np.nan).astype(np.float32)

    return DepthMap(meters=meters, valid=valid, source=source,
                    aligned_to_rgb=aligned_to_rgb, scale=sf)
