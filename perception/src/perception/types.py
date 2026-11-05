"""感知模块内部数据模型。

约定：
  * 像素坐标一律以 RGB 图像为基准（左上原点）。
  * 米制字段在缺少有效深度时为 None，绝不以 0 代替。
  * 每个结论都携带 reasons，便于追溯与前端展示。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .status import DepthSource, IntrusionLevel, RoadMaskStatus, SystemStatus


@dataclass(frozen=True)
class BBox:
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self) -> None:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError(f"非法 bbox: {self.as_list()}")

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    def clip(self, w: int, h: int) -> "BBox":
        return BBox(max(0.0, min(self.x1, w - 1)), max(0.0, min(self.y1, h - 1)),
                    max(1.0, min(self.x2, w)), max(1.0, min(self.y2, h)))

    def as_list(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]


@dataclass
class Reason:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass
class RockObservation:
    """RGB 检测器输出（尚未关联深度）。"""

    rock_id: str
    bbox: BBox
    score: float
    label: str = "rock"
    # 检测框是否被图像边界截断（落石只露出一部分）
    truncated: bool = False


@dataclass
class RoadObservation:
    """道路分割与其几何摘要。"""

    status: RoadMaskStatus
    mask: np.ndarray | None = None
    coverage_ratio: float = 0.0
    confidence: float | None = None
    polygon: list[tuple[float, float]] = field(default_factory=list)
    centerline: list[tuple[float, float]] = field(default_factory=list)
    width_profile: list[dict[str, float]] = field(default_factory=list)
    component_count: int = 0
    hole_ratio: float = 0.0
    reasons: list[Reason] = field(default_factory=list)

    @property
    def detected(self) -> bool:
        return self.status is RoadMaskStatus.OK and self.mask is not None


@dataclass
class DepthEstimate:
    """单个落石的深度估计结果。"""

    distance_m: float | None = None
    source: DepthSource = DepthSource.NONE
    valid_ratio: float = 0.0
    sample_count: int = 0
    reason: str = ""
    point_xyz_m: tuple[float, float, float] | None = None


@dataclass
class IntrusionAssessment:
    """落石与道路的几何关系（M4 核心产物）。"""

    rock_id: str
    level: IntrusionLevel
    overlap_ratio: float                 # 接地区域被道路覆盖的比例
    bbox_overlap_ratio: float            # 整框与道路的重叠比例（参考值）
    remaining_width_m: float | None
    remaining_width_px: float | None
    lateral_offset_norm: float | None    # -1=最左, 0=路中, +1=最右
    in_road_by_center: bool              # 仅用于和"中心点法"做对照
    reasons: list[Reason] = field(default_factory=list)


@dataclass
class FrameReport:
    """一帧的完整输出（M7 统一 JSON）。"""

    frame_id: str
    timestamp_ns: int
    system_status: SystemStatus
    road_detected: bool
    road_mask_status: RoadMaskStatus
    rockfalls: list[dict[str, Any]] = field(default_factory=list)
    road: dict[str, Any] = field(default_factory=dict)
    depth: dict[str, Any] = field(default_factory=dict)
    reasons: list[Reason] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    source: str = "LIVE"
    schema_version: str = "1.0"
