"""状态与枚举定义。

设计原则：任何"不知道"都必须有独立枚举值，不能用 False / 0 冒充。
"""
from __future__ import annotations

from enum import Enum


class RoadMaskStatus(str, Enum):
    """道路掩码的质量状态。"""

    OK = "OK"
    EMPTY = "EMPTY"                    # 分割结果为空
    TOO_SMALL = "TOO_SMALL"            # 面积占比过低，多半是视野偏离
    FRAGMENTED = "FRAGMENTED"          # 碎片化，连通域过多
    LOW_CONFIDENCE = "LOW_CONFIDENCE"  # 模型置信度不足
    FAILED = "FAILED"                  # 推理异常


class IntrusionLevel(str, Enum):
    """落石对道路的几何侵入等级（纯几何，不含通行判定）。"""

    NONE = "NONE"
    EDGE = "EDGE"
    PARTIAL = "PARTIAL"
    FULL = "FULL"
    UNKNOWN = "UNKNOWN"


class SystemStatus(str, Enum):
    """单帧处理链路的整体状态。"""

    OK = "OK"
    DEGRADED = "DEGRADED"
    NO_ROAD = "NO_ROAD"
    NO_DEPTH = "NO_DEPTH"
    INPUT_ERROR = "INPUT_ERROR"
    TIMEOUT = "TIMEOUT"
    DISCONNECTED = "DISCONNECTED"


class DepthSource(str, Enum):
    NONE = "NONE"
    LIDAR = "LIDAR"
    MONOCULAR = "MONOCULAR"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
