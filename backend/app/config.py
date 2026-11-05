"""服务端配置。

所有可调项都从环境变量读取，方便 Windows 部署时改而不改代码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    host: str = _env("ROAD_HOST", "0.0.0.0")
    port: int = int(_env("ROAD_PORT", "8000"))

    db_path: Path = field(
        default_factory=lambda: Path(_env("ROAD_DB", str(ROOT / "data" / "db" / "mountain.db"))))

    # 内存队列容量：满了丢最旧（新帧更有价值）
    queue_capacity: int = int(_env("ROAD_QUEUE_CAPACITY", "32"))

    # 保留窗口：超期数据自动清理
    retention_hours: float = float(_env("ROAD_RETENTION_HOURS", "24"))
    cleanup_interval_s: float = float(_env("ROAD_CLEANUP_INTERVAL", "300"))

    # 推理
    device: str = _env("ROAD_DEVICE", "auto")
    rock_weights: str | None = os.environ.get("ROAD_ROCK_WEIGHTS") or None
    # 道路分割模型: "yolop"（默认）或 U-Net 权重路径（沙盘微调后使用）
    road_model: str = _env("ROAD_ROAD_MODEL", "yolop")
    simulate_rock: str = _env("ROAD_SIMULATE_ROCK", "none")

    # 是否把原始帧落盘（默认关闭：10fps 会产生 4MB/s）
    save_frames: bool = _env("ROAD_SAVE_FRAMES", "0") == "1"
    frames_dir: Path = field(
        default_factory=lambda: Path(_env("ROAD_FRAMES_DIR", str(ROOT / "data" / "incoming"))))
