"""测试公共夹具：合成道路掩码与落石框。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

H, W = 480, 640


def make_road_mask(h: int = H, w: int = W, top_w: int = 60, bot_w: int = 300,
                   y0: int = 200, y1: int = 480) -> np.ndarray:
    """上窄下宽的梯形道路（模拟透视）。"""
    m = np.zeros((h, w), dtype=np.uint8)
    for y in range(y0, y1):
        t = (y - y0) / max(1, (y1 - y0))
        half = (top_w + (bot_w - top_w) * t) / 2.0
        cx = w / 2.0
        xl = int(round(cx - half))
        xr = int(round(cx + half))
        m[y, max(0, xl):min(w, xr)] = 1
    return m


def road_bounds_at(mask: np.ndarray, y: int) -> tuple[int, int]:
    row = np.flatnonzero(mask[y])
    return int(row[0]), int(row[-1]) + 1


@pytest.fixture
def road_mask() -> np.ndarray:
    return make_road_mask()
