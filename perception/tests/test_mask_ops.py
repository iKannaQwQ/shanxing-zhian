"""M2 道路掩码几何：状态判定与形态度量。"""
from __future__ import annotations

import numpy as np

from perception.road.mask_ops import (assess_road, component_stats, hole_ratio,
                                      road_extent_at, to_binary_mask,
                                      width_profile)
from perception.status import RoadMaskStatus


def test_binary_mask_from_probabilities():
    prob = np.zeros((4, 4), dtype=np.float32)
    prob[1:3, 1:3] = 0.9
    m = to_binary_mask(prob, threshold=0.5)
    assert m.dtype == np.uint8
    assert m.sum() == 4


def test_healthy_road_is_ok(road_mask):
    obs = assess_road(road_mask, confidence=0.9)
    assert obs.status is RoadMaskStatus.OK
    assert obs.detected is True
    assert 0.05 < obs.coverage_ratio < 0.5
    assert len(obs.polygon) >= 4
    assert len(obs.width_profile) > 4


def test_width_profile_increases_downwards(road_mask):
    prof = width_profile(road_mask, bands=8)
    widths = [p["width_px"] for p in prof if p["width_px"] > 0]
    assert len(widths) >= 4
    # 透视下越靠下越宽
    assert widths[-1] > widths[0] * 1.5


def test_centerline_follows_road(road_mask):
    obs = assess_road(road_mask)
    assert obs.centerline
    xs = [x for x, _ in obs.centerline]
    assert all(abs(x - 320) < 2 for x in xs)


def test_empty_mask_is_not_ok():
    obs = assess_road(np.zeros((480, 640), dtype=np.uint8))
    assert obs.status is RoadMaskStatus.EMPTY
    assert obs.detected is False
    assert any(r.code == "ROAD_MASK_EMPTY" for r in obs.reasons)


def test_none_mask_is_failed():
    obs = assess_road(None)
    assert obs.status is RoadMaskStatus.FAILED
    assert obs.detected is False


def test_tiny_mask_too_small():
    m = np.zeros((480, 640), dtype=np.uint8)
    m[300:310, 300:310] = 1
    obs = assess_road(m, min_coverage=0.02)
    assert obs.status is RoadMaskStatus.TOO_SMALL


def test_low_confidence_downgrades():
    m = np.zeros((480, 640), dtype=np.uint8)
    m[200:400, 250:390] = 1
    obs = assess_road(m, confidence=0.2, min_confidence=0.5)
    assert obs.status is RoadMaskStatus.LOW_CONFIDENCE


def test_fragmented_mask():
    m = np.zeros((480, 640), dtype=np.uint8)
    for i in range(6):
        m[100 + i * 50: 140 + i * 50, 100 + i * 40: 180 + i * 40] = 1
    n, areas = component_stats(m)
    assert n >= 6
    obs = assess_road(m, max_components=4)
    assert obs.status is RoadMaskStatus.FRAGMENTED


def test_hole_ratio_detects_occlusion(road_mask):
    holed = road_mask.copy()
    holed[320:360, 290:350] = 0          # 一块落石在路面上"挖走"的区域
    assert hole_ratio(holed) > 0.005
    obs = assess_road(holed)
    assert obs.hole_ratio > 0.005


def test_road_extent_at_row(road_mask):
    y = 460
    xl, xr = road_extent_at(road_mask, y)
    assert xr - xl > 200
    assert abs((xl + xr) / 2 - 320) < 3


def test_curved_road_supported():
    """U 形弯：同一行出现两段，取最长段而不是整体外接框。"""
    m = np.zeros((480, 640), dtype=np.uint8)
    m[100:480, 60:200] = 1
    m[400:480, 60:520] = 1
    obs = assess_road(m)
    assert obs.status is RoadMaskStatus.OK
    prof = width_profile(m, bands=16)
    assert any(p["width_px"] > 0 for p in prof)
    # 上部应只识别到左侧支路宽度，而不是整幅宽
    top = [p for p in prof if p["y"] < 200 and p["width_px"] > 0]
    assert top and all(p["width_px"] <= 150 for p in top)


# ---------- 设备朝向 → 旋转 ----------

def test_orientation_rotation_mapping():
    from perception.road.orientation import inverse, rotation_cw

    assert rotation_cw("landscape_right") == 0
    assert rotation_cw("landscape_left") == 180
    assert rotation_cw("portrait") == 90
    assert rotation_cw("portrait_upside_down") == 270
    assert rotation_cw(None) == 0
    assert rotation_cw("unknown_value") == 0


def test_orientation_inverse():
    from perception.road.orientation import inverse, rotation_cw

    for name in ("landscape_right", "landscape_left", "portrait", "portrait_upside_down"):
        r = rotation_cw(name)
        assert (r + inverse(r)) % 360 == 0


def test_portrait_needs_rotation():
    """竖持时必须旋转，否则道路横躺。"""
    from perception.road.orientation import rotation_cw

    assert rotation_cw("portrait") != 0


# ---------- 不确定性（域外）闸门 ----------

def _road_mask():
    import numpy as np
    mask = np.zeros((120, 160), dtype=np.uint8)
    mask[40:110, 55:105] = 1
    return mask


def test_high_uncertainty_rejects_even_with_large_coverage():
    """域外画面可能覆盖率很高——不确定性必须优先拦截。"""
    from perception.road.mask_ops import RoadMaskStatus, assess_road

    road = assess_road(_road_mask(), uncertainty=0.35, max_uncertainty=0.20)
    assert road.status is RoadMaskStatus.LOW_CONFIDENCE
    assert any(r.code == "ROAD_HIGH_UNCERTAINTY" for r in road.reasons)


def test_low_uncertainty_keeps_ok():
    from perception.road.mask_ops import RoadMaskStatus, assess_road

    road = assess_road(_road_mask(), uncertainty=0.08, max_uncertainty=0.20)
    assert road.status is RoadMaskStatus.OK


def test_uncertainty_gate_is_optional():
    """YOLOP 等没有该信号的模型不受影响。"""
    from perception.road.mask_ops import RoadMaskStatus, assess_road

    road = assess_road(_road_mask(), uncertainty=None, max_uncertainty=0.0)
    assert road.status is RoadMaskStatus.OK


def test_uncertainty_reason_is_traceable():
    from perception.road.mask_ops import assess_road

    road = assess_road(_road_mask(), uncertainty=0.9, max_uncertainty=0.2)
    message = next(r.message for r in road.reasons if r.code == "ROAD_HIGH_UNCERTAINTY")
    assert "0.900" in message and "0.200" in message
