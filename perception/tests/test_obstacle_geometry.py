"""几何障碍检测的单元测试（纯合成数据，不依赖模型与真实采集）。"""
from __future__ import annotations

import numpy as np
import pytest

from perception.obstacles.geometry import (detect_obstacles, fit_ground_plane,
                                           to_observations)

H, W, FX = 192, 256, 260.0


def make_depth(bump_mm: float = 0.0, noise_mm: float = 2.0, seed: int = 0,
               bump_center=(160, 90), bump_radius: int = 8) -> np.ndarray:
    ys, xs = np.mgrid[0:H, 0:W]
    base = 0.64 + 0.02 * (xs / W - 0.5) + 0.01 * (ys / H - 0.5)
    depth = (base + np.random.default_rng(seed).normal(0, noise_mm / 1000.0, (H, W)))
    if bump_mm:
        cx, cy = bump_center
        depth[((xs - cx) ** 2 + (ys - cy) ** 2) < bump_radius ** 2] -= bump_mm / 1000.0
    return depth.astype(np.float32)


def detect(depth):
    return detect_obstacles(depth, FX, FX, W / 2, H / 2)


def test_flat_ground_has_no_obstacles():
    result = detect(make_depth(0))
    assert result.reason == "OK"
    assert result.protrusions == []


def test_detects_20mm_protrusion_with_correct_bbox():
    result = detect(make_depth(20))
    assert len(result.protrusions) == 1
    box = result.protrusions[0].bbox
    # 合成凸起中心 (160, 90)、半径 8
    assert box[0] <= 160 <= box[2]
    assert box[1] <= 90 <= box[3]
    assert 10 <= (box[2] - box[0]) <= 24


def test_measures_height_accurately():
    result = detect(make_depth(20))
    mean_mm = result.protrusions[0].mean_height_m * 1000
    assert 17 <= mean_mm <= 23, f"测得 {mean_mm:.1f} mm"


def test_detects_10mm_but_not_5mm():
    """10 mm 应可检出；5 mm 低于最小高度，不报。"""
    assert len(detect(make_depth(10)).protrusions) == 1
    assert len(detect(make_depth(5)).protrusions) == 0


def test_noise_alone_does_not_trigger():
    """只有噪声、没有凸起时不得误报（多种种子）。"""
    for seed in range(5):
        assert detect(make_depth(0, seed=seed)).protrusions == []


def test_sparse_depth_fails_closed():
    depth = np.zeros((H, W), dtype=np.float32)
    depth[::8, ::8] = 0.64          # 有效像素约 1.5%，远低于阈值
    result = detect(depth)
    assert result.plane is None
    assert result.reason == "DEPTH_TOO_SPARSE"


def test_observation_is_category_agnostic():
    observations = to_observations(detect(make_depth(20)))
    assert len(observations) == 1
    obs = observations[0]
    assert obs.rock_id == "GEO-1"
    assert obs.label == "obstacle:geometric"
    assert 0.0 <= obs.score <= 1.0


def test_bbox_scales_to_image_resolution():
    result = detect(make_depth(20))
    scaled = to_observations(result, image_scale=5.0)[0]
    raw = result.protrusions[0].bbox
    assert scaled.bbox[0] == pytest.approx(raw[0] * 5.0)
    assert scaled.bbox[2] == pytest.approx(raw[2] * 5.0)


def test_plane_sigma_matches_injected_noise():
    plane, reason = fit_ground_plane(make_depth(0, noise_mm=2.0), FX, FX, W / 2, H / 2)
    assert reason == "OK"
    assert 1.4 <= plane.sigma_m * 1000 <= 2.6


def test_runtime_is_reasonable():
    """单帧应在 200 ms 内完成（实测约 40 ms）。"""
    import time
    depth = make_depth(20)
    detect(depth)  # warmup
    started = time.perf_counter()
    detect(depth)
    assert (time.perf_counter() - started) < 0.2
