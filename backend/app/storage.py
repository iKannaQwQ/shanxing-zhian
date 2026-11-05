"""原始帧的文件存储。

数据库只存路径与元数据；图像/深度/置信度落盘。
这样数据库保持小而快，也避免把几百 KB 的二进制塞进行里。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, frame_id: str) -> Path:
        return self.root / frame_id

    def save(self, frame_id: str, metadata: dict[str, Any], rgb: bytes,
             depth: bytes, confidence: bytes | None) -> dict[str, str]:
        """写入四个文件，返回绝对路径。已存在则覆盖（同一 frame_id 重复上传是幂等的）。"""
        target = self.directory(frame_id)
        target.mkdir(parents=True, exist_ok=True)

        meta_path = target / "metadata.json"
        rgb_path = target / "rgb.jpg"
        depth_path = target / "depth.bin"
        conf_path = target / "confidence.bin"

        meta_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        rgb_path.write_bytes(rgb)
        depth_path.write_bytes(depth)
        if confidence is not None:
            conf_path.write_bytes(confidence)
        elif conf_path.exists():
            conf_path.unlink()

        return {
            "artifact_dir": str(target),
            "rgb_path": str(rgb_path),
            "depth_path": str(depth_path),
            "confidence_path": str(conf_path) if confidence is not None else "",
        }

    def delete(self, frame_id: str) -> None:
        shutil.rmtree(self.directory(frame_id), ignore_errors=True)

    def disk_usage(self) -> dict[str, int]:
        total, count = 0, 0
        for path in self.root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
                count += 1
        return {"files": count, "bytes": total}

    def prune_missing(self, keep: set[str]) -> int:
        """删除不在 keep 集合里的目录，用于保留窗口清理。"""
        removed = 0
        for child in self.root.iterdir():
            if child.is_dir() and child.name not in keep:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed
