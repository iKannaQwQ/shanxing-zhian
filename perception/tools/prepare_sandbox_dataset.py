#!/usr/bin/env python3
"""校验并划分沙盘数据集。

用法:
    # 只体检，不改动任何文件
    perception/.venv/bin/python perception/tools/prepare_sandbox_dataset.py --raw data/sandbox/raw

    # 体检通过后划分训练/验证集
    perception/.venv/bin/python perception/tools/prepare_sandbox_dataset.py \
        --raw data/sandbox/raw --out data/sandbox/dataset --val-ratio 0.2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perception.datasets.sandbox import inspect, split  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path, default=Path("data/sandbox/raw"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--val-ratio", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if not args.raw.exists():
        print(f"原始目录不存在: {args.raw}")
        print("目录约定见 docs/09-sandbox-pipeline.md")
        return 1

    report = inspect(args.raw)
    print(report.summary())

    if not report.ok:
        print("\n体检未通过，先修数据再划分。")
        return 2

    if args.out is None:
        print("\n体检通过（未划分）。加 --out 生成训练集。")
        return 0

    summary = split(args.raw, args.out, args.val_ratio, args.seed)
    print(f"\n已划分 -> {args.out}")
    print(f"  训练 {summary['train']}  验证 {summary['val']}"
          f"（验证集中含落石 {summary['val_with_rock']} 帧）")
    print(f"  data.yaml / split.json 已写出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
