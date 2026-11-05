"""M7 统一 JSON：schema 合规 + 状态裁决 + 不出现"安全"字样。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from perception.depth.frame import Intrinsics, decode_depth
from perception.pipeline import FrameInput, PerceptionPipeline, PipelineConfig
from perception.types import BBox, RockObservation

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts" / "schemas" /
                     "perception_report.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())

H, W = 480, 640


def make_road(h=H, w=W, y0=200, y1=480) -> np.ndarray:
    m = np.zeros((h, w), dtype=np.uint8)
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0)
        half = (60 + 240 * t) / 2
        m[y, int(w / 2 - half):int(w / 2 + half)] = 1
    return m


def make_depth(value=0.8, aligned=True):
    raw = np.full((192, 256), value, dtype=np.float32)
    return decode_depth(raw, encoding="float32_m", aligned_to_rgb=aligned)


def intr() -> Intrinsics:
    return Intrinsics(1000.0, 1000.0, 320.0, 240.0, 640, 480)


def assert_valid(doc: dict) -> None:
    errs = sorted(VALIDATOR.iter_errors(doc), key=lambda e: list(e.absolute_path))
    assert not errs, "\n".join(f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
                               for e in errs)


@pytest.fixture
def pipe() -> PerceptionPipeline:
    return PerceptionPipeline()


def test_road_only_no_depth(pipe):
    out = pipe.process(FrameInput(frame_id="f1", timestamp_ns=1_700_000_000_000_000_000,
                                  road_mask=make_road()))
    assert_valid(out)
    assert out["road_detected"] is True
    assert out["road_mask_status"] == "OK"
    assert out["system_status"] == "NO_DEPTH"      # 无深度 -> 不能声称完全正常
    assert out["depth"]["usable"] is False
    assert out["metrics"]["max_risk_level"] == "NONE"


def test_full_pipeline_with_depth(pipe):
    rock = RockObservation("R1", BBox(200, 350, 330, 430), 0.88)
    out = pipe.process(FrameInput(
        frame_id="f2", timestamp_ns=1_700_000_000_000_000_000,
        rgb=np.zeros((H, W, 3), np.uint8), road_mask=make_road(),
        depth=make_depth(), intrinsics=intr(), rocks=[rock]))
    assert_valid(out)
    assert out["system_status"] == "OK"
    r = out["rockfalls"][0]
    assert r["intrusion"] == "PARTIAL"
    assert r["evidence"] == "RGB_D"
    assert r["depth_estimate"]["source"] == "LIDAR"
    assert r["remaining_width_m"] is not None and r["remaining_width_m"] > 0


def test_no_road_detected(pipe):
    out = pipe.process(FrameInput(frame_id="f3", timestamp_ns=1,
                                  road_mask=np.zeros((H, W), np.uint8)))
    assert_valid(out)
    assert out["road_detected"] is False
    assert out["road_mask_status"] == "EMPTY"
    assert out["system_status"] == "NO_ROAD"
    assert out["metrics"]["max_risk_level"] == "UNKNOWN"


def test_no_rocks_still_not_claiming_safe(pipe):
    out = pipe.process(FrameInput(frame_id="f4", timestamp_ns=1,
                                  road_mask=make_road(), depth=make_depth(),
                                  intrinsics=intr()))
    assert_valid(out)
    codes = {r["code"] for r in out["reasons"]}
    assert "NO_ROCK_DETECTED" in codes
    assert out["metrics"]["max_risk_level"] == "NONE"
    assert "SAFE" not in json.dumps(out, ensure_ascii=False)


def test_missing_input_is_error(pipe):
    out = pipe.process(FrameInput(frame_id="f5", timestamp_ns=1))
    assert_valid(out)
    assert out["system_status"] == "INPUT_ERROR"


def test_disconnected_report(pipe):
    out = pipe.disconnected("f6", 1_700_000_000_000_000_000)
    assert_valid(out)
    assert out["system_status"] == "DISCONNECTED"
    assert out["metrics"]["max_risk_level"] == "UNKNOWN"


def test_timeout_flag(pipe):
    p = PerceptionPipeline(config=PipelineConfig(timeout_ms=0.0))
    out = p.process(FrameInput(frame_id="f7", timestamp_ns=1,
                               road_mask=make_road(), depth=make_depth(),
                               intrinsics=intr()))
    assert_valid(out)
    assert out["system_status"] == "TIMEOUT"


def test_unaligned_depth_degrades(pipe):
    rock = RockObservation("R1", BBox(200, 350, 330, 430), 0.9)
    out = pipe.process(FrameInput(
        frame_id="f8", timestamp_ns=1, rgb=np.zeros((H, W, 3), np.uint8),
        road_mask=make_road(), depth=make_depth(aligned=False),
        intrinsics=intr(), rocks=[rock]))
    assert_valid(out)
    assert out["system_status"] == "NO_DEPTH"
    assert out["rockfalls"][0]["depth_estimate"]["source"] == "NONE"
    assert out["rockfalls"][0]["evidence"] == "RGB_ONLY"
    assert out["rockfalls"][0]["remaining_width_m"] is None


def test_multiple_rocks_joint_blocking_in_report(pipe):
    a = RockObservation("A", BBox(196, 350, 320, 430), 0.9)
    b = RockObservation("B", BBox(320, 350, 444, 430), 0.8)
    out = pipe.process(FrameInput(
        frame_id="f9", timestamp_ns=1, rgb=np.zeros((H, W, 3), np.uint8),
        road_mask=make_road(), depth=make_depth(), intrinsics=intr(), rocks=[a, b]))
    assert_valid(out)
    assert out["metrics"]["combined_blocked"] is True
    assert out["metrics"]["max_risk_level"] == "PARTIAL"   # 单块仍是部分侵入
    assert len(out["rockfalls"]) == 2


def test_risk_level_basis_always_declared(pipe):
    rock = RockObservation("R1", BBox(150, 350, 470, 430), 0.95)
    out = pipe.process(FrameInput(
        frame_id="f10", timestamp_ns=1, rgb=np.zeros((H, W, 3), np.uint8),
        road_mask=make_road(), depth=make_depth(), intrinsics=intr(), rocks=[rock]))
    assert_valid(out)
    assert out["rockfalls"][0]["risk_level_basis"] == "INTRUSION_GEOMETRY_ONLY"
    assert out["rockfalls"][0]["risk_level"] == "FULL"


def test_rocks_without_road_stay_unknown(pipe):
    """检测到落石但道路未知时，绝不能给出侵入等级（fail-closed 回归测试）。"""
    rocks = [RockObservation(f"R{i}", BBox(100 + i * 20, 100, 160 + i * 20, 200), 0.9)
             for i in range(3)]
    out = pipe.process(FrameInput(
        frame_id="f11", timestamp_ns=1, rgb=np.zeros((H, W, 3), np.uint8),
        road_mask=np.zeros((H, W), np.uint8), rocks=rocks))
    assert_valid(out)
    assert out["system_status"] == "NO_ROAD"
    assert len(out["rockfalls"]) == 3
    assert all(r["risk_level"] == "UNKNOWN" for r in out["rockfalls"])
    assert out["metrics"]["max_risk_level"] == "UNKNOWN"
