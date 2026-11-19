#!/usr/bin/env python3
"""生成用于汇报的合成沙盘演示数据（不依赖真实采集）。

输入是合成的 RGB-D 数据，但**感知流水线与 C++ 风险引擎是真实代码**：
  * 合成 RGB：80×60 cm 沙盘俯视图，5 cm 双向道路带（单车道 2.5 cm）
  * 合成深度：0.64 m 平面 + 1.5 mm 噪声 + 按场景放置的石块凸起
  * 道路掩码：直接给出真值，绕开尚未微调的道路模型
  * 感知：PerceptionPipeline(obstacle_mode=geometric, road_mask=真值)
  * 判定：rock_risk_cli --stream --policy sandbox_2p5cm_lane.json
  * 输出：每场景 RGB/深度/掩码/感知 JSON/风险 JSON/标注图，
          以及 summary.md、contact_sheet.png、figure_pipeline.png
  * 可选：生成独立 demo.db + artifacts，用 8001 端口起一个只读演示后端

⚠️ 所有 perception 报告的 source 都是 SIMULATED；界面会显示 DEMO 横幅。
   这些数字不能当作真实沙盘检测能力，只能用于汇报展示与链路验证。

用法:
    perception/.venv/bin/python scripts/make_demo_sandbox.py
    perception/.venv/bin/python scripts/make_demo_sandbox.py --out data/demo_sandbox --no-db
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "perception" / "src"))
sys.path.insert(0, str(ROOT))

from backend.app.db import Database, now_ns  # noqa: E402
from backend.app.risk import RiskEngine, assess_and_store  # noqa: E402
from backend.app.storage import ArtifactStore  # noqa: E402
from perception.depth.frame import DepthMap, Intrinsics  # noqa: E402
from perception.pipeline import FrameInput, PerceptionPipeline, PipelineConfig  # noqa: E402

# ---------- 物理与相机参数 ----------
BOARD_W_CM, BOARD_H_CM = 80.0, 60.0
PX_PER_CM = 16                       # 1280 / 80
RGB_W, RGB_H = 1280, 960
DEPTH_W, DEPTH_H = 256, 192
DEPTH_PX_PER_CM = DEPTH_W / BOARD_W_CM  # 3.2
ROAD_LEFT_CM, ROAD_RIGHT_CM = 37.5, 42.5
ROAD_W_CM = ROAD_RIGHT_CM - ROAD_LEFT_CM  # 5.0
LANE_W_CM = 2.5
CAMERA_H_M = 0.64
FX = 1021.0                          # 1280×960 参考分辨率，docs/12

FONT_PATH = "/System/Library/Fonts/STHeiti Medium.ttc"
# 汇报图用的中心放大裁剪：x 30–50 cm，y 22.5–37.5 cm（320×240，4:3）
ZOOM_CROP = (480, 360, 800, 600)
RISK_COLORS = {
    "SAFE": (34, 197, 94),
    "LOW": (234, 179, 8),
    "MEDIUM": (249, 115, 22),
    "HIGH": (239, 68, 68),
    "UNKNOWN": (107, 114, 128),
}
PASS_TEXT = {
    "PASSABLE": "可通行",
    "RESTRICTED": "部分受阻",
    "PARTIALLY_BLOCKED": "部分受阻",
    "BLOCKED": "阻断",
    "UNKNOWN": "未知",
}
RISK_TEXT = {
    "SAFE": "未见侵入",
    "LOW": "仅边缘/邻近",
    "MEDIUM": "部分侵入",
    "HIGH": "严重阻断",
    "UNKNOWN": "无法判定",
}
INTRUSION_COLORS = {
    "NONE": (156, 163, 175),
    "EDGE": (234, 179, 8),
    "PARTIAL": (249, 115, 22),
    "FULL": (239, 68, 68),
    "UNKNOWN": (107, 114, 128),
}


# ---------- 场景定义 ----------

@dataclass
class Shape:
    kind: str            # stone | plank | sheet
    cx_cm: float
    cy_cm: float
    w_cm: float
    h_cm: float
    height_mm: float
    label: str
    seed: int = 0


@dataclass
class Scene:
    scene_id: str
    title: str
    shapes: list[Shape] = field(default_factory=list)
    shadow: bool = False
    note: str = ""
    expected_detected: bool = True
    expected_risk: str = "SAFE"
    expected_pass: str = "PASSABLE"
    expected_remaining_cm: float = ROAD_W_CM


def build_scenes() -> list[Scene]:
    def stone(label: str, cx: float, cy: float, w: float, h: float,
              height_mm: float, seed: int = 0) -> Shape:
        return Shape("stone", cx, cy, w, h, height_mm, label, seed)

    return [
        Scene("01_empty", "空路面", note="负样本：无任何障碍，期望 SAFE / 可通行",
              expected_detected=False),
        Scene("02_shadow", "阴影干扰", shadow=True,
              note="阴影只改变颜色，不改变深度；几何检测不应误报",
              expected_detected=False),
        Scene("03_shoulder", "路肩石块", [stone("S1", 34.5, 30.0, 3.0, 2.0, 20, 11)],
              note="石块在道路带左侧外，应检出但不构成侵入",
              expected_risk="LOW", expected_pass="PASSABLE",
              expected_remaining_cm=ROAD_W_CM),
        Scene("04_half_3cm", "半幅侵入，剩 3 cm",
              [stone("S1", 38.5, 30.0, 2.0, 3.0, 20, 12)],
              note="石块压住左侧 2 cm，右侧剩 3 cm（> 2.5 cm 单车道）",
              expected_risk="MEDIUM", expected_pass="RESTRICTED",
              expected_remaining_cm=3.0),
        Scene("05_block_2cm", "大半阻断，剩 2 cm",
              [stone("S1", 39.0, 30.0, 3.0, 3.0, 30, 13)],
              note="石块压住左侧 3 cm，右侧剩 2 cm（< 2.5 cm 单车道）",
              expected_risk="HIGH", expected_pass="BLOCKED",
              expected_remaining_cm=2.0),
        Scene("06_two_3cm", "两块石，留 3 cm 缝",
              [Shape("slab", 38.0, 30.0, 1.0, 3.0, 20, "S1"),
               Shape("slab", 42.0, 30.0, 1.0, 3.0, 20, "S2")],
              note="左右各压 1 cm，中间剩 3 cm；检验多石联合判定",
              expected_risk="MEDIUM", expected_pass="RESTRICTED",
              expected_remaining_cm=3.0),
        Scene("07_two_1cm", "两块石，留 1 cm 缝",
              [stone("S1", 38.5, 30.0, 2.0, 3.0, 25, 16),
               stone("S2", 41.5, 30.0, 2.0, 3.0, 25, 17)],
              note="左右各压 2 cm，中间剩 1 cm；应判联合阻断",
              expected_risk="HIGH", expected_pass="BLOCKED",
              expected_remaining_cm=1.0),
        Scene("08_low_plank", "低矮横跨障碍（10 mm）",
              [Shape("plank", 40.0, 30.0, 5.0, 4.0, 10, "P1")],
              note="10 mm 高、跨满 5 cm 道路；低于 2 cm 但高于 8 mm 阈值，应检出并阻断",
              expected_risk="HIGH", expected_pass="BLOCKED",
              expected_remaining_cm=0.0),
        Scene("09_big_rock", "路中大石块",
              [stone("S1", 40.0, 30.0, 4.0, 4.0, 40, 18)],
              note="居中 4 cm，两侧各只剩约 0.5 cm；应判阻断",
              expected_risk="HIGH", expected_pass="BLOCKED",
              expected_remaining_cm=0.5),
        Scene("10_thin_5mm", "5 mm 薄片（已知盲区）",
              [Shape("sheet", 40.0, 30.0, 5.0, 4.0, 5, "T1")],
              note="物理上挡住整条路，但 5 mm < 8 mm 阈值：预期漏检。这是盲区，不是成功案例",
              expected_detected=False, expected_risk="SAFE", expected_pass="PASSABLE",
              expected_remaining_cm=0.0),
    ]


# ---------- 绘图工具 ----------

def load_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default()


def rounded_rect_mask(w: int, h: int, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.rectangle(mask, (x0, y0), (x1 - 1, y1 - 1), 255, -1)
    return mask


def stone_mask(w: int, h: int, cx: float, cy: float, sw: float, sh: float,
               scale: float, seed: int, irregular: bool = True) -> np.ndarray:
    """在给定分辨率下画一个石块足迹；返回 uint8 0/255。"""
    mask = np.zeros((h, w), dtype=np.uint8)
    if not irregular:
        x0 = int(round((cx - sw / 2) * scale))
        y0 = int(round((cy - sh / 2) * scale))
        x1 = int(round((cx + sw / 2) * scale))
        y1 = int(round((cy + sh / 2) * scale))
        return rounded_rect_mask(w, h, max(0, x0), max(0, y0),
                                 min(w, x1), min(h, y1))
    rng = np.random.default_rng(seed)
    n = 14
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    jitter = rng.uniform(0.88, 1.06, size=n)
    pts = []
    for a, j in zip(angles, jitter):
        pts.append([(cx + np.cos(a) * sw / 2 * j) * scale,
                    (cy + np.sin(a) * sh / 2 * j) * scale])
    pts = np.array(pts, dtype=np.int32)
    cv2.fillPoly(mask, [pts], 255)
    return mask


def shape_masks(shape: Shape) -> tuple[np.ndarray, np.ndarray]:
    """返回 RGB 分辨率与深度分辨率下的足迹掩码。"""
    irregular = shape.kind == "stone"
    rgb = stone_mask(RGB_W, RGB_H, shape.cx_cm, shape.cy_cm,
                     shape.w_cm, shape.h_cm, PX_PER_CM, shape.seed, irregular)
    dep = stone_mask(DEPTH_W, DEPTH_H, shape.cx_cm, shape.cy_cm,
                     shape.w_cm, shape.h_cm, DEPTH_PX_PER_CM, shape.seed, irregular)
    return rgb, dep


def sand_background(rng: np.random.Generator) -> np.ndarray:
    base = np.zeros((RGB_H, RGB_W, 3), dtype=np.float32)
    base[..., 0] = 196   # R
    base[..., 1] = 178   # G
    base[..., 2] = 146   # B
    coarse = rng.normal(0, 10, (RGB_H // 8, RGB_W // 8)).astype(np.float32)
    coarse = cv2.resize(coarse, (RGB_W, RGB_H), interpolation=cv2.INTER_CUBIC)
    fine = rng.normal(0, 3.5, (RGB_H, RGB_W)).astype(np.float32)
    base += (coarse + fine)[..., None]
    # 沙粒：小的深色/浅色点
    for _ in range(900):
        x = int(rng.integers(0, RGB_W))
        y = int(rng.integers(0, RGB_H))
        r = int(rng.integers(1, 3))
        delta = float(rng.uniform(-18, 14))
        cv2.circle(base, (x, y), r, (base[y, x] + delta).clip(0, 255).tolist(), -1)
    return base.clip(0, 255).astype(np.uint8)


def paint_road(img: np.ndarray, rng: np.random.Generator) -> None:
    x0 = int(round(ROAD_LEFT_CM * PX_PER_CM))
    x1 = int(round(ROAD_RIGHT_CM * PX_PER_CM))
    road = np.zeros((RGB_H, x1 - x0, 3), dtype=np.float32)
    road[..., 0] = 96
    road[..., 1] = 99
    road[..., 2] = 104
    road += rng.normal(0, 5.0, road.shape).astype(np.float32)
    # 沿路方向的细纹
    for _ in range(160):
        y = int(rng.integers(0, RGB_H))
        alpha = float(rng.uniform(0.03, 0.10))
        color = np.array([70, 72, 78], dtype=np.float32)
        road[y, :] = road[y, :] * (1 - alpha) + color * alpha
    img[:, x0:x1] = road.clip(0, 255).astype(np.uint8)
    # 路缘：一条很窄的深色边界
    cv2.line(img, (x0, 0), (x0, RGB_H - 1), (74, 76, 80), 2)
    cv2.line(img, (x1 - 1, 0), (x1 - 1, RGB_H - 1), (74, 76, 80), 2)


def paint_shadow(img: np.ndarray, rng: np.random.Generator) -> None:
    layer = Image.new("RGBA", (RGB_W, RGB_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    pts = [(80, 250), (420, 170), (1060, 700), (720, 900)]
    d.polygon(pts, fill=(20, 26, 38, 150))
    layer = layer.filter(ImageFilter.GaussianBlur(22))
    base = Image.fromarray(img).convert("RGBA")
    base.alpha_composite(layer)
    img[:] = np.asarray(base.convert("RGB"))


def paint_shape(img: np.ndarray, shape: Shape, mask: np.ndarray,
                rng: np.random.Generator) -> np.ndarray:
    """在 RGB 图上画石块/木板/薄片，带阴影、明暗和高光。返回新图。"""
    h, w = mask.shape
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return img
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1

    # 阴影：偏移 + 高斯模糊
    shadow = np.zeros_like(mask)
    shift_x, shift_y = 5, 7
    ys2 = np.clip(ys + shift_y, 0, h - 1)
    xs2 = np.clip(xs + shift_x, 0, w - 1)
    shadow[ys2, xs2] = 255
    shadow_img = Image.fromarray(shadow).filter(ImageFilter.GaussianBlur(6))
    shadow_np = np.asarray(shadow_img).astype(np.float32) / 255.0
    img = img.astype(np.float32)
    img *= (1.0 - 0.42 * shadow_np[..., None])
    img = img.clip(0, 255)

    # 主体颜色
    if shape.kind == "plank":
        base = np.array([126, 92, 58], dtype=np.float32)      # 木色
    elif shape.kind == "sheet":
        base = np.array([70, 74, 82], dtype=np.float32)       # 深灰薄片
    else:
        base = np.array([132, 132, 136], dtype=np.float32)    # 石灰色

    body = np.zeros((h, w, 3), dtype=np.float32)
    body[:] = base
    body += rng.normal(0, 7.0, body.shape).astype(np.float32)
    # 垂直光照渐变：上亮下暗
    grad = np.linspace(1.12, 0.82, y1 - y0, dtype=np.float32)[:, None, None]
    body[y0:y1, x0:x1] *= grad
    # 木纹 / 石纹
    if shape.kind == "plank":
        for _ in range(22):
            yy = int(rng.integers(y0, y1))
            cv2.line(body, (x0, yy), (x1, yy), (100, 70, 42), 1)
    else:
        for _ in range(30):
            px = int(rng.integers(x0, x1))
            py = int(rng.integers(y0, y1))
            cv2.circle(body, (px, py), int(rng.integers(1, 3)),
                       tuple(float(v) for v in base * rng.uniform(0.72, 1.12)), -1)
    # 高光：上边缘
    cv2.line(body, (x0 + 6, y0 + 3), (x1 - 6, y0 + 3),
             tuple(float(v) for v in base * 1.35), 2)
    # 轮廓
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(body, contours, -1, tuple(float(v) for v in base * 0.55), 2)

    m = mask.astype(bool)
    img[m] = body[m]
    return img.clip(0, 255).astype(np.uint8)


def make_rgb(scene: Scene, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    img = sand_background(rng)
    paint_road(img, rng)
    if scene.shadow:
        paint_shadow(img, rng)
    road_mask = np.zeros((RGB_H, RGB_W), dtype=np.uint8)
    x0 = int(round(ROAD_LEFT_CM * PX_PER_CM))
    x1 = int(round(ROAD_RIGHT_CM * PX_PER_CM))
    road_mask[:, x0:x1] = 255
    for shape in scene.shapes:
        rgb_mask, _ = shape_masks(shape)
        img = paint_shape(img, shape, rgb_mask, rng)
    return img, road_mask


def make_depth(scene: Scene, rng: np.random.Generator) -> np.ndarray:
    depth = np.full((DEPTH_H, DEPTH_W), CAMERA_H_M, dtype=np.float32)
    depth += rng.normal(0, 0.0015, depth.shape).astype(np.float32)
    for shape in scene.shapes:
        _, dep_mask = shape_masks(shape)
        depth[dep_mask > 0] -= shape.height_mm / 1000.0
    depth += rng.normal(0, 0.0005, depth.shape).astype(np.float32)
    return depth.astype(np.float32)


# ---------- 结果可视化 ----------

def colorize_depth(depth: np.ndarray, vmin: float = 0.60, vmax: float = 0.68) -> np.ndarray:
    depth = np.asarray(depth)
    if depth.ndim == 3 and depth.shape[-1] == 1:
        depth = depth[..., 0]
    stops = np.array([
        [37, 99, 235], [6, 182, 212], [34, 197, 94], [250, 204, 21], [239, 68, 68],
    ], dtype=np.float32)
    t = np.clip((depth - vmin) / (vmax - vmin), 0, 0.999)
    pos = t * (len(stops) - 1)
    idx = np.floor(pos).astype(int)
    frac = (pos - idx)[..., None]
    rgb = stops[idx] * (1 - frac) + stops[np.minimum(idx + 1, len(stops) - 1)] * frac
    rgb[depth <= 0] = 0
    return rgb.astype(np.uint8)


def depth_visual(depth: np.ndarray, title: str) -> Image.Image:
    rgb = colorize_depth(depth)
    big = cv2.resize(rgb, (RGB_W, RGB_H), interpolation=cv2.INTER_NEAREST)
    img = Image.fromarray(big).convert("RGB")
    d = ImageDraw.Draw(img)
    f_title = load_font(30)
    f_small = load_font(20)
    d.rounded_rectangle([20, 20, 700, 82], radius=14, fill=(15, 23, 42))
    d.text((38, 32), title, font=f_title, fill=(241, 245, 249))
    # 色带
    bar_x0, bar_x1, bar_y0, bar_y1 = 40, 760, 890, 918
    grad = np.linspace(0, 1, bar_x1 - bar_x0, dtype=np.float32)
    bar = np.repeat(grad[None, :, None], bar_y1 - bar_y0, axis=0)
    bar_rgb = colorize_depth(bar * (0.68 - 0.60) + 0.60)
    img.paste(Image.fromarray(bar_rgb), (bar_x0, bar_y0))
    d.rectangle([bar_x0, bar_y0, bar_x1, bar_y1], outline=(148, 163, 184), width=1)
    d.text((bar_x0, bar_y1 + 8), "0.60 m", font=f_small, fill=(226, 232, 240))
    d.text(((bar_x0 + bar_x1) // 2 - 32, bar_y1 + 8), "0.64 m",
           font=f_small, fill=(226, 232, 240))
    d.text((bar_x1 - 70, bar_y1 + 8), "0.68 m", font=f_small, fill=(226, 232, 240))
    d.text((880, 892), "深度伪彩色\n蓝=近  红=远", font=f_small,
           fill=(226, 232, 240), spacing=6)
    return img


def draw_badge(d: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
               color: tuple[int, int, int], fg: tuple[int, int, int] = (15, 23, 42),
               size: int = 22) -> tuple[int, int]:
    f = load_font(size)
    bbox = d.textbbox((0, 0), text, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = xy
    d.rounded_rectangle([x, y, x + w + 24, y + h + 14], radius=8, fill=color)
    d.text((x + 12, y + 4), text, font=f, fill=fg)
    return x + w + 32, y


def overlay_image(rgb: np.ndarray, road_mask: np.ndarray, report: dict,
                  status: dict, scene: Scene) -> Image.Image:
    base = Image.fromarray(rgb).convert("RGBA")
    # 道路掩码：绿色半透明
    mask_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    md = ImageDraw.Draw(mask_layer)
    m = (road_mask > 0).astype(np.uint8) * 255
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        pts = [tuple(map(int, p)) for p in c.reshape(-1, 2)]
        if len(pts) >= 3:
            md.polygon(pts, fill=(34, 197, 94, 72))
    base.alpha_composite(mask_layer)
    d = ImageDraw.Draw(base)
    f_title = load_font(30)
    f_body = load_font(22)
    f_small = load_font(18)

    # 检测框与标签。多块石并排时标签会互相盖住，因此先试上方、再试下方。
    placed: list[tuple[int, int, int, int]] = []
    for rock in report.get("rockfalls", []):
        bbox = rock.get("bbox") or []
        if len(bbox) != 4:
            continue
        intrusion = str(rock.get("intrusion", "UNKNOWN"))
        color = INTRUSION_COLORS.get(intrusion, (107, 114, 128))
        x0, y0, x1, y1 = [int(round(v)) for v in bbox]
        d.rectangle([x0, y0, x1, y1], outline=color, width=4)
        rock_id = str(rock.get("rock_id", "?")).replace("GEO-", "G")
        label = f"{rock_id} {intrusion}"
        tw = d.textbbox((0, 0), label, font=f_small)[2]
        lw, lh = tw + 14, 28
        candidates = [(x0, max(0, y0 - lh)), (x0, min(RGB_H - lh, y1 + 4))]
        chosen = candidates[0]
        for cx, cy in candidates:
            rect = (cx, cy, cx + lw, cy + lh)
            if cx + lw > RGB_W:
                continue
            if not any(rect[0] < p[2] and p[0] < rect[2] and
                       rect[1] < p[3] and p[1] < rect[3] for p in placed):
                chosen = (cx, cy)
                break
        cx, cy = chosen
        d.rectangle([cx, cy, cx + lw, cy + lh], fill=(15, 23, 42))
        d.text((cx + 7, cy + 3), label, font=f_small, fill=color)
        placed.append((cx, cy, cx + lw, cy + lh))

    risk = str(status.get("overall_risk", "UNKNOWN"))
    passability = str(status.get("passability", {}).get("A_TO_B", "UNKNOWN"))
    remaining = None
    dirs = status.get("directions") or []
    if dirs:
        remaining = dirs[0].get("remaining_width_m")
    color = RISK_COLORS.get(risk, RISK_COLORS["UNKNOWN"])

    # 左上信息面板
    d.rounded_rectangle([20, 20, 680, 205], radius=16, fill=(15, 23, 42, 225))
    d.text((42, 34), scene.title, font=f_title, fill=(241, 245, 249))
    d.text((42, 82), f"{risk} · {RISK_TEXT.get(risk, '')}", font=f_body, fill=color)
    d.text((42, 118), f"通行性：{PASS_TEXT.get(passability, passability)}",
           font=f_body, fill=(226, 232, 240))
    if remaining is None and risk in ("SAFE", "LOW"):
        rem_text = f"剩余宽度：{ROAD_W_CM:.1f} cm（未阻断）"
    elif remaining is None:
        rem_text = "剩余宽度：—"
    else:
        rem_text = f"剩余宽度：{remaining * 100:.1f} cm"
    d.text((42, 152), rem_text, font=f_body, fill=(226, 232, 240))
    d.text((420, 152), f"障碍：{len(report.get('rockfalls', []))} 个",
           font=f_body, fill=(226, 232, 240))

    # 右上角：DEMO 标识
    draw_badge(d, (RGB_W - 330, 24), "SYNTHETIC DEMO", (250, 204, 21), size=22)
    d.text((RGB_W - 330, 70), "合成输入 · 真实感知/风险代码",
           font=f_small, fill=(254, 249, 195))
    d.text((RGB_W - 330, 96), f"策略 v3 · 单车道 {LANE_W_CM:.1f} cm",
           font=f_small, fill=(226, 232, 240))

    # 底部说明
    if scene.note:
        d.rounded_rectangle([20, RGB_H - 74, RGB_W - 20, RGB_H - 18],
                            radius=12, fill=(15, 23, 42, 210))
        d.text((38, RGB_H - 64), scene.note, font=f_small, fill=(226, 232, 240))
    return base.convert("RGB")


def _reason_codes(status: dict, limit: int = 6) -> list[str]:
    """决策理由优先，其次是感知/合成来源标注。"""
    codes: list[str] = []
    groups = ([r for d in status.get("directions", []) for r in d.get("reasons", [])],
              status.get("reasons", []))
    for group in groups:
        for item in group:
            code = item.get("code") if isinstance(item, dict) else None
            if code and code not in codes:
                codes.append(code)
    return codes[:limit]


def result_panel(scene: Scene, report: dict, status: dict,
                 measured: list[dict], size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (w, h), (15, 23, 42))
    d = ImageDraw.Draw(img)
    f_title = load_font(28)
    f_big = load_font(44)
    f_body = load_font(20)
    f_small = load_font(16)
    risk = str(status.get("overall_risk", "UNKNOWN"))
    passability = str(status.get("passability", {}).get("A_TO_B", "UNKNOWN"))
    color = RISK_COLORS.get(risk, RISK_COLORS["UNKNOWN"])
    d.text((32, 20), "风险判定结果", font=f_title, fill=(241, 245, 249))
    d.text((32, 60), risk, font=f_big, fill=color)
    d.text((32, 114), f"{RISK_TEXT.get(risk, '')} · {PASS_TEXT.get(passability, passability)}",
           font=f_body, fill=color)
    dirs = status.get("directions") or []
    remaining = dirs[0].get("remaining_width_m") if dirs else None
    if remaining is None and risk in ("SAFE", "LOW"):
        rem_text = f"剩余宽度：{ROAD_W_CM:.1f} cm（未阻断）"
    elif remaining is None:
        rem_text = "剩余宽度：—"
    else:
        rem_text = f"剩余宽度：{remaining * 100:.2f} cm"
    d.text((32, 150), rem_text, font=f_body, fill=(226, 232, 240))
    d.text((32, 182),
           f"检出障碍：{len(report.get('rockfalls', []))} 个 · "
           f"道路覆盖：{report.get('road', {}).get('coverage_ratio', 0) * 100:.1f}%",
           font=f_body, fill=(226, 232, 240))
    d.line([32, 214, w - 32, 214], fill=(51, 65, 85), width=2)
    d.text((32, 226), "几何测量", font=f_small, fill=(148, 163, 184))
    y = 250
    if measured:
        for item in measured[:3]:
            text = (f"{item['rock_id']}  高 {item['mean_mm']:.1f} mm"
                    f"（峰值 {item['peak_mm']:.1f} mm，SNR {item['snr']:.1f}）")
            d.text((32, y), text, font=f_small, fill=(226, 232, 240))
            y += 24
    else:
        d.text((32, y), "未检出障碍", font=f_small, fill=(148, 163, 184))
        y += 24
    d.line([32, 330, w - 32, 330], fill=(51, 65, 85), width=2)
    d.text((32, 340), "理由码", font=f_small, fill=(148, 163, 184))
    y = 362
    for code in _reason_codes(status, 3):
        d.text((32, y), f"· {code}", font=f_small, fill=(226, 232, 240))
        y += 22
    d.text((32, h - 46), "实验性辅助评估，不构成真实道路驾驶指令。",
           font=f_small, fill=(148, 163, 184))
    d.text((32, h - 24), "输入为合成数据；不得作为真实检测能力宣称。",
           font=f_small, fill=(250, 204, 21))
    return img


# ---------- 汇总与图表 ----------

def scene_measured(pipeline: PerceptionPipeline) -> list[dict]:
    geom = pipeline.last_obstacle_geometry
    out = []
    if geom is None:
        return out
    for p in geom.protrusions:
        out.append({
            "rock_id": f"GEO-{len(out) + 1}",
            "mean_mm": p.mean_height_m * 1000,
            "peak_mm": p.peak_height_m * 1000,
            "snr": p.snr,
            "area_px": p.area_px,
        })
    return out


def make_contact_sheet(rows: list[dict], path: Path) -> None:
    cols, cell_w, cell_h, cap_h = 4, 480, 360, 74
    rows_n = (len(rows) + cols - 1) // cols
    margin, title_h = 28, 128
    W = margin * 2 + cols * cell_w + (cols - 1) * 12
    H = title_h + rows_n * (cell_h + cap_h) + (rows_n - 1) * 12 + margin
    img = Image.new("RGB", (W, H), (15, 23, 42))
    d = ImageDraw.Draw(img)
    f_title = load_font(38)
    f_sub = load_font(22)
    f_cap = load_font(21)
    f_small = load_font(17)
    d.text((margin, 24), "沙盘演示数据（合成）· 5 cm 双向路 / 2.5 cm 单车道 / 0.64 m 俯视",
           font=f_title, fill=(241, 245, 249))
    d.text((margin, 78), "输入 RGB-D 为合成数据；感知流水线与 C++ 风险引擎为真实代码。仅用于汇报演示。",
           font=f_sub, fill=(148, 163, 184))
    for i, row in enumerate(rows):
        r, c = divmod(i, cols)
        x = margin + c * (cell_w + 12)
        y = title_h + r * (cell_h + cap_h + 12)
        thumb = row["overlay_zoom"].resize((cell_w, cell_h), Image.LANCZOS)
        img.paste(thumb, (x, y))
        d.rectangle([x, y, x + cell_w, y + cell_h], outline=(51, 65, 85), width=2)
        risk = row["actual"]["risk"]
        is_blind = row["scene"].scene_id == "10_thin_5mm"
        color = (250, 204, 21) if is_blind else RISK_COLORS.get(
            risk, RISK_COLORS["UNKNOWN"])
        risk_label = "SAFE（漏检）" if is_blind else risk
        if is_blind:
            badge = "已知盲区"
            bw = d.textbbox((0, 0), badge, font=f_small)[2] + 18
            d.rounded_rectangle([x + cell_w - bw - 10, y + 10,
                                 x + cell_w - 10, y + 42],
                                radius=7, fill=(250, 204, 21))
            d.text((x + cell_w - bw - 2, y + 17), badge,
                   font=f_small, fill=(15, 23, 42))
        d.rectangle([x, y + cell_h, x + cell_w, y + cell_h + cap_h],
                    fill=(30, 41, 59))
        d.ellipse([x + 12, y + cell_h + 18, x + 28, y + cell_h + 34], fill=color)
        d.text((x + 38, y + cell_h + 12), row["scene"].title,
               font=f_cap, fill=(241, 245, 249))
        remaining = row["actual"].get("remaining_m")
        if remaining is None and risk in ("SAFE", "LOW"):
            rem = f"剩 {ROAD_W_CM:.1f} cm（未阻断）"
        elif remaining is None:
            rem = "剩 —"
        else:
            rem = f"剩 {remaining * 100:.1f} cm"
        d.text((x + 38, y + cell_h + 42),
               f"{risk_label} · {PASS_TEXT.get(row['actual']['pass'], row['actual']['pass'])} · {rem}",
               font=f_small, fill=color)
    img.save(path)


def make_pipeline_figure(row: dict, path: Path) -> None:
    scene = row["scene"]
    rgb = row["rgb"]
    overlay = row["overlay"]
    depth_img = row["depth_img"]
    panel = result_panel(scene, row["report"], row["status"],
                         row["measured"], (640, 480))
    canvas = Image.new("RGB", (1300, 1100), (15, 23, 42))
    f = load_font(24)
    titles = ["① RGB 输入（全场）", "② 道路掩码 + 障碍框（放大）",
              "③ 深度（放大）", "④ 风险判定"]
    imgs = [
        Image.fromarray(rgb).resize((640, 480), Image.LANCZOS),
        overlay.crop(ZOOM_CROP).resize((640, 480), Image.LANCZOS),
        depth_img.crop(ZOOM_CROP).resize((640, 480), Image.LANCZOS),
        panel,
    ]
    for i, (im, title) in enumerate(zip(imgs, titles)):
        r, c = divmod(i, 2)
        x = 10 + c * 650
        y = 80 + r * 520
        canvas.paste(im, (x, y))
        ImageDraw.Draw(canvas).text((x, y - 30), title, font=f, fill=(226, 232, 240))
    ImageDraw.Draw(canvas).text(
        (10, 12), "沙盘演示数据生成链路（合成输入 / 真实感知与风险代码）",
        font=f, fill=(241, 245, 249))
    canvas.save(path)


# ---------- 主流程 ----------

def validate_report(report: dict, schema: dict, label: str) -> None:
    errors = sorted(Draft202012Validator(schema).iter_errors(report),
                    key=lambda e: list(e.absolute_path))
    if errors:
        msg = "; ".join(f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
                        for e in errors[:5])
        raise SystemExit(f"{label} 不符合 perception_report schema: {msg}")


def build_metadata(frame_id: str, session_id: str, seq: int, ts_ns: int,
                   jpeg_bytes: int) -> dict:
    return {
        "schema_version": "1.0",
        "msg_type": "frame_bundle",
        "frame_id": frame_id,
        "device_id": "demo-sandbox",
        "session_id": session_id,
        "seq": seq,
        "ts_capture_ns": ts_ns,
        "capture": {
            "width": RGB_W,
            "height": RGB_H,
            "bytes": jpeg_bytes,
            "format": "jpeg",
            "jpeg_quality": 0.92,
            "orientation": "landscape_right",
            "device_orientation": "landscape_right",
        },
        "intrinsics": {
            "fx": FX, "fy": FX, "cx": RGB_W / 2, "cy": RGB_H / 2,
            "reference_width": RGB_W, "reference_height": RGB_H,
        },
        "depth": {
            "width": DEPTH_W, "height": DEPTH_H,
            "encoding": "float32_m", "scale_factor": 1.0,
            "invalid_value": 0.0, "aligned_to_rgb": True,
            "source": "arkit.sceneDepth",
        },
        "confidence": {
            "width": DEPTH_W, "height": DEPTH_H,
            "encoding": "uint8", "semantics": "ar_confidence_level",
        },
        "pose": {
            "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "tracking_state": "normal",
        },
        "delivery": {"queue_depth": 0, "attempt": 1, "dropped_since_last": 0},
        "note": "SYNTHETIC DEMO — 非真实采集，仅用于汇报演示",
    }


def publish_db(out: Path, rows: list[dict], policy: Path, engine_bin: Path) -> Path:
    db_path = out / "demo.db"
    art_root = out / "artifacts"
    if db_path.exists():
        db_path.unlink()
    if art_root.exists():
        shutil.rmtree(art_root)
    db = Database(db_path)
    store = ArtifactStore(art_root)
    engine = RiskEngine(binary=engine_bin, policy=policy)
    db.insert_event(now_ns(), None, "demo-sandbox", "INFO", "DEMO_DATA",
                    "合成演示数据：非真实采集，仅用于汇报演示")
    # 最后发布 05_block_2cm，让 Web/App 首屏看到 BLOCKED 场景
    order = [r for r in rows if r["scene"].scene_id != "05_block_2cm"]
    order += [r for r in rows if r["scene"].scene_id == "05_block_2cm"]
    for index, row in enumerate(order):
        frame_id = row["frame_id"]
        meta = row["metadata"]
        saved = store.save(frame_id, meta, row["rgb_bytes"],
                           row["depth_bytes"], row["conf_bytes"])
        for name in ("road_mask.png", "perception.json", "depth_vis.png", "overlay.png"):
            shutil.copy2(row["scene_dir"] / name, store.directory(frame_id) / name)
        db.upsert_device("demo-sandbox", now_ns(),
                         meta={"note": "SYNTHETIC DEMO", "target_fps": 0,
                               "thermal": "nominal"})
        db.insert_frame({
            "frame_id": frame_id,
            "device_id": "demo-sandbox",
            "session_id": meta["session_id"],
            "seq": index,
            "ts_capture_ns": meta["ts_capture_ns"],
            "ts_received_ns": now_ns(),
            "width": RGB_W,
            "height": RGB_H,
            "jpeg_bytes": len(row["rgb_bytes"]),
            "depth_bytes": len(row["depth_bytes"]),
            "confidence_bytes": len(row["conf_bytes"]),
            "dropped_since_last": 0,
            "status": "stored",
            "error": None,
            **saved,
        })
        assess_and_store(db, engine, frame_id, "demo-sandbox", row["report"])
    engine.close()
    db.close()
    return db_path


def _fmt_cm(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f} cm"


def _fmt_m(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f} cm"


def _actual_remaining_text(row: dict) -> str:
    value = row["actual"].get("remaining_m")
    if value is not None:
        return f"{value * 100:.1f} cm"
    if row["actual"].get("risk") in ("SAFE", "LOW"):
        return f"{ROAD_W_CM:.1f} cm（未阻断）"
    return "—"


def _judgement(row: dict) -> str:
    scene = row["scene"]
    checks = row["checks"]
    if scene.scene_id == "10_thin_5mm":
        return "盲区（预期漏检）"
    if checks["risk_match"] and checks["pass_match"] and checks["detected_match"]:
        diff = checks["remaining_diff_cm"]
        if diff is not None and abs(diff) > 0.8:
            return "风险一致，宽度偏差"
        return "通过"
    return "与预期有偏差"


def write_summary(out: Path, rows: list[dict], db_command: str | None) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 沙盘演示数据（合成）",
        "",
        f"> 生成时间：{now}　|　输入：**合成 RGB-D**　|　感知/风险：**真实代码**",
        "",
        "> ⚠️ 这套数据用于汇报演示与链路验证，**不是真实沙盘采集**，",
        "> 不能作为“系统已在沙盘上验证”的依据。所有 perception 报告均标 `source=SIMULATED`，",
        "> Web 页面会显示 DEMO 横幅。",
        "",
        "## 设置",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 沙盘 | 80 × 60 cm，俯视 0.64 m |",
        "| 道路 | 双向 5 cm，单车道 2.5 cm |",
        "| RGB / 深度 | 1280×960 / 256×192（1 深度像素 ≈ 3.1 mm） |",
        "| 合成深度 | 平面 + σ=1.5 mm 噪声 + 场景凸起 |",
        "| 道路掩码 | 真值（不经过道路模型） |",
        "| 风险策略 | `sandbox_2p5cm_lane.json`（policy_version 3） |",
        "| 障碍来源 | RGB-D 几何，不使用分类模型 |",
        "",
        "## 场景结果",
        "",
        "| # | 场景 | 物理剩余 | 实际剩余 | 期望风险 | 实际风险 | 通行性 | 检出 | 判定 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        scene = row["scene"]
        actual = row["actual"]
        det = "是" if actual["rock_count"] else "否"
        lines.append(
            f"| {scene.scene_id} | {scene.title} | "
            f"{_fmt_cm(scene.expected_remaining_cm)} | {_actual_remaining_text(row)} | "
            f"{scene.expected_risk} | {actual['risk']} | "
            f"{PASS_TEXT.get(actual['pass'], actual['pass'])} | {det} | {_judgement(row)} |")
    lines += [
        "",
        "> `物理剩余` 是按场景几何算出的真实可通行宽度；`实际剩余` 是感知 + C++ 引擎的输出。",
        "> 2.5 cm 阈值附近存在 ±3–6 mm 的量化不确定度，所以演示应使用 3 cm / 2 cm 这类明确案例。",
        "",
        "## 每个场景的文件",
        "",
        "```",
        "scenes/<scene_id>/",
        "  rgb.jpg            合成 RGB（可做输入图）",
        "  depth.bin          256×192 float32（米）",
        "  confidence.bin     256×192 uint8（全 2=高置信）",
        "  metadata.json      FrameBundle 格式（note 标明 SYNTHETIC DEMO）",
        "  road_mask.png      1280×960，0/255 道路真值",
        "  perception.json    完整感知结果（source=SIMULATED）",
        "  road_status.json   C++ 风险引擎输出",
        "  overlay.png        RGB + 道路掩码 + 障碍框 + 判定（全场）",
        "  overlay_zoom.png   中心放大版（汇报用）",
        "  depth_vis.png      深度伪彩色 + 色带（全场）",
        "  depth_zoom.png     中心放大深度图",
        "```",
        "",
        "汇总图：`contact_sheet.png`（10 场景总览）、`figure_pipeline.png`（单场景四联图）。",
        "",
        "## 怎么用于汇报",
        "",
        "1. **幻灯片总览**：用 `contact_sheet.png`，配下面这张表。",
        "2. **链路说明**：用 `figure_pipeline.png` 的 4 个面板依次讲 RGB → 掩码/障碍 → 深度 → 风险。",
        "3. **单项案例**：`scenes/04_half_3cm/overlay.png`（剩 3 cm，RESTRICTED）与",
        "   `scenes/05_block_2cm/overlay.png`（剩 2 cm，BLOCKED）最适合做对比。",
        "4. **负样本/局限**：`02_shadow` 证明几何法不受阴影影响；",
        "   `10_thin_5mm` 是已知盲区，放在“限制与改进”页，不要放在成果页。",
        "",
        "## Web/App 演示（可选）",
        "",
        "生成时如果没加 `--no-db`，会额外生成 `demo.db` 与 `artifacts/`。",
        "它不依赖 AI worker，判定结果是预置的：",
        "",
        "```bash",
    ]
    if db_command:
        lines += [db_command, "```", "",
                  "然后打开 `http://127.0.0.1:8001/`（用户端）与",
                  "`http://127.0.0.1:8001/#/admin`（管理后台）。首屏是 BLOCKED 场景，",
                  "历史页可以翻到其余场景。所有页面都会显示 DEMO 横幅。"]
    else:
        lines += ["# 本次使用 --no-db 生成，未创建演示数据库", "```"]
    lines += [
        "",
        "## 不能宣称",
        "",
        "1. 这套数据不是沙盘实采，不能证明真实检测能力；",
        "2. 合成深度没有真实 LiDAR 的飞点、反射、多路径等失效模式；",
        "3. 5 mm 薄片场景是**漏检**，恰好说明 8 mm 阈值以下不可见；",
        "4. 道路掩码是真值，绕开了道路模型的域适配问题——真实闭环仍需沙盘数据微调。",
        "",
    ]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    readme = f"""# 沙盘演示数据（合成）

用 `scripts/make_demo_sandbox.py` 生成。**输入 RGB-D 是合成的，感知与风险引擎是真实代码。**

- 设置：80×60 cm 沙盘、0.64 m 俯视、双向 5 cm / 单车道 2.5 cm、256×192 深度。
- 结果：见 `summary.md` / `summary.json`；总览图 `contact_sheet.png`；链路图 `figure_pipeline.png`。
- 所有 perception 报告 `source=SIMULATED`，Web 页面会显示 DEMO 横幅。
- 生成时间：{now}

重新生成：

```bash
perception/.venv/bin/python scripts/make_demo_sandbox.py --out {out}
```

不能把本目录当作真实沙盘验证证据。
"""
    (out / "README.md").write_text(readme, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "demo_sandbox")
    ap.add_argument("--policy", type=Path,
                    default=ROOT / "risk_engine" / "policy" / "sandbox_2p5cm_lane.json")
    ap.add_argument("--engine", type=Path,
                    default=ROOT / "risk_engine" / "build" / "rock_risk_cli")
    ap.add_argument("--no-db", action="store_true", help="不生成演示数据库")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if not args.engine.exists():
        raise SystemExit(f"未找到风险引擎 {args.engine}，先运行 bash scripts/build_risk_engine.sh")
    if not args.policy.exists():
        raise SystemExit(f"未找到策略 {args.policy}")

    out: Path = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    (out / "scenes").mkdir(parents=True)
    rng = np.random.default_rng(args.seed)
    session_id = str(uuid.uuid4())
    base_ns = time.time_ns()

    report_schema = json.loads(
        (ROOT / "contracts" / "schemas" / "perception_report.schema.json")
        .read_text(encoding="utf-8"))
    status_schema = json.loads(
        (ROOT / "contracts" / "schemas" / "road_status.schema.json")
        .read_text(encoding="utf-8"))
    bundle_schema = json.loads(
        (ROOT / "contracts" / "schemas" / "frame_bundle.schema.json")
        .read_text(encoding="utf-8"))
    bundle_validator = Draft202012Validator(bundle_schema)
    status_validator = Draft202012Validator(status_schema)

    scenes = build_scenes()
    rows: list[dict] = []
    engine = RiskEngine(binary=args.engine, policy=args.policy)

    for index, scene in enumerate(scenes):
        scene_dir = out / "scenes" / scene.scene_id
        scene_dir.mkdir(parents=True, exist_ok=True)

        rgb, road_mask = make_rgb(scene, rng)
        depth = make_depth(scene, rng)
        confidence = np.full((DEPTH_H, DEPTH_W), 2, dtype=np.uint8)
        frame_id = str(uuid.uuid4())
        ts_ns = base_ns + index * 100_000_000

        intrinsics = Intrinsics(FX, FX, RGB_W / 2, RGB_H / 2, RGB_W, RGB_H)
        depth_map = DepthMap(
            meters=depth,
            valid=np.isfinite(depth) & (depth > 0),
            source="SYNTHETIC",
            aligned_to_rgb=True,
        )
        pipeline = PerceptionPipeline(
            config=PipelineConfig(obstacle_mode="geometric", road_source="road_model"))
        report = pipeline.process(FrameInput(
            frame_id=frame_id,
            timestamp_ns=ts_ns,
            rgb=cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
            depth=depth_map,
            intrinsics=intrinsics,
            confidence=confidence,
            road_mask=road_mask,
            source="SIMULATED",
        ))
        measured = scene_measured(pipeline)
        report["source"] = "SIMULATED"
        report["reasons"].append({
            "code": "OBSTACLE_SOURCE_GEOMETRIC",
            "message": "障碍由 RGB-D 几何检出，不含类别信息"})
        report["reasons"].append({
            "code": "SYNTHETIC_DEMO",
            "message": "输入为合成 RGB-D，仅用于汇报演示"})
        report["reasons"].append({
            "code": "SYNTHETIC_DEPTH",
            "message": "深度由平面 + 合成凸起生成，非 LiDAR 实测"})
        validate_report(report, report_schema, scene.scene_id)

        status = engine.assess(report)
        errors = sorted(status_validator.iter_errors(status),
                        key=lambda e: list(e.absolute_path))
        if errors:
            msg = "; ".join(
                f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
                for e in errors[:5])
            raise SystemExit(f"{scene.scene_id} 不符合 road_status schema: {msg}")

        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, jpg = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            raise SystemExit("JPEG 编码失败")
        rgb_bytes = jpg.tobytes()
        depth_bytes = depth.astype("<f4").tobytes()
        conf_bytes = confidence.tobytes()

        metadata = build_metadata(frame_id, session_id, index, ts_ns, len(rgb_bytes))
        mk_errors = sorted(bundle_validator.iter_errors(metadata),
                           key=lambda e: list(e.absolute_path))
        if mk_errors:
            msg = "; ".join(
                f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
                for e in mk_errors[:5])
            raise SystemExit(f"{scene.scene_id} metadata 不符合 frame_bundle schema: {msg}")

        (scene_dir / "rgb.jpg").write_bytes(rgb_bytes)
        (scene_dir / "depth.bin").write_bytes(depth_bytes)
        (scene_dir / "confidence.bin").write_bytes(conf_bytes)
        (scene_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        cv2.imwrite(str(scene_dir / "road_mask.png"), road_mask)
        (scene_dir / "perception.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (scene_dir / "road_status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

        depth_img = depth_visual(depth, f"深度 · {scene.title}")
        depth_img.save(scene_dir / "depth_vis.png")
        overlay = overlay_image(rgb, road_mask, report, status, scene)
        overlay.save(scene_dir / "overlay.png")

        overlay_zoom = overlay.crop(ZOOM_CROP).resize((640, 480), Image.LANCZOS)
        dz = ImageDraw.Draw(overlay_zoom)
        dz.rounded_rectangle([12, 12, 212, 44], radius=8, fill=(250, 204, 21))
        dz.text((22, 17), "SYNTHETIC DEMO", font=load_font(18), fill=(15, 23, 42))
        overlay_zoom.save(scene_dir / "overlay_zoom.png")
        depth_zoom = depth_img.crop(ZOOM_CROP).resize((640, 480), Image.LANCZOS)
        depth_zoom.save(scene_dir / "depth_zoom.png")

        dirs = status.get("directions") or []
        remaining_m = dirs[0].get("remaining_width_m") if dirs else None
        detections = [{
            "rock_id": rock.get("rock_id"),
            "label": rock.get("label"),
            "intrusion": rock.get("intrusion"),
            "bbox": rock.get("bbox"),
            "remaining_m": rock.get("remaining_width_m"),
            "confidence": rock.get("confidence"),
        } for rock in report.get("rockfalls", [])]
        actual = {
            "risk": status.get("overall_risk"),
            "pass": status.get("passability", {}).get("A_TO_B"),
            "remaining_m": remaining_m,
            "rock_count": len(report.get("rockfalls", [])),
            "system_status": status.get("system_status"),
            "detections": detections,
            "measured": measured,
            "reasons": _reason_codes(status, 8),
        }
        checks = {
            "risk_match": actual["risk"] == scene.expected_risk,
            "pass_match": actual["pass"] == scene.expected_pass,
            "detected_match": bool(actual["rock_count"]) == scene.expected_detected,
            "remaining_diff_cm": (
                None if remaining_m is None
                else round(remaining_m * 100 - scene.expected_remaining_cm, 2)),
        }
        row = {
            "scene": scene,
            "frame_id": frame_id,
            "metadata": metadata,
            "rgb": rgb,
            "rgb_bytes": rgb_bytes,
            "depth": depth,
            "depth_bytes": depth_bytes,
            "conf_bytes": conf_bytes,
            "report": report,
            "status": status,
            "measured": measured,
            "actual": actual,
            "checks": checks,
            "scene_dir": scene_dir,
            "overlay": overlay,
            "overlay_zoom": overlay_zoom,
            "depth_img": depth_img,
            "depth_zoom": depth_zoom,
        }
        rows.append(row)
        print(f"[{index + 1}/{len(scenes)}] {scene.scene_id:14s} "
              f"risk={actual['risk']:7s} pass={actual['pass']:10s} "
              f"remaining={_fmt_m(remaining_m)} rocks={actual['rock_count']}")

    engine.close()

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "synthetic": True,
        "notice": "输入为合成 RGB-D；感知流水线与 C++ 风险引擎为真实代码。仅用于汇报演示，不代表真实检测能力。",
        "setup": {
            "board_cm": [BOARD_W_CM, BOARD_H_CM],
            "camera_height_m": CAMERA_H_M,
            "road_width_cm": ROAD_W_CM,
            "lane_width_cm": LANE_W_CM,
            "rgb_resolution": [RGB_W, RGB_H],
            "depth_resolution": [DEPTH_W, DEPTH_H],
            "depth_pixel_mm": round(BOARD_W_CM * 10 / DEPTH_W, 2),
            "policy": str(args.policy),
            "policy_version": 3,
        },
        "scenes": [],
    }
    for row in rows:
        scene = row["scene"]
        summary["scenes"].append({
            "scene_id": scene.scene_id,
            "title": scene.title,
            "note": scene.note,
            "frame_id": row["frame_id"],
            "expected": {
                "detected": scene.expected_detected,
                "risk": scene.expected_risk,
                "passability": scene.expected_pass,
                "remaining_cm": scene.expected_remaining_cm,
            },
            "actual": row["actual"],
            "checks": row["checks"],
        })
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    db_command = None
    if not args.no_db:
        db_path = publish_db(out, rows, args.policy, args.engine)
        db_command = (
            f"ROAD_DB={out / 'demo.db'} \\\n"
            f"ROAD_ARTIFACTS_DIR={out / 'artifacts'} \\\n"
            "ROAD_PORT=8001 ROAD_STALE_AFTER_S=86400 ROAD_DEVICE_OFFLINE_S=86400 \\\n"
            "ROAD_ARTIFACT_HOURS=72 ROAD_RETENTION_HOURS=72 \\\n"
            "perception/.venv/bin/python -m uvicorn backend.app.main:app "
            "--host 0.0.0.0 --port 8001"
        )
        print(f"演示数据库: {db_path}")

    write_summary(out, rows, db_command)
    make_contact_sheet(rows, out / "contact_sheet.png")
    block_row = next((r for r in rows if r["scene"].scene_id == "05_block_2cm"), rows[0])
    make_pipeline_figure(block_row, out / "figure_pipeline.png")

    print("")
    print(f"输出目录: {out}")
    print(f"  总览图: {out / 'contact_sheet.png'}")
    print(f"  链路图: {out / 'figure_pipeline.png'}")
    print(f"  说明  : {out / 'summary.md'}")
    if db_command:
        print("")
        print("启动演示后端（不需要 AI worker）：")
        print(db_command)
        print("打开 http://127.0.0.1:8001/ 与 http://127.0.0.1:8001/#/admin")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
