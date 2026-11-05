"""RGB 与深度的分辨率/坐标系对齐（M5）。

重要：深度图与 RGB 分辨率不同（iPhone 12 Pro 上约 256×192 vs 1280×960）。
**分辨率不同本身不是问题，问题在于"是否同源且共像素网格"。**
本模块显式区分两种情况：

  * `aligned_to_rgb=True`：深度与彩色来自同一相机模型，只是采样更稀疏。
    此时按比例缩放像素坐标是**合法的**，但必须校验宽高比一致。
  * `aligned_to_rgb=False`：两者的像素网格关系未知，任何缩放都只是猜测。
    此时返回 None，由上层输出 UNKNOWN —— 不做"简单 resize 就宣称对齐"。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .frame import DepthMap, Intrinsics


@dataclass
class AlignmentReport:
    ok: bool
    aspect_ok: bool
    scale_x: float
    scale_y: float
    reasons: list[str]

    def as_dict(self) -> dict:
        return {"ok": self.ok, "aspect_ok": self.aspect_ok,
                "scale_x": round(self.scale_x, 4), "scale_y": round(self.scale_y, 4),
                "reasons": self.reasons}


def check_alignment(rgb_shape: tuple[int, int], depth: DepthMap,
                    aspect_tolerance: float = 0.02) -> AlignmentReport:
    """校验"同网格、异分辨率"这一前提是否成立。

    rgb_shape: (h, w)
    """
    h, w = rgb_shape
    dh, dw = depth.shape
    sx = dw / float(w)
    sy = dh / float(h)
    aspect_rgb = w / float(h)
    aspect_depth = dw / float(dh)
    aspect_ok = abs(aspect_rgb - aspect_depth) / aspect_rgb <= aspect_tolerance

    reasons: list[str] = []
    if not depth.aligned_to_rgb:
        reasons.append("DEPTH_NOT_ALIGNED_TO_RGB: 采集端未声明深度与彩色同网格，"
                       "禁止假设缩放关系")
    if not aspect_ok:
        reasons.append(f"ASPECT_MISMATCH: RGB {w}x{h} 与深度 {dw}x{dh} 宽高比不一致，"
                       "仅缩放无法得到正确映射")
    return AlignmentReport(ok=depth.aligned_to_rgb and aspect_ok,
                           aspect_ok=aspect_ok, scale_x=sx, scale_y=sy, reasons=reasons)


def rgb_to_depth_px(x: float, y: float, rgb_shape: tuple[int, int],
                    depth: DepthMap, report: AlignmentReport | None = None
                    ) -> tuple[int, int] | None:
    """RGB 像素 -> 深度像素。对齐不可信时返回 None（不猜）。"""
    rep = report or check_alignment(rgb_shape, depth)
    if not rep.ok:
        return None
    h, w = rgb_shape
    dh, dw = depth.shape
    dx = int(np.floor(np.clip(x, 0, w - 1) * rep.scale_x))
    dy = int(np.floor(np.clip(y, 0, h - 1) * rep.scale_y))
    return int(np.clip(dx, 0, dw - 1)), int(np.clip(dy, 0, dh - 1))


def sample_patch(depth: DepthMap, x: int, y: int, win: int = 3
                 ) -> tuple[float, float, int]:
    """以 (x,y) 为中心的窗口内取有效深度中位数。

    返回 (median_m, valid_ratio, sample_count)。窗口内有效点不足时返回 nan。
    """
    h, w = depth.shape
    r = max(1, win // 2)
    y0, y1 = max(0, y - r), min(h, y + r + 1)
    x0, x1 = max(0, x - r), min(w, x + r + 1)
    patch = depth.meters[y0:y1, x0:x1]
    valid = depth.valid[y0:y1, x0:x1]
    n = int(valid.sum())
    if n == 0 or patch.size == 0:
        return float("nan"), 0.0, 0
    vals = patch[valid]
    return float(np.median(vals)), n / float(patch.size), n


def sample_confidence(conf: np.ndarray | None, depth: DepthMap, x: int, y: int,
                      win: int = 3) -> float | None:
    """confidenceMap 与深度同分辨率；无则为 None。"""
    if conf is None:
        return None
    c = np.asarray(conf)
    if c.shape != depth.shape:
        return None
    h, w = depth.shape
    r = max(1, win // 2)
    y0, y1 = max(0, y - r), min(h, y + r + 1)
    x0, x1 = max(0, x - r), min(w, x + r + 1)
    patch = c[y0:y1, x0:x1].astype(np.float32)
    if patch.size == 0:
        return None
    # 不同实现可能给 0-255 或 0-1，统一归一到 0-1
    scale = 255.0 if patch.max() > 1.0 else 1.0
    return float(patch.mean() / scale)
