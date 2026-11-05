"""M4 落石-道路空间关系：五类场景 + 多石联合阻断 + 中心点对照。"""
from __future__ import annotations

import numpy as np

from perception.geometry.intrusion import (IntrusionPolicy, assess_intrusion,
                                           assess_multiple_intrusions,
                                           contact_region, overlap_ratio,
                                           rect_mask)
from perception.road.mask_ops import assess_road
from perception.status import IntrusionLevel
from perception.types import BBox, RoadObservation, RockObservation


def _rock(x1, y1, x2, y2, rid="R1"):
    return RockObservation(rock_id=rid, bbox=BBox(x1, y1, x2, y2), score=0.9)


def _road(mask):
    return assess_road(mask)


def test_fully_outside_road_is_none(road_mask):
    road = _road(road_mask)
    r = _rock(100, 350, 180, 430)
    a = assess_intrusion(r, road)
    assert a.level is IntrusionLevel.NONE
    assert a.overlap_ratio == 0.0


def test_edge_touch_is_edge(road_mask):
    road = _road(road_mask)
    # 接触带在 y≈415..430，该处道路左边界约 x=196
    r = _rock(170, 350, 197, 430)
    a = assess_intrusion(r, road)
    assert 0.03 < a.overlap_ratio <= 0.15
    assert a.level is IntrusionLevel.EDGE


def test_partial_intrusion(road_mask):
    road = _road(road_mask)
    r = _rock(200, 350, 330, 430)
    a = assess_intrusion(r, road)
    assert a.level is IntrusionLevel.PARTIAL
    assert a.remaining_width_px is not None and a.remaining_width_px > 40


def test_full_block(road_mask):
    road = _road(road_mask)
    r = _rock(150, 350, 470, 430)
    a = assess_intrusion(r, road)
    assert a.level is IntrusionLevel.FULL
    assert a.remaining_width_px is not None and a.remaining_width_px <= 40


def test_multiple_rocks_joint_block(road_mask):
    """两块各占一半，单看都只是 PARTIAL，合起来把路堵死。"""
    road = _road(road_mask)
    a = _rock(196, 350, 320, 430, "A")
    b = _rock(320, 350, 444, 430, "B")
    assessments, union = assess_multiple_intrusions([a, b], road)

    assert all(x.level is IntrusionLevel.PARTIAL for x in assessments)
    assert union["combined_blocked"] is True
    assert union["combined_remaining_width_px"] <= 40
    assert set(union["blocking_rock_ids"]) == {"A", "B"}


def test_contact_region_not_center_point(road_mask):
    """框整体与道路有重叠，但接地区域在路外——必须判 NONE。"""
    m = road_mask.copy()
    m[350:, :] = 0                      # 道路在 y=350 处截断
    road = _road(m)
    r = _rock(250, 330, 420, 430)       # 框上半段压在路上，接地部分已出路面
    a = assess_intrusion(r, road)
    assert a.bbox_overlap_ratio > 0.15        # 整框仍有约 19% 压在路面上
    assert a.level is IntrusionLevel.NONE      # 但接地部分已出路面


def test_center_method_disagrees_recorded(road_mask):
    road = _road(road_mask)
    r = _rock(100, 250, 203, 430)       # 中心在路外，接地边缘擦到路面
    a = assess_intrusion(r, road)
    assert a.in_road_by_center is False
    assert a.level is not IntrusionLevel.NONE
    assert any(x.code == "CENTER_METHOD_DIFFERS" for x in a.reasons)


def test_unknown_road_yields_unknown():
    road = RoadObservation(status=_road(np.zeros((480, 640), np.uint8)).status)
    a = assess_intrusion(_rock(200, 350, 330, 430), road)
    assert a.level is IntrusionLevel.UNKNOWN
    assert a.overlap_ratio == 0.0


def test_lateral_offset_sign(road_mask):
    road = _road(road_mask)
    left = assess_intrusion(_rock(205, 350, 280, 430), road)
    right = assess_intrusion(_rock(360, 350, 435, 430), road)
    assert left.lateral_offset_norm is not None and right.lateral_offset_norm is not None
    assert left.lateral_offset_norm < 0 < right.lateral_offset_norm


def test_contact_region_geometry():
    b = BBox(10, 100, 50, 200)
    c = contact_region(b, (480, 640), band_ratio=0.18)
    assert c.x1 == 10 and c.x2 == 50
    assert abs(c.y2 - 200) < 1e-6
    assert abs((c.y2 - c.y1) - 18) < 1e-6


def test_overlap_ratio_semantics():
    a = np.zeros((10, 10), np.uint8)
    a[0:4, 0:4] = 1
    b = np.zeros((10, 10), np.uint8)
    b[0:4, 0:2] = 1
    assert overlap_ratio(a, b) == 0.5
    assert overlap_ratio(np.zeros((10, 10), np.uint8), b) == 0.0
