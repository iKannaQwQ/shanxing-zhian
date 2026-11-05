"""M5 RGB-D 关联合成测试：分辨率差异、对齐可信度、无效深度、量程。"""
from __future__ import annotations

import pytest
from pathlib import Path

import numpy as np

from perception.depth.align import (check_alignment, rgb_to_depth_px, sample_patch)
from perception.depth.estimator import (DepthPolicy, depth_quality, estimate_rock_depth,
                                        px_to_meters)
from perception.depth.frame import Intrinsics, decode_depth
from perception.status import DepthSource
from perception.types import BBox

RGB_SHAPE = (480, 640)
DEPTH_SHAPE = (192, 256)          # iPhone LiDAR 常见比例


def _depth(value_m: float = 0.8, aligned: bool = True):
    raw = np.full(DEPTH_SHAPE, value_m, dtype=np.float32)
    return decode_depth(raw, encoding="float32_m", aligned_to_rgb=aligned)


def _intr() -> Intrinsics:
    return Intrinsics(fx=1000.0, fy=1000.0, cx=320.0, cy=240.0,
                      ref_width=640, ref_height=480)


def test_decode_uint16_mm_and_invalid():
    raw = np.array([[0, 1000], [2000, 0]], dtype=np.uint16)
    d = decode_depth(raw, encoding="uint16_mm", invalid_value=0)
    assert np.isnan(d.meters[0, 0]) and np.isnan(d.meters[1, 1])
    assert abs(d.meters[0, 1] - 1.0) < 1e-6
    assert abs(d.meters[1, 0] - 2.0) < 1e-6
    assert d.valid_ratio == 0.5


def test_zero_depth_is_not_near_zero_distance():
    """0 必须变成无效，而不是 0 米。"""
    raw = np.zeros((4, 4), dtype=np.uint16)
    d = decode_depth(raw, encoding="uint16_mm")
    assert not d.valid.any()
    assert np.isnan(d.meters).all()


def test_alignment_ok_for_same_aspect():
    d = _depth()
    rep = check_alignment(RGB_SHAPE, d)
    assert rep.ok is True
    assert abs(rep.scale_x - 0.4) < 1e-6


def test_alignment_rejects_unverified_grid():
    d = _depth(aligned=False)
    rep = check_alignment(RGB_SHAPE, d)
    assert rep.ok is False
    assert rgb_to_depth_px(320, 240, RGB_SHAPE, d) is None
    assert any("DEPTH_NOT_ALIGNED" in r for r in rep.reasons)


def test_alignment_rejects_aspect_mismatch():
    raw = np.full((100, 400), 1.0, dtype=np.float32)   # 4:1，明显不匹配 4:3
    d = decode_depth(raw, encoding="float32_m", aligned_to_rgb=True)
    rep = check_alignment(RGB_SHAPE, d)
    assert rep.ok is False
    assert any("ASPECT_MISMATCH" in r for r in rep.reasons)


def test_pixel_mapping_scales_correctly():
    d = _depth()
    px = rgb_to_depth_px(320, 240, RGB_SHAPE, d)
    assert px == (128, 96)


def test_sample_patch_uses_median():
    d = _depth()
    d.meters[10:13, 10:13] = np.array([[1.0, 1.0, 1.0],
                                       [1.0, 9.0, 1.0],
                                       [1.0, 1.0, 1.0]], dtype=np.float32)
    val, ratio, n = sample_patch(d, 11, 11, win=3)
    assert abs(val - 1.0) < 1e-6          # 中位数抗单点离群
    assert n == 9 and ratio == 1.0


def test_estimate_normal():
    d = _depth(0.8)
    est = estimate_rock_depth(BBox(200, 350, 330, 430), RGB_SHAPE, d, _intr())
    assert est.source is DepthSource.LIDAR
    assert est.distance_m is not None and abs(est.distance_m - 0.8) < 1e-6
    assert est.point_xyz_m is not None
    assert abs(est.point_xyz_m[2] - 0.8) < 1e-6


def test_estimate_missing_depth():
    est = estimate_rock_depth(BBox(200, 350, 330, 430), RGB_SHAPE, None, _intr())
    assert est.source is DepthSource.NONE
    assert est.distance_m is None
    assert est.reason == "NO_DEPTH_DATA"


def test_estimate_sparse_depth_preserves_uncertainty():
    raw = np.zeros(DEPTH_SHAPE, dtype=np.float32)
    raw[171, 106] = 0.8                      # 窗口内仅 1/9 有效
    d = decode_depth(raw, encoding="float32_m", aligned_to_rgb=True)
    est = estimate_rock_depth(BBox(200, 350, 330, 430), RGB_SHAPE, d, _intr())
    assert est.source is DepthSource.NONE
    assert est.distance_m is None
    assert "DEPTH_SPARSE" in est.reason


def test_estimate_out_of_range_rejected():
    d = _depth(6.0)
    est = estimate_rock_depth(BBox(200, 350, 330, 430), RGB_SHAPE, d, _intr())
    assert est.source is DepthSource.NONE
    assert "OUT_OF_LIDAR_RANGE" in est.reason


def test_estimate_without_intrinsics_keeps_distance_but_no_point():
    d = _depth(0.8)
    est = estimate_rock_depth(BBox(200, 350, 330, 430), RGB_SHAPE, d, None)
    assert est.distance_m is not None
    assert est.point_xyz_m is None


def test_confidence_gate():
    d = _depth(0.8)
    conf = np.zeros(DEPTH_SHAPE, dtype=np.uint8)
    est = estimate_rock_depth(BBox(200, 350, 330, 430), RGB_SHAPE, d, _intr(), conf)
    assert est.source is DepthSource.NONE
    assert "LOW_CONFIDENCE" in est.reason


def test_px_to_meters():
    assert abs(px_to_meters(100, 1.0, _intr()) - 0.1) < 1e-9
    assert np.isnan(px_to_meters(100, float("nan"), _intr()))


def test_depth_quality_reasons():
    ok, reasons = depth_quality(_depth(0.8), None)
    assert ok is True and reasons == []
    ok2, r2 = depth_quality(None, None)
    assert ok2 is False and "NO_DEPTH_DATA" in r2


def test_confidence_arkit_levels_accepted():
    """ARKit 置信度是 0/1/2 三档，高置信度必须能通过门槛。"""
    d = _depth(0.8)
    high = np.full(DEPTH_SHAPE, 2, dtype=np.uint8)
    est = estimate_rock_depth(BBox(200, 350, 330, 430), RGB_SHAPE, d, _intr(), high)
    assert est.source is DepthSource.LIDAR

    medium = np.full(DEPTH_SHAPE, 1, dtype=np.uint8)
    est2 = estimate_rock_depth(BBox(200, 350, 330, 430), RGB_SHAPE, d, _intr(), medium)
    assert est2.source is DepthSource.LIDAR


def test_confidence_low_level_rejected():
    d = _depth(0.8)
    low = np.zeros(DEPTH_SHAPE, dtype=np.uint8)
    est = estimate_rock_depth(BBox(200, 350, 330, 430), RGB_SHAPE, d, _intr(), low)
    assert est.source is DepthSource.NONE


# ---------- 真机数据回归（fixtures/device_frame_0011）----------

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "device_frame_0011"


@pytest.mark.skipif(not FIXTURE.exists(), reason="缺少真机样本")
def test_real_device_metadata_matches_contract():
    import json
    from jsonschema import Draft202012Validator, FormatChecker

    schema_path = Path(__file__).resolve().parents[2] / "contracts" / "schemas" / \
        "frame_bundle.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    meta = json.loads((FIXTURE / "metadata.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(meta))
    assert not errors, [e.message for e in errors]
    assert meta["intrinsics"]["reference_width"] == meta["capture"]["width"]


@pytest.mark.skipif(not FIXTURE.exists(), reason="缺少真机样本")
def test_real_device_depth_decodes_as_meters():
    import json
    meta = json.loads((FIXTURE / "metadata.json").read_text(encoding="utf-8"))
    d = meta["depth"]
    raw = np.fromfile(FIXTURE / "depth.bin", dtype=np.float32).reshape(d["height"], d["width"])
    valid = raw[np.isfinite(raw) & (raw > 0)]
    assert valid.size > 0
    # 该帧为近距物体：0.06~0.45 m
    assert 0.03 < valid.min() < 0.15
    assert 0.3 < valid.max() < 1.0


@pytest.mark.skipif(not FIXTURE.exists(), reason="缺少真机样本")
def test_real_device_confidence_is_ar_levels():
    """真机置信度必须是 0/1/2；若出现 0-255 说明采集端改了语义。"""
    raw = np.fromfile(FIXTURE / "confidence.bin", dtype=np.uint8)
    unique = set(np.unique(raw).tolist())
    assert unique.issubset({0, 1, 2}), f"非预期取值: {sorted(unique)[:10]}"


@pytest.mark.skipif(not FIXTURE.exists(), reason="缺少真机样本")
def test_real_device_confidence_tracks_depth_range():
    """近于 0.2 m 的像素置信度应显著偏低——这是 LiDAR 有效量程的经验证据。"""
    import json
    meta = json.loads((FIXTURE / "metadata.json").read_text(encoding="utf-8"))
    d = meta["depth"]
    depth = np.fromfile(FIXTURE / "depth.bin", dtype=np.float32).reshape(d["height"], d["width"])
    conf = np.fromfile(FIXTURE / "confidence.bin", dtype=np.uint8).reshape(d["height"], d["width"])

    near = depth < 0.15
    far = depth > 0.40
    assert near.sum() > 100 and far.sum() > 100
    assert (conf[near] == 0).mean() > 0.9      # 近处几乎全是低置信度
    assert (conf[far] == 2).mean() > 0.9       # 远处几乎全是高置信度
