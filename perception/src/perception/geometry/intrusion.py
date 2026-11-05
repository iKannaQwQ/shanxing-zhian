"""落石与道路的空间关系（M4）。

核心原则：**不用检测框中心点判定**。
用检测框的"接地区域"（框底部一带）与道路掩码求交，再计算道路剩余通行宽度。
中心点判定只作为对照字段 `in_road_by_center` 保留，用于说明两者差异。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..road.mask_ops import road_extent_at, to_binary_mask
from ..status import IntrusionLevel
from ..types import BBox, IntrusionAssessment, Reason, RoadObservation, RockObservation


@dataclass(frozen=True)
class IntrusionPolicy:
    """阈值集中管理，便于随沙盘实测调参。"""

    contact_band_ratio: float = 0.18   # 取检测框底部多少比例作为接地区域
    edge_overlap_max: float = 0.15     # ≤ 该值视为仅擦边
    none_overlap_max: float = 0.03     # ≤ 该值视为完全在路外
    min_pass_width_px: float = 40.0    # 可通过的最小宽度（像素，沙盘标定后改为米制）
    full_cover_tolerance_px: float = 3.0


def _clip_bbox(bbox: BBox, shape: tuple[int, int]) -> BBox:
    h, w = shape
    x1 = float(np.clip(bbox.x1, 0, w - 1))
    y1 = float(np.clip(bbox.y1, 0, h - 1))
    x2 = float(np.clip(bbox.x2, 1, w))
    y2 = float(np.clip(bbox.y2, 1, h))
    if x2 <= x1:
        x2 = min(w, x1 + 1)
    if y2 <= y1:
        y2 = min(h, y1 + 1)
    return BBox(x1, y1, x2, y2)


def contact_region(bbox: BBox, shape: tuple[int, int],
                   band_ratio: float = 0.18) -> BBox:
    """检测框底部的"接地"带状区域。

    检测框上半部分通常是空气/背景，直接整框求交会高估占路比例。
    """
    b = _clip_bbox(bbox, shape)
    band_h = max(1.0, b.height * band_ratio)
    return BBox(b.x1, max(b.y1, b.y2 - band_h), b.x2, b.y2)


def rect_mask(shape: tuple[int, int], bbox: BBox) -> np.ndarray:
    m = np.zeros(shape, dtype=np.uint8)
    b = _clip_bbox(bbox, shape)
    x1, y1 = int(np.floor(b.x1)), int(np.floor(b.y1))
    x2, y2 = int(np.ceil(b.x2)), int(np.ceil(b.y2))
    m[y1:y2, x1:x2] = 1
    return m


def overlap_ratio(a: np.ndarray, b: np.ndarray) -> float:
    """|a ∩ b| / |a|；a 为空时返回 0。"""
    a = to_binary_mask(a)
    b = to_binary_mask(b)
    denom = int(a.sum())
    if denom == 0:
        return 0.0
    return float(np.count_nonzero((a == 1) & (b == 1))) / denom


def _runs(row: np.ndarray) -> list[tuple[int, int]]:
    if not row.any():
        return []
    idx = np.flatnonzero(row)
    parts = np.split(idx, np.where(np.diff(idx) != 1)[0] + 1)
    return [(int(p[0]), int(p[-1]) + 1) for p in parts]


def free_corridor(road_row: np.ndarray, blocked_row: np.ndarray) -> list[tuple[int, int]]:
    """该行未被占用的可通行区间。"""
    free = (to_binary_mask(road_row) == 1) & (blocked_row == 0)
    return _runs(free.astype(np.uint8))


def widest_gap(road_row: np.ndarray, blocked_row: np.ndarray) -> float:
    runs = free_corridor(road_row, blocked_row)
    return float(max((e - s for s, e in runs), default=0))


def _remaining_width(road_mask: np.ndarray, blocked: np.ndarray,
                     y0: int, y1: int) -> tuple[float, int]:
    """在 [y0,y1) 行范围内，最窄行的最大可通行宽度（保守估计）。"""
    h = road_mask.shape[0]
    y0 = int(np.clip(y0, 0, h - 1))
    y1 = int(np.clip(y1, y0 + 1, h))
    widths = [widest_gap(road_mask[y], blocked[y]) for y in range(y0, y1)]
    widths = [w for w in widths if w > 0] or [0.0]
    worst_row = y0 + int(np.argmin(widths))
    return float(min(widths)), worst_row


def assess_intrusion(rock: RockObservation,
                     road: RoadObservation,
                     policy: IntrusionPolicy | None = None) -> IntrusionAssessment:
    """单块落石的侵入评估。"""
    policy = policy or IntrusionPolicy()

    if road.mask is None or not road.detected:
        return IntrusionAssessment(
            rock_id=rock.rock_id, level=IntrusionLevel.UNKNOWN,
            overlap_ratio=0.0, bbox_overlap_ratio=0.0,
            remaining_width_m=None, remaining_width_px=None,
            lateral_offset_norm=None, in_road_by_center=False,
            reasons=[Reason("ROAD_UNKNOWN", "道路掩码不可用，落石关系不可判定")],
        )

    mask = to_binary_mask(road.mask)
    shape = mask.shape
    contact = contact_region(rock.bbox, shape, policy.contact_band_ratio)
    cmask = rect_mask(shape, contact)
    bmask = rect_mask(shape, rock.bbox)

    ov = overlap_ratio(cmask, mask)
    bbox_ov = overlap_ratio(bmask, mask)
    reasons: list[Reason] = []

    cx, cy = rock.bbox.center
    cy_i = int(np.clip(cy, 0, shape[0] - 1))
    in_road_by_center = bool(mask[cy_i, int(np.clip(cx, 0, shape[1] - 1))])

    extent = road_extent_at(mask, contact.y2 - 1)
    lateral = None
    if extent is not None:
        xl, xr = extent
        w = max(1.0, float(xr - xl))
        lateral = float(np.clip(2.0 * (cx - (xl + xr) / 2.0) / w, -2.0, 2.0))

    # 整帧道路上让该落石"占用"的区域
    blocked = cmask.copy()
    rem_px, worst_row = _remaining_width(mask, blocked,
                                         int(contact.y1), int(np.ceil(contact.y2)))

    if ov <= policy.none_overlap_max:
        level = IntrusionLevel.NONE
        reasons.append(Reason("ROCK_OUTSIDE_ROAD", f"接地区域与道路重叠仅 {ov:.2f}"))
    elif ov <= policy.edge_overlap_max:
        level = IntrusionLevel.EDGE
        reasons.append(Reason("ROCK_EDGE", f"落石擦边，重叠 {ov:.2f}"))
    elif rem_px <= policy.min_pass_width_px:
        level = IntrusionLevel.FULL
        reasons.append(Reason("ROCK_FULL_BLOCK",
                              f"剩余通行宽度 {rem_px:.0f}px 小于下限 "
                              f"{policy.min_pass_width_px:.0f}px（第 {worst_row} 行）"))
    else:
        level = IntrusionLevel.PARTIAL
        reasons.append(Reason("ROCK_PARTIAL",
                              f"落石部分占路，重叠 {ov:.2f}，剩余 {rem_px:.0f}px"))

    if in_road_by_center != (level is not IntrusionLevel.NONE):
        reasons.append(Reason("CENTER_METHOD_DIFFERS",
                              "中心点判定与接地区域判定结论不同，以接地区域为准"))

    return IntrusionAssessment(
        rock_id=rock.rock_id, level=level, overlap_ratio=ov, bbox_overlap_ratio=bbox_ov,
        remaining_width_m=None, remaining_width_px=rem_px,
        lateral_offset_norm=lateral, in_road_by_center=in_road_by_center,
        reasons=reasons,
    )


def assess_multiple_intrusions(rocks: list[RockObservation],
                               road: RoadObservation,
                               policy: IntrusionPolicy | None = None
                               ) -> tuple[list[IntrusionAssessment], dict]:
    """多块落石：逐块评估 + 联合阻断评估。

    多块石头可能各自只占一部分，但合起来把路堵死——这必须单独算，
    不能只看单块结论。
    """
    policy = policy or IntrusionPolicy()
    assessments = [assess_intrusion(r, road, policy) for r in rocks]

    union_info = {"combined_remaining_width_px": None,
                  "combined_blocked": None,
                  "blocking_rock_ids": []}

    if road.mask is None or not road.detected or not rocks:
        return assessments, union_info

    mask = to_binary_mask(road.mask)
    shape = mask.shape
    blocked = np.zeros(shape, dtype=np.uint8)
    for r in rocks:
        contact = contact_region(r.bbox, shape, policy.contact_band_ratio)
        blocked |= rect_mask(shape, contact)

    ys = [int(contact_region(r.bbox, shape, policy.contact_band_ratio).y1) for r in rocks]
    ye = [int(np.ceil(contact_region(r.bbox, shape, policy.contact_band_ratio).y2)) for r in rocks]
    rem, worst = _remaining_width(mask, blocked, min(ys), max(ye))

    union_info.update({
        "combined_remaining_width_px": rem,
        "combined_blocked": bool(rem <= policy.min_pass_width_px),
        "worst_row": worst,
        "blocking_rock_ids": [a.rock_id for a in assessments
                              if a.level in (IntrusionLevel.FULL, IntrusionLevel.PARTIAL)],
    })
    return assessments, union_info
