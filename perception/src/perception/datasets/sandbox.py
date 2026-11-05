"""沙盘数据集：目录约定、校验与划分。

这里不假设数据已经存在——它定义"数据应该长什么样"，
并提供纯函数校验，让采集/标注阶段就能发现格式问题，
而不是等到训练跑了一半才报错。

目录约定：

    <raw>/
      images/*.jpg        采集图像
      masks/*.png         道路掩码（单通道，0=非道路，>0=可行驶）
      labels/*.txt        YOLO 检测标签（可缺省：没有落石的帧不需要文件）

    <out>/
      images/{train,val}
      masks/{train,val}
      labels/{train,val}
      data.yaml
      split.json
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXT = (".jpg", ".jpeg", ".png")


@dataclass
class LabelIssue:
    path: str
    line: int
    message: str


@dataclass
class DatasetReport:
    images: int = 0
    labels: int = 0
    masks: int = 0
    with_rock: int = 0
    empty_labels: int = 0
    boxes: int = 0
    issues: list[LabelIssue] = field(default_factory=list)
    sizes: dict[tuple[int, int], int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.issues

    def summary(self) -> str:
        lines = [
            f"图像 {self.images}  掩码 {self.masks}  标签 {self.labels}"
            f"（含框的 {self.with_rock}，空标签 {self.empty_labels}）",
            f"检测框总数 {self.boxes}",
        ]
        if self.sizes:
            sizes = ", ".join(f"{w}x{h}×{n}" for (w, h), n in
                              sorted(self.sizes.items(), key=lambda kv: -kv[1])[:4])
            lines.append(f"尺寸分布 {sizes}")
        if self.issues:
            lines.append(f"发现 {len(self.issues)} 个问题：")
            for issue in self.issues[:10]:
                lines.append(f"  {issue.path}:{issue.line} {issue.message}")
            if len(self.issues) > 10:
                lines.append(f"  …还有 {len(self.issues) - 10} 条")
        return "\n".join(lines)


def list_images(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir()
                  if p.suffix.lower() in IMAGE_EXT and p.is_file()) if directory.exists() else []


def validate_yolo_labels(label_path: Path, class_count: int = 1) -> tuple[list[str], list[LabelIssue]]:
    """校验单个 YOLO 标签文件。返回（错误列表, 结构化问题）。"""
    issues: list[LabelIssue] = []
    if not label_path.exists():
        return issues, issues

    for index, raw in enumerate(label_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            issues.append(LabelIssue(str(label_path), index, f"应为 5 列，实际 {len(parts)}"))
            continue
        try:
            cls = int(float(parts[0]))
            cx, cy, w, h = (float(v) for v in parts[1:])
        except ValueError:
            issues.append(LabelIssue(str(label_path), index, "存在非数值字段"))
            continue
        if not (0 <= cls < class_count):
            issues.append(LabelIssue(str(label_path), index, f"类别 {cls} 超出范围 [0,{class_count})"))
        if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
            issues.append(LabelIssue(str(label_path), index,
                                     f"坐标越界 cx={cx:.3f} cy={cy:.3f} w={w:.3f} h={h:.3f}"))
    return [], issues


def validate_mask(mask_path: Path, image_size: tuple[int, int]) -> list[LabelIssue]:
    """掩码必须是单通道、与图像同尺寸、二值（允许 0/255 或 0/1）。

    image_size 的顺序是 **(高, 宽)**，与 numpy 一致；调用方最容易在这里传反。
    """
    issues: list[LabelIssue] = []
    mask = cv2.imread(str(mask_path), cv2.IMREAD_UNCHANGED)
    if mask is None:
        return [LabelIssue(str(mask_path), 0, "无法读取")]
    if mask.ndim != 2:
        issues.append(LabelIssue(str(mask_path), 0, f"应为单通道，实际 {mask.shape}"))
        return issues
    height, width = image_size
    if (mask.shape[1], mask.shape[0]) != (width, height):
        issues.append(LabelIssue(str(mask_path), 0,
                                 f"尺寸 {mask.shape[1]}x{mask.shape[0]} 与图像 {width}x{height} 不符"))
    unique = np.unique(mask)
    if not set(unique.tolist()).issubset({0, 1, 255}):
        issues.append(LabelIssue(str(mask_path), 0,
                                 f"取值应为 0/1/255，实际出现 {unique[:6].tolist()}"))
    return issues


def inspect(raw: Path, class_count: int = 1) -> DatasetReport:
    """对整个原始目录做体检，不改动任何文件。"""
    report = DatasetReport()

    images_dir = raw / "images"
    labels_dir = raw / "labels"
    masks_dir = raw / "masks"

    images = list_images(images_dir)
    report.images = len(images)
    if not images:
        report.issues.append(LabelIssue(str(images_dir), 0, "没有找到任何图像"))

    for image_path in images:
        image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
        if image is None:
            report.issues.append(LabelIssue(str(image_path), 0, "无法读取图像"))
            continue
        height, width = image.shape[:2]
        key = (width, height)
        report.sizes[key] = report.sizes.get(key, 0) + 1

        mask_path = masks_dir / f"{image_path.stem}.png"
        if mask_path.exists():
            report.masks += 1
            report.issues.extend(validate_mask(mask_path, (height, width)))
        else:
            report.issues.append(LabelIssue(str(mask_path), 0, "缺少对应掩码"))

        label_path = labels_dir / f"{image_path.stem}.txt"
        if label_path.exists():
            report.labels += 1
            _, issues = validate_yolo_labels(label_path, class_count)
            report.issues.extend(issues)
            content = [ln for ln in label_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if content:
                report.with_rock += 1
                report.boxes += len(content)
            else:
                report.empty_labels += 1
        else:
            # 没有标签文件等价于"这一帧没有落石"，是合法的
            report.empty_labels += 1

    return report


def split(raw: Path, out: Path, val_ratio: float = 0.2, seed: int = 0) -> dict:
    """按 stem 划分训练/验证集，拷贝文件并生成 data.yaml。

    分层策略：含落石的帧与空帧分别按比例抽样，避免验证集里一块落石都没有。
    """
    images = list_images(raw / "images")
    if not images:
        raise ValueError(f"{raw}/images 下没有图像")

    with_rock, without_rock = [], []
    for image_path in images:
        label_path = raw / "labels" / f"{image_path.stem}.txt"
        has_rock = label_path.exists() and label_path.stat().st_size > 0
        (with_rock if has_rock else without_rock).append(image_path)

    rng = random.Random(seed)

    def take(pool: list[Path], ratio: float) -> list[Path]:
        shuffled = pool[:]
        rng.shuffle(shuffled)
        count = max(1, round(len(shuffled) * ratio)) if len(shuffled) > 1 else 0
        return shuffled[:count]

    val = take(with_rock, val_ratio) + take(without_rock, val_ratio)
    val_stems = {p.stem for p in val}
    train = [p for p in images if p.stem not in val_stems]

    layout: dict[str, list[str]] = {"train": [], "val": []}
    for split_name, pool in (("train", train), ("val", val)):
        for sub in ("images", "labels", "masks"):
            (out / sub / split_name).mkdir(parents=True, exist_ok=True)
        for image_path in pool:
            stem = image_path.stem
            target_image = out / "images" / split_name / image_path.name
            target_image.write_bytes(image_path.read_bytes())
            layout[split_name].append(stem)

            mask_src = raw / "masks" / f"{stem}.png"
            if mask_src.exists():
                (out / "masks" / split_name / f"{stem}.png").write_bytes(mask_src.read_bytes())

            label_src = raw / "labels" / f"{stem}.txt"
            if label_src.exists():
                (out / "labels" / split_name / f"{stem}.txt").write_bytes(label_src.read_bytes())

    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "nc: 1\n"
        "names: ['rock']\n", encoding="utf-8")

    import json
    summary = {
        "total": len(images),
        "train": len(train),
        "val": len(val),
        "with_rock": len(with_rock),
        "val_with_rock": len([p for p in val if p.stem in {q.stem for q in with_rock}]),
        "seed": seed,
        "val_ratio": val_ratio,
    }
    (out / "split.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    return summary
