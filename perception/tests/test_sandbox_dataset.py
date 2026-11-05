"""沙盘数据集：标签/掩码校验与划分逻辑。"""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from perception.datasets.sandbox import (inspect, split, validate_mask,
                                         validate_yolo_labels)


def _write_label(path, lines):
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_valid_label_passes(tmp_path):
    label = _write_label(tmp_path / "a.txt", ["0 0.500000 0.500000 0.100000 0.200000"])
    _, issues = validate_yolo_labels(label, class_count=1)
    assert issues == []


def test_rejects_wrong_column_count(tmp_path):
    label = _write_label(tmp_path / "b.txt", ["0 0.5 0.5 0.1"])
    _, issues = validate_yolo_labels(label, class_count=1)
    assert issues and "5 列" in issues[0].message


def test_rejects_class_out_of_range(tmp_path):
    label = _write_label(tmp_path / "c.txt", ["3 0.5 0.5 0.1 0.1"])
    _, issues = validate_yolo_labels(label, class_count=1)
    assert issues and "类别" in issues[0].message


def test_rejects_coordinate_out_of_range(tmp_path):
    label = _write_label(tmp_path / "d.txt", ["0 1.400000 0.5 0.1 0.1"])
    _, issues = validate_yolo_labels(label, class_count=1)
    assert issues and "坐标越界" in issues[0].message


def test_rejects_non_numeric(tmp_path):
    label = _write_label(tmp_path / "e.txt", ["0 abc 0.5 0.1 0.1"])
    _, issues = validate_yolo_labels(label, class_count=1)
    assert issues and "非数值" in issues[0].message


def test_mask_size_mismatch(tmp_path):
    mask = np.zeros((100, 100), dtype=np.uint8)
    path = tmp_path / "m.png"
    cv2.imwrite(str(path), mask)
    issues = validate_mask(path, (200, 150))
    assert issues and "尺寸" in issues[0].message


def test_mask_must_be_binary(tmp_path):
    mask = np.full((60, 80), 77, dtype=np.uint8)
    path = tmp_path / "m2.png"
    cv2.imwrite(str(path), mask)
    issues = validate_mask(path, (60, 80))   # (高, 宽)
    assert issues and "取值" in issues[0].message


def test_mask_accepts_0_255(tmp_path):
    mask = np.zeros((60, 80), dtype=np.uint8)
    mask[10:40, 20:60] = 255
    path = tmp_path / "m3.png"
    cv2.imwrite(str(path), mask)
    assert validate_mask(path, (60, 80)) == []   # (高, 宽)


def _make_raw(root, count=6, with_rock_every=2):
    for sub in ("images", "masks", "labels"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for index in range(count):
        stem = f"f{index:03d}"
        image = np.zeros((120, 160, 3), dtype=np.uint8)
        mask = np.zeros((120, 160), dtype=np.uint8)
        mask[40:100, 50:110] = 255
        cv2.imwrite(str(root / "images" / f"{stem}.jpg"), image)
        cv2.imwrite(str(root / "masks" / f"{stem}.png"), mask)
        if index % with_rock_every == 0:
            (root / "labels" / f"{stem}.txt").write_text(
                "0 0.5 0.5 0.1 0.1", encoding="utf-8")
    return root


def test_inspect_counts(tmp_path):
    raw = _make_raw(tmp_path / "raw", count=6)
    report = inspect(raw)
    assert report.ok
    assert report.images == 6
    assert report.masks == 6
    assert report.with_rock == 3          # 0,2,4
    assert report.empty_labels == 3
    assert report.boxes == 3


def test_inspect_flags_missing_mask(tmp_path):
    raw = _make_raw(tmp_path / "raw2", count=2)
    (raw / "masks" / "f001.png").unlink()
    report = inspect(raw)
    assert not report.ok
    assert any("缺少对应掩码" in issue.message for issue in report.issues)


def test_split_is_stratified(tmp_path):
    raw = _make_raw(tmp_path / "raw3", count=10)
    out = tmp_path / "dataset"
    summary = split(raw, out, val_ratio=0.2, seed=1)

    assert summary["train"] + summary["val"] == 10
    assert summary["val"] >= 1
    # 验证集里必须至少有一帧带落石，否则 mAP 无从计算
    assert summary["val_with_rock"] >= 1
    assert (out / "data.yaml").exists()
    assert (out / "images" / "train").exists()
    assert (out / "masks" / "val").exists()


@pytest.mark.parametrize("ratio", [0.1, 0.3])
def test_split_ratios(tmp_path, ratio):
    raw = _make_raw(tmp_path / f"raw_{ratio}", count=20)
    summary = split(raw, tmp_path / f"out_{ratio}", val_ratio=ratio, seed=3)
    assert summary["val"] >= 1
    assert summary["train"] >= 1
