#!/usr/bin/env python3
"""契约校验器：结构校验(JSON Schema) + 语义安全不变量(策略)。

用法:
    python contracts/validate.py --all
    python contracts/validate.py samples/perception_result/sample_sim_blocked.json
退出码: 0 全部通过, 1 存在失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # pragma: no cover
    sys.exit("缺少 jsonschema，请先运行: bash scripts/bootstrap_mac.sh")

ROOT = Path(__file__).resolve().parent
SCHEMAS = ROOT / "schemas"
SAMPLES = ROOT / "samples"

SCHEMA_BY_MSG = {
    "frame_bundle": "frame_bundle.schema.json",
    "perception_result": "perception_result.schema.json",
    "risk_assessment": "risk_assessment.schema.json",
}

# ---- 策略阈值（与 docs/02-risk-model.md 保持一致；改动需同步）----
POLICY = {
    "passable_min_depth_valid_ratio": 0.50,
    "passable_min_observed_area_ratio": 0.60,
    "passable_min_road_status": "OK",
}
STATUS_ORDER = ["PASSABLE", "RESTRICTED", "UNKNOWN", "BLOCKED"]  # 越靠后越不利


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def schema_errors(doc: dict, schema: dict) -> list[str]:
    v = Draft202012Validator(schema, format_checker=FormatChecker())
    out = []
    for e in sorted(v.iter_errors(doc), key=lambda x: list(x.absolute_path)):
        loc = "/".join(str(p) for p in e.absolute_path) or "<root>"
        out.append(f"[schema] {loc}: {e.message}")
    return out


# ---- 语义不变量 ----

def check_perception(doc: dict) -> list[str]:
    errs: list[str] = []
    rocks = {r["rock_id"]: r for r in doc.get("rocks", [])}

    # P1: 无深度支撑的落石不得生成米制侵入量
    for it in doc.get("intrusions", []):
        rid = it["rock_id"]
        rock = rocks.get(rid)
        if rock is None:
            errs.append(f"[rule P1] intrusion 引用了不存在的 rock_id={rid}")
            continue
        if rock["depth_source"] == "NONE":
            errs.append(f"[rule P1] rock {rid} 无深度来源却出现在 intrusions 中（禁止臆造距离）")

    # P2: depth_source=NONE 时不得携带任何米制字段
    for rid, rock in rocks.items():
        if rock["depth_source"] == "NONE":
            for f in ("height_m", "footprint_m2", "distance_m", "base_point_m"):
                if rock.get(f) is not None:
                    errs.append(f"[rule P2] rock {rid} depth_source=NONE 却给出了 {f}")

    # P3: 分割失败时不应宣称高覆盖率
    road = doc["road"]
    if road["status"] == "FAILED" and road["coverage_ratio"] > 0.30:
        errs.append("[rule P3] road.status=FAILED 但 coverage_ratio 仍偏高，分割结果自相矛盾")

    # P4: 深度有效率不足时必须显式降级
    q = doc["quality"]
    if q["depth_valid_ratio"] < POLICY["passable_min_depth_valid_ratio"]:
        if "LOW_DEPTH_VALIDITY" not in q["degraded_reasons"]:
            errs.append("[rule P4] depth_valid_ratio 低于阈值却未标记 LOW_DEPTH_VALIDITY")

    # P5: 配准失败时必须显式降级
    if not q["depth_aligned_ok"] and "DEPTH_ALIGN_FAILED" not in q["degraded_reasons"]:
        errs.append("[rule P5] depth_aligned_ok=false 却未标记 DEPTH_ALIGN_FAILED")

    return errs


def check_risk(doc: dict) -> list[str]:
    errs: list[str] = []
    dirs = doc["per_direction"]
    codes = {r["code"] for r in doc["reasons"]} | {
        r["code"] for d in dirs for r in d["reasons"]
    }

    # R1: overall_status 必须等于各方向中最不利者
    if dirs:
        worst = max((d["status"] for d in dirs), key=STATUS_ORDER.index)
        if doc["overall_status"] not in ("DEGRADED",) and doc["overall_status"] != worst:
            errs.append(
                f"[rule R1] overall_status={doc['overall_status']} 与最不利方向 {worst} 不一致"
            )

    # R2: PASSABLE 的证据门槛（fail-closed 核心）
    echo = doc.get("inputs_echo") or {}
    for d in dirs:
        if d["status"] != "PASSABLE":
            continue
        if echo.get("road_status") != POLICY["passable_min_road_status"]:
            errs.append(f"[rule R2] {d['direction']} 判 PASSABLE 但 road_status 非 OK")
        if echo.get("depth_valid_ratio", 0) < POLICY["passable_min_depth_valid_ratio"]:
            errs.append(f"[rule R2] {d['direction']} 判 PASSABLE 但深度有效率不足")
        if echo.get("observed_area_ratio", 0) < POLICY["passable_min_observed_area_ratio"]:
            errs.append(f"[rule R2] {d['direction']} 判 PASSABLE 但观测覆盖率不足")
        if not d["reasons"]:
            errs.append(f"[rule R2] {d['direction']} 判 PASSABLE 却没有任何支撑理由")
        if "NO_ROCK_DETECTED_FULL_OBSERVATION" not in {r["code"] for r in d["reasons"]}:
            if "REMAINING_WIDTH_SUFFICIENT" not in {r["code"] for r in d["reasons"]}:
                errs.append(f"[rule R2] {d['direction']} 判 PASSABLE 但缺少明确支撑码")

    # R3: 替代通行 available=true 的前置条件
    ap = doc["alternative_passage"]
    if ap["available"] is True:
        if not ap["verifiable"]:
            errs.append("[rule R3] alternative_passage.available=true 但 verifiable=false")
        if ap["oncoming"] in ("UNVERIFIABLE", "UNKNOWN"):
            errs.append("[rule R3] 对向交通不可确认，却宣称替代通行可用")
        if ap["min_gap_m"] is None:
            errs.append("[rule R3] available=true 但未给出 min_gap_m")

    # R4: BLOCKED 必须给出阻断理由
    for d in dirs:
        if d["status"] == "BLOCKED":
            dcs = {r["code"] for r in d["reasons"]}
            if not dcs & {"BLOCKED_BY_ROCK", "FULL_ROCK_INTRUSION"}:
                errs.append(f"[rule R4] {d['direction']} 判 BLOCKED 但缺少阻断类理由码")

    # R5: 任一方向 UNKNOWN 时不得出现全局 PASSABLE 语义
    if doc["overall_status"] == "PASSABLE" and any(d["status"] == "UNKNOWN" for d in dirs):
        errs.append("[rule R5] 存在 UNKNOWN 方向却给出整体 PASSABLE")

    if not codes:
        errs.append("[rule R6] reasons 全空：判定缺乏可追溯依据")

    return errs


def check_cross(perception: dict, risk: dict) -> list[str]:
    errs: list[str] = []
    echo = risk.get("inputs_echo") or {}
    if echo.get("road_status") and echo["road_status"] != perception["road"]["status"]:
        errs.append("[cross C1] inputs_echo.road_status 与感知结果不一致")
    known = {r["rock_id"] for r in perception["rocks"]}
    for d in risk["per_direction"]:
        for rid in d.get("blocking_rock_ids", []):
            if rid not in known:
                errs.append(f"[cross C2] {d['direction']} 引用了感知结果中不存在的 rock_id={rid}")
    if echo.get("rock_count") is not None and echo["rock_count"] != len(perception["rocks"]):
        errs.append("[cross C3] inputs_echo.rock_count 与感知结果不一致")
    return errs


def validate_file(path: Path, cache: dict[str, dict]) -> list[str]:
    try:
        doc = load_json(path)
    except json.JSONDecodeError as e:
        return [f"[parse] JSON 解析失败: {e}"]

    mt = doc.get("msg_type")
    schema_name = SCHEMA_BY_MSG.get(mt)
    if schema_name is None:
        return [f"[schema] 未知 msg_type={mt!r}"]
    if schema_name not in cache:
        cache[schema_name] = load_json(SCHEMAS / schema_name)

    errs = schema_errors(doc, cache[schema_name])
    if errs:
        return errs  # 结构不合法时不做语义检查，避免噪音

    if mt == "perception_result":
        errs += check_perception(doc)
    elif mt == "risk_assessment":
        errs += check_risk(doc)
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="契约结构与语义校验")
    ap.add_argument("files", nargs="*", type=Path, help="待校验文件")
    ap.add_argument("--all", action="store_true", help="校验 samples/ 下全部合法样例")
    ap.add_argument("--expect-fail", action="store_true", help="断言这些文件应当校验失败")
    args = ap.parse_args()

    targets = list(args.files)
    if args.all or not targets:
        targets = sorted(SAMPLES.rglob("*.json"))
        targets = [p for p in targets if "invalid" not in p.parts]

    cache: dict[str, dict] = {}
    results: dict[Path, list[str]] = {}

    for p in targets:
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        results[p] = validate_file(p, cache)

    cross_errs: list[str] = []
    by_frame: dict[str, list[dict]] = {}
    for p, errs in results.items():
        if errs:
            continue
        d = load_json(p)
        if "frame_id" in d:
            by_frame.setdefault(d["frame_id"], []).append(d)
    for fid, group in by_frame.items():
        percs = [g for g in group if g["msg_type"] == "perception_result"]
        risks = [g for g in group if g["msg_type"] == "risk_assessment"]
        for perc in percs:
            for risk in risks:
                cross_errs += [f"{fid[:8]}: {e}" for e in check_cross(perc, risk)]

    failed = [p for p, e in results.items() if e]
    ok = not failed and not cross_errs

    if args.expect_fail:
        if ok:
            print(f"✗ 期望失败但全部通过（{len(targets)} 个文件）")
            return 1
        for p in failed:
            print(f"✓ 按预期被拒绝: {p.name}")
            for e in results[p]:
                print(f"    - {e}")
        return 0

    for p in sorted(results):
        mark = "✓" if not results[p] else "✗"
        print(f"{mark} {p.relative_to(ROOT.parent) if p.is_relative_to(ROOT.parent) else p}")
        for e in results[p]:
            print(f"    - {e}")
    for e in cross_errs:
        print(f"✗ [cross] {e}")

    print(f"\n{'通过' if ok else '失败'}：{len(targets) - len(failed)}/{len(targets)} 文件合法，"
          f"{len(cross_errs)} 个跨文件问题")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
