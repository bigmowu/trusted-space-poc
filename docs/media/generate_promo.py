"""Generate the trusted-space promo GIF/WebP from verified prototype evidence.

The animation is intentionally rendered from code rather than screen capture.
All displayed watermark and sandbox values are taken from real prototype runs.
"""

from __future__ import annotations

import math
import os
import random
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


W, H = 1280, 720
SCALE = 2
W2, H2 = W * SCALE, H * SCALE
FPS = 10
OUT_DIR = Path(__file__).resolve().parent

CN = "/System/Library/Fonts/Hiragino Sans GB.ttc"
MONO = "/System/Library/Fonts/Menlo.ttc"

THEME = os.getenv("PROMO_THEME", "light").strip().lower()
IS_LIGHT = THEME != "dark"

if IS_LIGHT:
    WHITE = (16, 35, 56)
    TEXT = (42, 61, 84)
    MUTED = (94, 113, 137)
    DIM = (133, 150, 171)
    TEAL = (9, 138, 121)
    MINT = (8, 170, 137)
    BLUE = (37, 99, 235)
    INDIGO = (99, 86, 211)
    PINK = (211, 57, 100)
    AMBER = (184, 117, 16)
    CARD = (255, 255, 255)
    CARD2 = (245, 250, 255)
    DARK = (235, 244, 252)
    BORDER = (207, 222, 238)
    SHADOW = (30, 64, 98)
    SHADOW_ALPHA = 0.10
    TERMINAL_BG = (250, 253, 255)
    TERMINAL_HEADER = (230, 243, 250)
    TERMINAL_BORDER = (190, 214, 226)
    NOTE_BG = (244, 250, 255)
    NOTE_BORDER = (204, 224, 236)
    GATE_BG = (242, 251, 247)
    GATE_BORDER = (185, 226, 213)
    BOTTOM_BG = (246, 251, 255)
    BOTTOM_BORDER = (207, 226, 238)
    IDENTITY_BG = (242, 252, 248)
    IDENTITY_BORDER = (178, 224, 211)
    GRID_BG = (248, 252, 255)
    GRID_BORDER = (204, 222, 238)
    SAMPLES_BG = (246, 251, 255)
    SAMPLES_BORDER = (204, 222, 238)
    PALE_CARD = (250, 253, 255)
    PALE_CARD_BORDER = (205, 223, 238)
    TILE_BG = (238, 247, 252)
    TILE_BORDER = (206, 224, 238)
    METRIC_BG = (246, 250, 255)
    ICON_BG = (226, 246, 241)
    BADGE_BG = (226, 246, 241)
    ACTIVE_BLOCK_BG = (236, 251, 247)
    SAMPLE_CARD_BORDER = (202, 220, 238)
    FAST_BORDER = (188, 224, 216)
    APPROVAL_BORDER = (205, 207, 241)
    UNDERLINE = (219, 230, 242)
    DASH_BORDER = (185, 204, 222)
    TAG_BG = (226, 243, 239)
    INACTIVE_DOT = (207, 221, 235)
    FOOTER_LINE = (214, 227, 240)
    FOOTER_TEXT = (113, 132, 154)
    SEPARATOR = (211, 226, 238)
    INTRO_INNER = (235, 249, 246)
    RING_TRACK = (215, 229, 240)
    DOT_COLOR = (101, 164, 194)
    PAGE_BG = (248, 252, 255)
else:
    WHITE = (239, 247, 255)
    TEXT = (219, 233, 249)
    MUTED = (137, 160, 188)
    DIM = (86, 108, 136)
    TEAL = (54, 231, 199)
    MINT = (142, 255, 225)
    BLUE = (82, 151, 255)
    INDIGO = (128, 112, 255)
    PINK = (255, 107, 151)
    AMBER = (255, 190, 92)
    CARD = (11, 26, 47)
    CARD2 = (14, 33, 58)
    DARK = (5, 13, 27)
    BORDER = (38, 67, 101)
    SHADOW = (0, 0, 0)
    SHADOW_ALPHA = 0.22
    TERMINAL_BG = (7, 17, 33)
    TERMINAL_HEADER = (16, 35, 60)
    TERMINAL_BORDER = (44, 73, 106)
    NOTE_BG = (9, 24, 43)
    NOTE_BORDER = (35, 69, 92)
    GATE_BG = (9, 26, 43)
    GATE_BORDER = (44, 82, 94)
    BOTTOM_BG = (8, 24, 43)
    BOTTOM_BORDER = (39, 76, 96)
    IDENTITY_BG = (8, 28, 45)
    IDENTITY_BORDER = (42, 91, 98)
    GRID_BG = (8, 22, 40)
    GRID_BORDER = (35, 68, 94)
    SAMPLES_BG = (8, 23, 42)
    SAMPLES_BORDER = (34, 68, 94)
    PALE_CARD = (9, 27, 47)
    PALE_CARD_BORDER = (36, 71, 100)
    TILE_BG = (14, 40, 64)
    TILE_BORDER = (43, 74, 104)
    METRIC_BG = (13, 36, 59)
    ICON_BG = (17, 56, 72)
    BADGE_BG = (22, 54, 67)
    ACTIVE_BLOCK_BG = (10, 30, 50)
    SAMPLE_CARD_BORDER = (45, 74, 114)
    FAST_BORDER = (42, 83, 104)
    APPROVAL_BORDER = (58, 74, 132)
    UNDERLINE = (45, 74, 104)
    DASH_BORDER = (58, 92, 124)
    TAG_BG = (41, 84, 105)
    INACTIVE_DOT = (35, 58, 84)
    FOOTER_LINE = (24, 46, 73)
    FOOTER_TEXT = (120, 144, 172)
    SEPARATOR = (38, 67, 99)
    INTRO_INNER = (10, 38, 56)
    RING_TRACK = (35, 65, 96)
    DOT_COLOR = (91, 180, 205)
    PAGE_BG = (4, 10, 21)

SCENES = [
    ("INTRO", 0.0, 3.0),
    ("SANDBOX FACTS", 3.0, 6.8),
    ("EXPORT GATE", 6.8, 10.1),
    ("WATERMARK", 10.1, 14.7),
    ("EVIDENCE", 14.7, 18.5),
    ("TRACE", 18.5, 21.7),
    ("OPEN SOURCE", 21.7, 25.2),
    ("ALIBABA CLOUD", 25.2, 29.5),
    ("OUTRO", 29.5, 32.5),
]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def smooth(value: float) -> float:
    value = clamp(value)
    return value * value * (3 - 2 * value)


def ease_out(value: float) -> float:
    value = clamp(value)
    return 1 - (1 - value) ** 3


def appear(local: float, start: float, duration: float = 0.42) -> float:
    return ease_out((local - start) / duration)


def rgba(color: tuple[int, int, int], alpha: float = 1.0) -> tuple[int, int, int, int]:
    return (*color, int(255 * clamp(alpha)))


@lru_cache(maxsize=128)
def font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    if weight == "mono":
        return ImageFont.truetype(MONO, size * SCALE)
    index = 2 if weight == "bold" else 0
    return ImageFont.truetype(CN, size * SCALE, index=index)


def sc(value: float) -> int:
    return int(round(value * SCALE))


def point(x: float, y: float) -> tuple[int, int]:
    return sc(x), sc(y)


def box(values: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return tuple(sc(value) for value in values)  # type: ignore[return-value]


def text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    value: str,
    size: int = 18,
    color: tuple[int, int, int] = TEXT,
    alpha: float = 1.0,
    weight: str = "regular",
    anchor: str = "la",
) -> None:
    draw.text(point(*xy), value, font=font(size, weight), fill=rgba(color, alpha), anchor=anchor)


def text_width(
    draw: ImageDraw.ImageDraw,
    value: str,
    size: int,
    weight: str = "regular",
) -> float:
    bounds = draw.textbbox((0, 0), value, font=font(size, weight))
    return (bounds[2] - bounds[0]) / SCALE


def card(
    draw: ImageDraw.ImageDraw,
    values: tuple[float, float, float, float],
    *,
    fill: tuple[int, int, int] = CARD,
    outline: tuple[int, int, int] = BORDER,
    alpha: float = 1.0,
    radius: float = 18,
    width: float = 1.0,
    shadow: bool = True,
) -> None:
    if shadow:
        sx1, sy1, sx2, sy2 = values
        draw.rounded_rectangle(
            box((sx1 + 3, sy1 + 7, sx2 + 3, sy2 + 9)),
            radius=sc(radius),
            fill=rgba(SHADOW, SHADOW_ALPHA * alpha),
        )
    draw.rounded_rectangle(
        box(values),
        radius=sc(radius),
        fill=rgba(fill, alpha),
        outline=rgba(outline, alpha),
        width=max(1, sc(width)),
    )


def line(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    color: tuple[int, int, int],
    width: float = 2,
    alpha: float = 1.0,
) -> None:
    draw.line((point(*start), point(*end)), fill=rgba(color, alpha), width=max(1, sc(width)))


def arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    color: tuple[int, int, int],
    alpha: float = 1.0,
    width: float = 2.2,
) -> None:
    line(draw, start, end, color, width, alpha)
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    size = 8
    p1 = (end[0] - size * math.cos(angle - 0.55), end[1] - size * math.sin(angle - 0.55))
    p2 = (end[0] - size * math.cos(angle + 0.55), end[1] - size * math.sin(angle + 0.55))
    draw.polygon((point(*end), point(*p1), point(*p2)), fill=rgba(color, alpha))


def pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    value: str,
    *,
    color: tuple[int, int, int] = TEAL,
    size: int = 12,
    alpha: float = 1.0,
    mono: bool = True,
) -> float:
    weight = "mono" if mono else "regular"
    width = text_width(draw, value, size, weight)
    x, y = xy
    draw.rounded_rectangle(
        box((x, y, x + width + 24, y + size + 12)),
        radius=sc(10),
        fill=rgba(color, 0.10 * alpha),
        outline=rgba(color, 0.35 * alpha),
        width=max(1, sc(1)),
    )
    text(draw, (x + 12, y + 6), value, size, color, alpha, weight)
    return width + 24


def check(draw: ImageDraw.ImageDraw, x: float, y: float, size: float, color: tuple[int, int, int], alpha: float) -> None:
    draw.ellipse(box((x, y, x + size, y + size)), fill=rgba(color, 0.14 * alpha), outline=rgba(color, 0.7 * alpha), width=sc(1))
    draw.line(
        (point(x + size * 0.25, y + size * 0.52), point(x + size * 0.43, y + size * 0.70), point(x + size * 0.76, y + size * 0.31)),
        fill=rgba(color, alpha),
        width=max(1, sc(1.7)),
        joint="curve",
    )


def hexagon(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    radius: float,
    color: tuple[int, int, int],
    alpha: float = 1.0,
    width: float = 2.0,
    fill_alpha: float = 0.0,
) -> list[tuple[float, float]]:
    points = [
        (cx + radius * math.cos(math.radians(-90 + 60 * index)), cy + radius * math.sin(math.radians(-90 + 60 * index)))
        for index in range(6)
    ]
    draw.polygon([point(*item) for item in points], fill=rgba(color, fill_alpha * alpha), outline=rgba(color, alpha), width=max(1, sc(width)))
    return points


def brand(draw: ImageDraw.ImageDraw, alpha: float = 1.0) -> None:
    hexagon(draw, 82, 54, 15, TEAL, alpha, 1.5, 0.08)
    draw.line((point(76, 55), point(81, 60), point(90, 48)), fill=rgba(TEAL, alpha), width=sc(2), joint="curve")
    text(draw, (108, 37), "可信数据空间", 15, WHITE, alpha, "bold")
    text(draw, (108, 58), "TRUSTED DATA SPACE", 8, DIM, alpha, "mono")


def chrome(image: Image.Image, global_time: float) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    brand(draw, 1.0)
    total = SCENES[-1][2]
    progress = clamp(global_time / total)
    line(draw, (88, 681), (1192, 681), FOOTER_LINE, 1.2, 1.0)
    line(draw, (88, 681), (88 + 1104 * progress, 681), TEAL, 1.6, 0.95)
    draw.ellipse(box((82 + 1104 * progress, 676, 94 + 1104 * progress, 688)), fill=rgba(TEAL, 1.0))
    text(draw, (88, 690), "基于真实运行数据生成 · 非屏幕录制", 9, FOOTER_TEXT, 1.0, "regular")
    text(draw, (1192, 690), "LOCAL PROTOTYPE · 127.0.0.1:8000", 9, FOOTER_TEXT, 1.0, "mono", "ra")
    x = 1040
    for index, (_, _, _) in enumerate(SCENES):
        active = index == scene_index(global_time)
        draw.rounded_rectangle(
            box((x + index * 17, 34, x + index * 17 + 10, 38)),
            radius=sc(2),
            fill=rgba(TEAL if active else INACTIVE_DOT, 1.0 if active else 0.8),
        )


def scene_index(global_time: float) -> int:
    for index, (_, start, end) in enumerate(SCENES):
        if start <= global_time < end:
            return index
    return len(SCENES) - 1


def scene_local(global_time: float) -> tuple[int, float, float]:
    index = scene_index(global_time)
    _, start, end = SCENES[index]
    return index, global_time - start, end - start


def make_background() -> Image.Image:
    y = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    base = np.zeros((H, W, 3), dtype=np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    if IS_LIGHT:
        base[:, :, 0] = 250 - 12 * y
        base[:, :, 1] = 253 - 8 * y
        base[:, :, 2] = 255 - 3 * y
        glows = [
            (210, 90, 540, (188, 238, 246), 0.52),
            (1090, 120, 590, (217, 228, 255), 0.58),
            (1010, 620, 460, (206, 245, 233), 0.35),
            (720, 60, 420, (255, 241, 224), 0.22),
        ]
        for cx, cy, radius, color, strength in glows:
            distance = ((xx - cx) ** 2 + (yy - cy) ** 2) / (radius**2)
            weight = np.clip(np.exp(-distance * 2.4) * strength, 0, 0.75)[..., None]
            base = base * (1 - weight) + np.array(color, dtype=np.float32) * weight
    else:
        base[:, :, 0] = 4 + 5 * y
        base[:, :, 1] = 10 + 13 * y
        base[:, :, 2] = 21 + 24 * y
        glows = [
            (210, 100, 520, (20, 90, 120), 0.32),
            (1080, 130, 560, (30, 55, 140), 0.32),
            (1020, 610, 440, (9, 95, 92), 0.17),
        ]
        for cx, cy, radius, color, strength in glows:
            distance = ((xx - cx) ** 2 + (yy - cy) ** 2) / (radius**2)
            weight = np.exp(-distance * 2.4) * strength
            for channel in range(3):
                base[:, :, channel] += color[channel] * weight

    rng = np.random.default_rng(28)
    noise = rng.normal(0, 0.7 if IS_LIGHT else 1.3, (H, W, 1)).astype(np.float32)
    base = np.clip(base + noise, 0, 255).astype(np.uint8)
    image = Image.fromarray(base, "RGB").resize((W2, H2), Image.Resampling.LANCZOS).convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")

    for x in range(0, W2, 64 * SCALE):
        draw.line((x, 0, x, H2), fill=(150, 179, 207, 26 if IS_LIGHT else 16), width=1)
    for yline in range(0, H2, 64 * SCALE):
        draw.line((0, yline, W2, yline), fill=(150, 179, 207, 26 if IS_LIGHT else 16), width=1)

    stars = random.Random(42)
    for _ in range(48 if IS_LIGHT else 85):
        x = stars.randrange(0, W2)
        y = stars.randrange(0, H2)
        radius = stars.choice([1, 1, 1, 2]) * SCALE
        alpha = stars.randrange(10, 35) if IS_LIGHT else stars.randrange(20, 80)
        draw.ellipse((x, y, x + radius, y + radius), fill=(*DOT_COLOR, alpha))

    # A soft horizon keeps the composition grounded in both themes.
    if IS_LIGHT:
        draw.ellipse(box((-300, 610, 1580, 980)), fill=(225, 239, 250, 145))
        draw.rectangle(box((0, 660, 1280, 720)), fill=(248, 252, 255, 232))
        draw.line(box((0, 660, 1280, 660)), fill=(216, 229, 241, 150), width=sc(1))
    else:
        draw.ellipse(box((-300, 610, 1580, 980)), fill=(6, 15, 29, 140))
        draw.rectangle(box((0, 660, 1280, 720)), fill=(4, 10, 21, 205))
    return image


BASE = make_background()


def draw_intro(draw: ImageDraw.ImageDraw, local: float, duration: float) -> None:
    e1 = appear(local, 0.05, 0.6)
    e2 = appear(local, 0.22, 0.65)
    e3 = appear(local, 0.48, 0.65)
    text(draw, (88, 145 + 10 * (1 - e1)), "TRUSTED DATA SPACE  /  LOCAL PROTOTYPE", 12, TEAL, e1, "mono")
    text(draw, (88, 185 + 18 * (1 - e2)), "可信数据空间", 72, WHITE, e2, "bold")
    text(draw, (92, 288 + 14 * (1 - e3)), "让数据可用，不可失控", 31, TEXT, e3, "regular")
    draw.rounded_rectangle(box((92, 342, 258, 346)), radius=sc(2), fill=rgba(TEAL, 0.9 * e3))
    text(draw, (92, 370 + 10 * (1 - e3)), "隔离执行  ·  分级审批  ·  强制水印  ·  全链路溯源", 18, MUTED, e3)

    x = 92.0
    for index, label in enumerate(("FastAPI", "Mock / Docker / OpenSandbox", "SHA256 + LSB + CRC32")):
        alpha = appear(local, 0.9 + index * 0.12, 0.4)
        x += pill(draw, (x, 438), label, color=(TEAL, BLUE, INDIGO)[index], size=11, alpha=alpha) + 10

    cx, cy = 1002, 325
    pulse = 1 + 0.025 * math.sin(local * 4.2)
    for index, radius in enumerate((154, 118, 82)):
        alpha = (0.32, 0.5, 0.9)[index]
        draw.ellipse(box((cx - radius * pulse, cy - radius * pulse, cx + radius * pulse, cy + radius * pulse)), outline=rgba(TEAL if index == 2 else BLUE, alpha), width=sc(1.1))
    for offset, color in ((0, TEAL), (120, BLUE), (238, INDIGO)):
        start = offset + local * 38
        draw.arc(box((cx - 154, cy - 154, cx + 154, cy + 154)), start, start + 54, fill=rgba(color, 0.75), width=sc(2.4))
    ring_alpha = appear(local, 0.2, 0.8)
    draw.ellipse(box((cx - 70, cy - 70, cx + 70, cy + 70)), fill=rgba(INTRO_INNER, 0.92 * ring_alpha), outline=rgba(TEAL, 0.65 * ring_alpha), width=sc(1.5))
    hexagon(draw, cx, cy, 52, TEAL, ring_alpha, 2.4, 0.06)
    draw.line((point(cx - 20, cy + 2), point(cx - 5, cy + 17), point(cx + 26, cy - 18)), fill=rgba(MINT, ring_alpha), width=sc(4), joint="curve")
    text(draw, (cx, cy + 92), "VERIFIED", 9, TEAL, ring_alpha, "mono", "ma")

    line(draw, (632, 248), (808, 270), BLUE, 1.1, 0.28 * e2)
    draw.ellipse(box((801, 266, 815, 280)), fill=rgba(TEAL, 0.8 * e2))


def draw_sandbox(draw: ImageDraw.ImageDraw, local: float, duration: float) -> None:
    head = appear(local, 0.02, 0.55)
    text(draw, (88, 98 + 12 * (1 - head)), "沙箱不是一句 RUNNING", 44, WHITE, head, "bold")
    text(draw, (89, 158 + 8 * (1 - head)), "把“存在”拆成五份可独立核对的证据", 17, MUTED, appear(local, 0.14, 0.5))

    rows = [
        ("database.record", "sandboxes.status = RUNNING", "DB"),
        ("database.running", "status=RUNNING", "DB"),
        ("process.registry", "registered in FastAPI process", "PY"),
        ("runtime.live", "MockSandbox · on-demand", "RT"),
        ("storage.volumes", "raw 2 files · work 1 file", "FS"),
    ]
    for index, (name, detail, tag) in enumerate(rows):
        alpha = appear(local, 0.28 + index * 0.28, 0.42)
        y = 218 + index * 70 + (1 - alpha) * 13
        selected = index == 3
        card(draw, (88, y, 716, y + 58), fill=CARD2 if selected else CARD, outline=TEAL if selected else BORDER, alpha=alpha, radius=13, width=1.4 if selected else 1)
        check(draw, 106, y + 16, 25, TEAL, alpha)
        text(draw, (148, y + 11), name, 14, WHITE, alpha, "mono")
        text(draw, (148, y + 32), detail, 11, MUTED, alpha)
        draw.rounded_rectangle(box((664, y + 18, 694, y + 40)), radius=sc(5), fill=rgba(TAG_BG, 0.78 * alpha))
        text(draw, (679, y + 24), tag, 9, TEAL, alpha, "mono", "ma")

    terminal_alpha = appear(local, 0.45, 0.55)
    card(draw, (776, 218, 1192, 566), fill=TERMINAL_BG, outline=TERMINAL_BORDER, alpha=terminal_alpha, radius=16)
    draw.rounded_rectangle(box((776, 218, 1192, 250)), radius=sc(16), fill=rgba(TERMINAL_HEADER, terminal_alpha))
    for index, color in enumerate((PINK, AMBER, TEAL)):
        draw.ellipse(box((797 + index * 15, 229, 805 + index * 15, 237)), fill=rgba(color, 0.75 * terminal_alpha))
    text(draw, (850, 229), "GET /api/sandbox/{id}/status", 9, MUTED, terminal_alpha, "mono")
    text(draw, (800, 274), "SCOPE", 10, DIM, terminal_alpha, "mono")
    text(draw, (878, 274), "service_process", 11, BLUE, terminal_alpha, "mono")
    text(draw, (800, 306), "RUNNING_VERIFIED", 29, TEAL, terminal_alpha, "mono")
    line(draw, (800, 350), (1168, 350), SEPARATOR, 1, terminal_alpha)
    lines = [
        ("CHECKS", "5 / 5", TEAL),
        ("MODE", "on_demand", BLUE),
        ("PID", "—  (on demand)", AMBER),
        ("STORAGE", "raw + work", TEAL),
    ]
    for index, (key, value, color) in enumerate(lines):
        y = 378 + index * 38
        text(draw, (800, y), key, 10, DIM, terminal_alpha, "mono")
        text(draw, (918, y), value, 12, color, terminal_alpha, "mono")
    text(draw, (800, 527), "→ 控制面证据成立", 10, MUTED, terminal_alpha)

    note_alpha = appear(local, 1.75, 0.5)
    card(draw, (88, 585, 1192, 648), fill=NOTE_BG, outline=NOTE_BORDER, alpha=note_alpha, radius=12, shadow=False)
    text(draw, (108, 600), "CLI 是独立进程：只能核对 DB + 卷；运行时事实必须以服务端 API 为准。", 12, MUTED, note_alpha)
    text(draw, (108, 621), "Mock 没有长驻 PID，执行时按需启动 python3；Docker 才检查容器 State.Running。", 10, DIM, note_alpha)


def draw_approval(draw: ImageDraw.ImageDraw, local: float, duration: float) -> None:
    head = appear(local, 0.02, 0.52)
    text(draw, (88, 92 + 12 * (1 - head)), "出域不是下载，是受控交付", 44, WHITE, head, "bold")
    text(draw, (89, 151 + 8 * (1 - head)), "分级决定路径；只有 approved 能进入水印导出", 17, MUTED, appear(local, 0.14, 0.5))

    fast_alpha = appear(local, 0.25, 0.5)
    card(draw, (88, 215, 1192, 326), fill=CARD, outline=FAST_BORDER, alpha=fast_alpha, radius=16)
    text(draw, (112, 238), "FAST", 13, TEAL, fast_alpha, "mono")
    text(draw, (112, 262), "L1 / L2", 19, WHITE, fast_alpha, "bold")
    text(draw, (112, 292), "低敏数据", 9, MUTED, fast_alpha)
    fast_nodes = [("申请", 310), ("策略判定", 505), ("APPROVED", 760)]
    for index, (label, x) in enumerate(fast_nodes):
        alpha = fast_alpha * appear(local, 0.45 + index * 0.28, 0.4)
        color = TEAL if index == 2 else BLUE
        draw.ellipse(box((x, 254, x + 34, 288)), fill=rgba(color, 0.12 * alpha), outline=rgba(color, 0.8 * alpha), width=sc(1.3))
        check(draw, x + 5, 259, 24, color, alpha)
        text(draw, (x + 17, 306), label, 10, color, alpha, "mono" if index == 2 else "regular", "ma")
    arrow(draw, (353, 271), (487, 271), BLUE, 0.65 * fast_alpha)
    arrow(draw, (576, 271), (735, 271), TEAL, 0.65 * fast_alpha)
    text(draw, (840, 258), "自动放行", 14, MUTED, fast_alpha)
    text(draw, (840, 282), "status = approved", 10, DIM, fast_alpha, "mono")

    approval_alpha = appear(local, 0.65, 0.52)
    card(draw, (88, 343, 1192, 522), fill=CARD2, outline=APPROVAL_BORDER, alpha=approval_alpha, radius=16)
    text(draw, (112, 366), "APPROVAL", 13, INDIGO, approval_alpha, "mono")
    text(draw, (112, 390), "L3 / L4", 19, WHITE, approval_alpha, "bold")
    text(draw, (112, 420), "高敏数据 · 三级审批", 9, MUTED, approval_alpha)
    approval_nodes = [("Owner", 318, BLUE), ("Security", 527, INDIGO), ("Compliance", 747, PINK), ("APPROVED", 980, TEAL)]
    stage = min(3, int(max(0.0, local - 0.75) / 0.38))
    for index, (label, x, color) in enumerate(approval_nodes):
        revealed = approval_alpha * appear(local, 0.9 + index * 0.25, 0.42)
        active = index <= stage
        draw.ellipse(box((x, 382, x + 42, 424)), fill=rgba(color, (0.16 if active else 0.05) * revealed), outline=rgba(color, (0.95 if active else 0.35) * revealed), width=sc(1.8 if active else 1.1))
        if active:
            check(draw, x + 8, 390, 26, color, revealed)
        else:
            text(draw, (x + 21, 403), str(index + 1), 11, DIM, revealed, "mono", "mm")
        text(draw, (x + 21, 440), label, 11, color if active else DIM, revealed, "mono" if index == 3 else "regular", "ma")
        if index < 3:
            arrow(draw, (x + 52, 403), (approval_nodes[index + 1][1] - 12, 403), color, (0.72 if index < stage else 0.25) * revealed)
    glow_alpha = approval_alpha * appear(local, 2.7, 0.5)
    draw.ellipse(box((958, 360, 1044, 446)), outline=rgba(TEAL, 0.18 * glow_alpha), width=sc(7))

    gate_alpha = appear(local, 1.75, 0.55)
    card(draw, (88, 548, 1192, 648), fill=GATE_BG, outline=GATE_BORDER, alpha=gate_alpha, radius=14, shadow=False)
    text(draw, (112, 565), "export.execute", 13, TEAL, gate_alpha, "mono")
    text(draw, (112, 589), "强制调用 watermark-engine → 写入 exports/ → 登记映射", 13, TEXT, gate_alpha)
    text(draw, (112, 614), "未通过审批直接执行：HTTP 403 · 不产生文件", 10, PINK, gate_alpha, "regular")
    draw.rounded_rectangle(box((980, 571, 1164, 620)), radius=sc(9), fill=rgba(BADGE_BG, 0.9 * gate_alpha), outline=rgba(TEAL, 0.4 * gate_alpha), width=sc(1))
    text(draw, (1072, 583), "SINGLE EXIT", 9, DIM, gate_alpha, "mono", "ma")
    text(draw, (1072, 601), "WATERMARKED", 12, TEAL, gate_alpha, "mono", "ma")


def draw_watermark(draw: ImageDraw.ImageDraw, local: float, duration: float) -> None:
    head = appear(local, 0.02, 0.52)
    text(draw, (88, 82 + 12 * (1 - head)), "水印不是一个编号", 44, WHITE, head, "bold")
    text(draw, (89, 140 + 8 * (1 - head)), "而是一条能逐位复算的编码链", 17, MUTED, appear(local, 0.13, 0.5))

    blocks = [
        ("01", "PAYLOAD", "JSON 149 B"),
        ("02", "COMPRESS", "ZLIB 125 B"),
        ("03", "FRAME", "134 B · CRC32"),
        ("04", "REPEAT", "1072 × 3 = 3216"),
        ("05", "LSB WRITE", "even=0 · odd=1"),
    ]
    block_width = 201
    gap = 27
    for index, (number, label, value) in enumerate(blocks):
        alpha = appear(local, 0.18 + index * 0.32, 0.42)
        x = 88 + index * (block_width + gap)
        active = index == min(4, int(max(0.0, local - 0.45) / 0.75))
        y = 192 - (5 if active else 0)
        card(draw, (x, y, x + block_width, y + 86), fill=ACTIVE_BLOCK_BG if active else CARD, outline=TEAL if active else BORDER, alpha=alpha, radius=13, width=1.5 if active else 1)
        text(draw, (x + 15, y + 12), number, 9, TEAL if active else DIM, alpha, "mono")
        text(draw, (x + 15, y + 29), label, 11, WHITE, alpha, "mono")
        text(draw, (x + 15, y + 54), value, 12, TEAL if active else TEXT, alpha, "mono")
        if index < 4:
            arrow(draw, (x + block_width + 5, 235), (x + block_width + gap - 6, 235), TEAL if index < 4 else BLUE, 0.6 * alpha, 1.5)

    grid_alpha = appear(local, 0.85, 0.5)
    card(draw, (88, 310, 796, 566), fill=GRID_BG, outline=GRID_BORDER, alpha=grid_alpha, radius=15)
    text(draw, (108, 326), "SLOT MAP", 11, WHITE, grid_alpha, "mono")
    text(draw, (182, 326), "3216 个槽位 · 3 份冗余 · SHA256(customer_id) % 3216", 9, MUTED, grid_alpha, "regular")
    rng = random.Random(16)
    bit_rows = 8
    bit_cols = 29
    for row in range(bit_rows):
        for col in range(bit_cols):
            threshold = 0.95 * (row * bit_cols + col) / (bit_rows * bit_cols)
            if local < 1.0 + threshold * 1.8:
                continue
            bit = rng.randint(0, 1)
            x = 110 + col * 23
            y = 358 + row * 21
            color = TEAL if bit == 0 else BLUE
            alpha = 0.28 + bit * 0.45
            draw.rounded_rectangle(box((x, y, x + 15, y + 15)), radius=sc(2), fill=rgba(color, alpha * grid_alpha))
    highlight = [(2929, 0, PINK), (112, 0, AMBER), (2003, 1, INDIGO)]
    for slot, bit, color in highlight:
        row = slot % bit_rows
        col = (slot // bit_rows) % bit_cols
        x = 110 + col * 23
        y = 358 + row * 21
        draw.rounded_rectangle(box((x - 2, y - 2, x + 17, y + 17)), radius=sc(3), fill=rgba(color, 0.56 * grid_alpha), outline=rgba(WHITE, 0.72 * grid_alpha), width=sc(1))
    text(draw, (108, 537), "occupied 2552   ·   uncovered 664   ·   collisions 2448", 9, DIM, grid_alpha, "mono")

    sample_alpha = appear(local, 1.35, 0.55)
    card(draw, (820, 310, 1192, 566), fill=CARD2, outline=SAMPLE_CARD_BORDER, alpha=sample_alpha, radius=15)
    text(draw, (840, 328), "真实写入样本", 12, WHITE, sample_alpha, "bold")
    text(draw, (840, 349), "amount 只改 ±1", 9, MUTED, sample_alpha, "regular")
    samples = [
        ("C000001", "101 → 100", "LSB 1→0", "slot 2929", PINK),
        ("C000003", "103 → 102", "LSB 1→0", "slot 112", AMBER),
        ("C000004", "104 → 105", "LSB 0→1", "slot 2003", INDIGO),
    ]
    for index, (customer, amount, lsb, slot, color) in enumerate(samples):
        alpha = sample_alpha * appear(local, 1.5 + index * 0.2, 0.4)
        y = 382 + index * 56
        draw.rounded_rectangle(box((840, y, 1172, y + 45)), radius=sc(7), fill=rgba(TILE_BG, 0.82 * alpha))
        text(draw, (852, y + 7), customer, 11, color, alpha, "mono")
        text(draw, (852, y + 25), f"{amount}   {lsb}", 9, TEXT, alpha, "mono")
        text(draw, (1158, y + 16), slot, 9, MUTED, alpha, "mono", "ra")

    formula_alpha = appear(local, 2.25, 0.45)
    text(draw, (88, 588), "slot = int(SHA256(customer_id)[:8], 16) % 3216", 11, TEAL, formula_alpha, "mono")
    text(draw, (88, 610), "bit=0 → amount 偶数   ·   bit=1 → amount 奇数   ·   提取时逐行读回奇偶位，再做 3 份多数投票", 10, MUTED, formula_alpha)
    text(draw, (1192, 588), "CRC32  d93ea751", 10, AMBER, formula_alpha, "mono", "ra")


def draw_evidence(draw: ImageDraw.ImageDraw, local: float, duration: float) -> None:
    head = appear(local, 0.02, 0.5)
    text(draw, (88, 82 + 12 * (1 - head)), "证据不是截图，是可复算的数字", 42, WHITE, head, "bold")
    text(draw, (89, 139 + 8 * (1 - head)), "同一份导出文件，回读后仍能恢复身份与完整性", 17, MUTED, appear(local, 0.13, 0.5))

    panel_alpha = appear(local, 0.22, 0.55)
    card(draw, (88, 190, 808, 518), fill=CARD, outline=BORDER, alpha=panel_alpha, radius=16)
    text(draw, (112, 212), "WATERMARK ID", 9, DIM, panel_alpha, "mono")
    text(draw, (112, 230), "WM-20261028-321f7a4a", 28, TEAL, panel_alpha, "mono")
    text(draw, (112, 271), "REQUEST", 8, DIM, panel_alpha, "mono")
    text(draw, (112, 286), "EXP-20261028-BCAD8C", 11, TEXT, panel_alpha, "mono")
    text(draw, (354, 271), "EXPORT USER", 8, DIM, panel_alpha, "mono")
    text(draw, (354, 286), "alice", 13, WHITE, panel_alpha, "mono")
    text(draw, (112, 317), "ALGORITHM", 8, DIM, panel_alpha, "mono")
    text(draw, (112, 332), "sha256_hash_slot + amount_lsb + 3x_repeat + crc32", 9, MUTED, panel_alpha, "mono")

    progress = ease_out((local - 0.65) / 1.8)
    metrics = [
        ("ROWS", 5000, TEAL),
        ("CHANGED", 2583, BLUE),
        ("RATIO", 51.66, INDIGO),
        ("MAX Δ", 1, AMBER),
    ]
    for index, (label, target, color) in enumerate(metrics):
        alpha = panel_alpha * appear(local, 0.7 + index * 0.12, 0.42)
        x = 112 + index * 166
        draw.rounded_rectangle(box((x, 368, x + 150, 454)), radius=sc(10), fill=rgba(METRIC_BG, 0.92 * alpha), outline=rgba(TILE_BORDER, alpha), width=sc(1))
        value = int(round(target * progress))
        if label == "RATIO":
            value_text = f"{target * progress:.2f}%"
        else:
            value_text = f"{value:,}"
        text(draw, (x + 14, 382), label, 8, DIM, alpha, "mono")
        text(draw, (x + 14, 400), value_text, 24, color, alpha, "mono")

    response_alpha = panel_alpha * appear(local, 1.45, 0.45)
    text(draw, (112, 474), "watermark.embedded", 9, DIM, response_alpha, "mono")
    text(draw, (112, 490), "verified=true  ·  changed_rows=2583  ·  frame_bytes=134", 10, TEAL, response_alpha, "mono")

    ring_alpha = appear(local, 0.45, 0.55)
    card(draw, (830, 190, 1192, 518), fill=PALE_CARD, outline=PALE_CARD_BORDER, alpha=ring_alpha, radius=16)
    cx, cy, radius = 1011, 330, 80
    draw.arc(box((cx - radius, cy - radius, cx + radius, cy + radius)), -90, 270, fill=rgba(RING_TRACK, ring_alpha), width=sc(10))
    ring_value = 99.16 * ease_out((local - 0.7) / 1.75)
    draw.arc(box((cx - radius, cy - radius, cx + radius, cy + radius)), -90, -90 + 360 * ring_value / 100, fill=rgba(TEAL, ring_alpha), width=sc(10))
    text(draw, (cx, cy - 14), f"{ring_value:.2f}%", 26, WHITE, ring_alpha, "mono", "mm")
    text(draw, (cx, cy + 17), "MATCH", 8, MUTED, ring_alpha, "mono", "ma")
    text(draw, (856, 426), "unknown", 9, DIM, ring_alpha, "mono")
    text(draw, (1014, 426), "9", 12, AMBER, ring_alpha, "mono", "ra")
    text(draw, (856, 446), "corrected", 9, DIM, ring_alpha, "mono")
    text(draw, (1014, 446), "9", 12, TEAL, ring_alpha, "mono", "ra")
    text(draw, (856, 466), "CRC32", 9, DIM, ring_alpha, "mono")
    text(draw, (1014, 466), "verified", 11, TEAL, ring_alpha, "mono", "ra")

    samples_alpha = appear(local, 1.75, 0.55)
    card(draw, (88, 538, 1192, 648), fill=SAMPLES_BG, outline=SAMPLES_BORDER, alpha=samples_alpha, radius=14, shadow=False)
    text(draw, (112, 555), "逐行写入证据", 10, WHITE, samples_alpha, "bold")
    sample_data = [
        ("C000001", "101 → 100", "slot 2929 · replica 2", PINK),
        ("C000003", "103 → 102", "slot 112 · replica 0", AMBER),
        ("C000004", "104 → 105", "slot 2003 · replica 1", INDIGO),
    ]
    for index, (customer, change, detail, color) in enumerate(sample_data):
        x = 112 + index * 353
        text(draw, (x, 580), customer, 11, color, samples_alpha, "mono")
        text(draw, (x, 600), change, 15, WHITE, samples_alpha, "mono")
        text(draw, (x, 623), detail, 8, MUTED, samples_alpha, "mono")


def draw_trace(draw: ImageDraw.ImageDraw, local: float, duration: float) -> None:
    head = appear(local, 0.02, 0.52)
    text(draw, (88, 82 + 12 * (1 - head)), "从一份泄露文件，回到导出人", 44, WHITE, head, "bold")
    text(draw, (89, 140 + 8 * (1 - head)), "上传、提取、映射；七步时间线全部落库", 17, MUTED, appear(local, 0.13, 0.5))

    timeline_alpha = appear(local, 0.2, 0.5)
    labels = [("数据入湖", "INGEST"), ("创建沙箱", "SANDBOX"), ("生成文件", "GENERATE"), ("出域申请", "REQUEST"), ("审批放行", "APPROVE"), ("水印嵌入", "WATERMARK"), ("文件出域", "EXPORT")]
    start_x, end_x, y = 126, 1154, 226
    line(draw, (start_x, y), (end_x, y), UNDERLINE, 2, timeline_alpha)
    reveal = clamp((local - 0.35) / 0.9)
    line(draw, (start_x, y), (start_x + (end_x - start_x) * reveal, y), TEAL, 2.4, timeline_alpha)
    for index, (cn_label, en_label) in enumerate(labels):
        x = start_x + index * ((end_x - start_x) / 6)
        alpha = timeline_alpha * appear(local, 0.35 + index * 0.12, 0.35)
        active = local >= 0.35 + index * 0.12
        color = TEAL if active else DIM
        draw.ellipse(box((x - 15, y - 15, x + 15, y + 15)), fill=rgba(CARD, alpha), outline=rgba(color, alpha), width=sc(2))
        if active:
            draw.ellipse(box((x - 4, y - 4, x + 4, y + 4)), fill=rgba(color, alpha))
        text(draw, (x, y + 25), cn_label, 11, WHITE if active else DIM, alpha, "regular", "ma")
        text(draw, (x, y + 43), en_label, 7, color, alpha, "mono", "ma")

    file_alpha = appear(local, 0.55, 0.55)
    card(draw, (88, 330, 530, 574), fill=CARD, outline=BORDER, alpha=file_alpha, radius=16)
    text(draw, (112, 352), "LEAKED FILE", 9, DIM, file_alpha, "mono")
    draw.rounded_rectangle(box((112, 380, 505, 506)), radius=sc(12), outline=rgba(DASH_BORDER, file_alpha), width=sc(1.4))
    for xdash in range(126, 500, 18):
        draw.line((point(xdash, 380), point(min(xdash + 9, 505), 380)), fill=rgba(DASH_BORDER, file_alpha), width=sc(1))
        draw.line((point(xdash, 506), point(min(xdash + 9, 505), 506)), fill=rgba(DASH_BORDER, file_alpha), width=sc(1))
    draw.rounded_rectangle(box((142, 407, 210, 475)), radius=sc(10), fill=rgba(ICON_BG, file_alpha), outline=rgba(TEAL, 0.55 * file_alpha), width=sc(1))
    text(draw, (176, 425), "CSV", 16, TEAL, file_alpha, "mono", "ma")
    text(draw, (232, 414), "exported_customer.csv", 13, WHITE, file_alpha, "mono")
    text(draw, (232, 440), "210,035 bytes", 11, MUTED, file_alpha, "mono")
    text(draw, (232, 463), "SHA256 697fb7e8...", 9, DIM, file_alpha, "mono")
    arrow(draw, (306, 492), (392, 492), TEAL, 0.8 * file_alpha, 1.7)
    text(draw, (112, 531), "仅内存解析 · 不落盘", 9, MUTED, file_alpha, "regular")
    text(draw, (112, 550), "multipart upload → extract → resolve", 9, DIM, file_alpha, "mono")

    identity_alpha = appear(local, 1.35, 0.58)
    card(draw, (566, 330, 1192, 574), fill=IDENTITY_BG, outline=IDENTITY_BORDER, alpha=identity_alpha, radius=16)
    text(draw, (590, 352), "IDENTITY RESOLVED", 9, TEAL, identity_alpha, "mono")
    text(draw, (590, 374), "alice", 44, WHITE, identity_alpha, "bold")
    text(draw, (590, 432), "WM-20261028-321f7a4a", 14, TEAL, identity_alpha, "mono")
    text(draw, (590, 456), "EXP-20261028-BCAD8C · 2026-10-28 15:06:08", 10, MUTED, identity_alpha, "mono")
    x = 590
    for label, color in (("match 99.16%", TEAL), ("CRC verified", BLUE), ("unknown 9 → corrected 9", AMBER)):
        x += pill(draw, (x, 490), label, color=color, size=9, alpha=identity_alpha) + 8
    text(draw, (590, 540), "TRACE  TRC-20261028-11b4313c01", 9, DIM, identity_alpha, "mono")
    check(draw, 1146, 350, 26, TEAL, identity_alpha)

    note_alpha = appear(local, 2.05, 0.5)
    text(draw, (88, 604), "水印 ID 不是文件里的明文；它先由 amount 奇偶位恢复，再与映射库交叉验证。", 11, MUTED, note_alpha)
    text(draw, (88, 626), "同一份 trace_id 可串起审批人、导出时间、文件摘要、逐位证据与泄露样本。", 10, DIM, note_alpha)


def draw_open_source(draw: ImageDraw.ImageDraw, local: float, duration: float) -> None:
    head = appear(local, 0.02, 0.5)
    text(draw, (88, 82 + 12 * (1 - head)), "开源底座，组件可替换", 44, WHITE, head, "bold")
    text(draw, (89, 140 + 8 * (1 - head)), "从 API 到运行时都有明确边界，不把业务语义锁进某一家云", 17, MUTED, appear(local, 0.13, 0.5))

    items = [
        ("01", "FastAPI", "MIT", "API · 异步接口", TEAL),
        ("02", "Uvicorn", "BSD-3", "ASGI 服务", BLUE),
        ("03", "Pydantic", "MIT", "模型与请求校验", INDIGO),
        ("04", "SQLite", "PUBLIC DOMAIN", "元数据 · 可换 RDS", AMBER),
        ("05", "Docker", "APACHE-2.0", "容器运行时", BLUE),
        ("06", "OpenSandbox", "OPTIONAL", "远端隔离适配层", PINK),
        ("07", "Vanilla JS", "NO BUILD", "原生前端", TEAL),
        ("08", "Python stdlib", "ZLIB · HASHLIB", "水印算法基础", INDIGO),
    ]
    for index, (number, name, license_name, description, color) in enumerate(items):
        alpha = appear(local, 0.22 + index * 0.15, 0.4)
        x = 88 + (index % 4) * 274
        y = 190 + (index // 4) * 156 + (1 - alpha) * 12
        card(draw, (x, y, x + 256, y + 132), fill=CARD, outline=BORDER, alpha=alpha, radius=15)
        draw.rounded_rectangle(box((x + 16, y + 13, x + 43, y + 32)), radius=sc(5), fill=rgba(TAG_BG, 0.9 * alpha))
        text(draw, (x + 29, y + 18), number, 8, color, alpha, "mono", "ma")
        draw.ellipse(box((x + 235, y + 17, x + 243, y + 25)), fill=rgba(color, 0.8 * alpha))
        text(draw, (x + 16, y + 38), name, 18, WHITE, alpha, "bold")
        text(draw, (x + 16, y + 65), license_name, 8, color, alpha, "mono")
        text(draw, (x + 16, y + 88), description, 10, MUTED, alpha)
        draw.rounded_rectangle(box((x + 16, y + 112, x + 240, y + 113)), radius=sc(1), fill=rgba(color, 0.24 * alpha))

    note_alpha = appear(local, 1.45, 0.55)
    card(draw, (88, 506, 1192, 648), fill=NOTE_BG, outline=NOTE_BORDER, alpha=note_alpha, radius=15, shadow=False)
    text(draw, (112, 522), "替换点", 9, DIM, note_alpha, "regular")
    text(draw, (178, 522), "SANDBOX_BACKEND / DB repository / ObjectStore / LogSink / WatermarkEngine", 10, TEAL, note_alpha, "mono")
    text(draw, (112, 548), "前端无构建链；HTTP 错误码、审批状态与 trace_id 保持不变，云侧只替换基础设施实现。", 12, TEXT, note_alpha)
    text(draw, (112, 576), "诚实状态：OpenSandbox SDK 当前未安装，组件中心会返回 unavailable；不会把 adapter 存在伪装成远端已接通。", 10, MUTED, note_alpha)
    text(draw, (112, 601), "水印算法依赖 Python 标准库 hashlib / zlib / struct / csv，可在容器内独立运行。", 10, DIM, note_alpha)
    text(draw, (112, 624), "核对：GET /api/components · ./.venv/bin/python -m app.cli component sandbox-runtime --selftest", 9, BLUE, note_alpha, "regular")


def draw_aliyun(draw: ImageDraw.ImageDraw, local: float, duration: float) -> None:
    head = appear(local, 0.02, 0.5)
    text(draw, (88, 78 + 12 * (1 - head)), "阿里云落地：换基础设施，不换业务语义", 40, WHITE, head, "bold")
    text(draw, (89, 134 + 8 * (1 - head)), "目标架构 · 规划态；产品能力、配额与可用区以目标 Region 实际开通为准", 16, MUTED, appear(local, 0.13, 0.5))
    badge = appear(local, 0.35, 0.45)
    draw.rounded_rectangle(box((1002, 82, 1192, 112)), radius=sc(9), fill=rgba((252, 235, 241), 0.95 * badge), outline=rgba(PINK, 0.45 * badge), width=sc(1))
    text(draw, (1097, 90), "ROADMAP · NOT IMPLEMENTED", 8, PINK, badge, "mono", "ma")

    items = [
        ("ACK / ECI", "FastAPI + 沙箱 Job", "替换：DockerSandbox 本地进程", TEAL),
        ("RDS PostgreSQL", "元数据与审批状态", "替换：SQLite", BLUE),
        ("OSS + Object Lock", "导出文件 · 证据归档", "替换：本机 exports/", INDIGO),
        ("RAM / 企业 IdP", "身份 · 角色 · 审批人", "新增：OIDC / SAML + RBAC", AMBER),
        ("KMS", "密钥托管 · 证据签名", "新增：信封加密 / 轮换", PINK),
        ("SLS / ARMS", "事件 · trace · 指标", "替换：本地三份日志", TEAL),
    ]
    for index, (name, role, mapping, color) in enumerate(items):
        alpha = appear(local, 0.18 + index * 0.14, 0.4)
        x = 88 + (index % 3) * 366
        y = 176 + (index // 3) * 126 + (1 - alpha) * 10
        card(draw, (x, y, x + 348, y + 108), fill=CARD, outline=BORDER, alpha=alpha, radius=14)
        draw.rounded_rectangle(box((x + 14, y + 14, x + 18, y + 94)), radius=sc(2), fill=rgba(color, 0.9 * alpha))
        text(draw, (x + 30, y + 14), name, 16, WHITE, alpha, "bold")
        text(draw, (x + 30, y + 42), role, 11, TEXT, alpha)
        text(draw, (x + 30, y + 66), mapping, 9, MUTED, alpha, "regular")
        text(draw, (x + 324, y + 18), "→ CLOUD", 8, color, alpha, "mono", "ra")

    phase_alpha = appear(local, 1.2, 0.5)
    phases = [
        ("01", "抽象存储", "ObjectStore / Repository / LogSink"),
        ("02", "接入 ACK / ECI", "API Deployment + 沙箱 Job"),
        ("03", "安全与审计", "RAM · KMS · SLS · OSS Lock"),
    ]
    for index, (number, title_value, detail) in enumerate(phases):
        alpha = phase_alpha * appear(local, 1.35 + index * 0.16, 0.4)
        x = 88 + index * 376
        card(draw, (x, 452, x + 338, 532), fill=CARD2, outline=UNDERLINE, alpha=alpha, radius=13, shadow=False)
        text(draw, (x + 18, 466), number, 9, TEAL, alpha, "mono")
        text(draw, (x + 50, 464), title_value, 14, WHITE, alpha, "bold")
        text(draw, (x + 18, 496), detail, 9, MUTED, alpha, "regular")
        if index < 2:
            arrow(draw, (x + 344, 492), (x + 365, 492), TEAL, 0.6 * alpha, 1.5)

    note_alpha = appear(local, 1.9, 0.5)
    text(draw, (88, 552), "网络与数据边界：VPC 私网 · ALB/WAF 仅暴露 UI/API · 沙箱无公网出口 · OSS 临时凭证 · 不把桶直接可写挂进沙箱", 10, TEXT, note_alpha)
    text(draw, (88, 578), "OpenSandbox 路径：服务部署在私网，OPENSANDBOX_ADAPTER=module:factory；RAM/STS 签发短期凭证，健康探针与执行探针分离。", 10, MUTED, note_alpha)
    text(draw, (88, 604), "迁移顺序：先抽象后上云。保持 /api、状态机、水印载荷和 trace_id 不变，再逐项替换 SQLite、目录、日志和运行时。", 10, MUTED, note_alpha)
    text(draw, (88, 628), "这不是已上线承诺；具体产品规格、跨区复制、WORM 合规与配额需在阿里云目标账号做 PoC 验收。", 9, DIM, note_alpha)


def draw_outro(draw: ImageDraw.ImageDraw, local: float, duration: float) -> None:
    alpha = appear(local, 0.05, 0.65)
    hexagon(draw, 640, 137, 36, TEAL, alpha, 2.4, 0.05)
    draw.line((point(624, 139), point(636, 151), point(662, 121)), fill=rgba(MINT, alpha), width=sc(3.4), joint="curve")
    text(draw, (640, 193), "TRUSTED DATA SPACE", 10, TEAL, alpha, "mono", "ma")
    text(draw, (640, 230), "让每一次数据出域，都有证据", 49, WHITE, alpha, "bold", "ma")
    text(draw, (640, 300), "隔离执行  ·  分级审批  ·  强制水印  ·  全链路溯源", 19, MUTED, alpha, "regular", "ma")

    for index, (number, label) in enumerate((("01", "隔离执行"), ("02", "分级审批"), ("03", "强制水印"), ("04", "全链路溯源"))):
        item_alpha = alpha * appear(local, 0.55 + index * 0.12, 0.42)
        x = 190 + index * 238
        text(draw, (x, 385), number, 10, TEAL, item_alpha, "mono")
        text(draw, (x, 407), label, 16, TEXT, item_alpha, "regular")
        draw.rounded_rectangle(box((x, 438, x + 180, 440)), radius=sc(1), fill=rgba(TEAL if index == 2 else UNDERLINE, item_alpha))

    bottom_alpha = appear(local, 1.35, 0.55)
    card(draw, (270, 492, 1010, 584), fill=BOTTOM_BG, outline=BOTTOM_BORDER, alpha=bottom_alpha, radius=15, shadow=False)
    text(draw, (640, 507), "本地可运行  ·  开源底座  ·  真实 API  ·  云上可演进", 11, MUTED, bottom_alpha, "regular", "ma")
    text(draw, (640, 535), "http://localhost:8000", 21, TEAL, bottom_alpha, "mono", "ma")
    text(draw, (640, 563), "FastAPI · Uvicorn · Pydantic · SQLite · Docker · OpenSandbox(可选)", 8, DIM, bottom_alpha, "regular", "ma")


DRAWERS = [draw_intro, draw_sandbox, draw_approval, draw_watermark, draw_evidence, draw_trace, draw_open_source, draw_aliyun, draw_outro]


def render_frame(global_time: float) -> Image.Image:
    image = BASE.copy()
    index, local, duration = scene_local(global_time)
    layer = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    DRAWERS[index](draw, local, duration)
    fade_in = smooth(local / 0.38)
    fade_out = 1.0 if index == len(SCENES) - 1 else smooth((duration - local) / 0.28)
    opacity = min(fade_in, fade_out)
    if opacity < 0.999:
        channel = layer.getchannel("A").point(lambda value: int(value * opacity))
        layer.putalpha(channel)
    image.alpha_composite(layer)
    chrome(image, global_time)
    return image.convert("RGB").resize((W, H), Image.Resampling.LANCZOS)


def main() -> None:
    total = SCENES[-1][2]
    frame_count = int(round(total * FPS))
    sample_times = [start + (end - start) * ratio for _, start, end in SCENES for ratio in (0.35, 0.72)]
    thumbs = [render_frame(time_value).resize((192, 108), Image.Resampling.LANCZOS) for time_value in sample_times]
    cols = 4
    rows = math.ceil(len(thumbs) / cols)
    mosaic = Image.new("RGB", (192 * cols, 108 * rows), PAGE_BG)
    for index, thumb in enumerate(thumbs):
        mosaic.paste(thumb, ((index % cols) * 192, (index // cols) * 108))
    global_palette = mosaic.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)

    frames: list[Image.Image] = []
    print(f"Rendering {frame_count} frames at {W}x{H}, {FPS} fps, 128-color global palette...")
    for frame_index in range(frame_count):
        global_time = min(total - 0.001, frame_index / FPS)
        frame = render_frame(global_time)
        palette_frame = frame.quantize(palette=global_palette, dither=Image.Dither.NONE)
        frames.append(palette_frame)
        if frame_index % 25 == 0:
            print(f"  frame {frame_index:03d}/{frame_count}")

    suffix = "-light" if IS_LIGHT else ""
    gif_path = OUT_DIR / f"trusted-space-promo{suffix}.gif"
    webp_path = OUT_DIR / f"trusted-space-promo{suffix}.webp"
    poster_path = OUT_DIR / f"trusted-space-poster{suffix}.png"
    cloud_poster_path = OUT_DIR / f"trusted-space-aliyun-poster{suffix}.png"
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / FPS),
        loop=0,
        optimize=True,
        disposal=1,
    )
    # Re-render one clean RGB frame for the WebP and poster rather than upscaling a GIF palette.
    poster_time = SCENES[4][1] + 2.65
    for frame_index, frame in enumerate(frames):
        if abs(frame_index / FPS - poster_time) < 1 / FPS / 2:
            poster = render_frame(frame_index / FPS)
            poster.save(poster_path, format="PNG", optimize=True)
            break
    else:
        poster = render_frame(poster_time)
        poster.save(poster_path, format="PNG", optimize=True)

    cloud_poster = render_frame(SCENES[7][1] + 3.55)
    cloud_poster.save(cloud_poster_path, format="PNG", optimize=True)

    rgb_frames = []
    for frame in frames:
        rgb_frames.append(frame.convert("RGB"))
    rgb_frames[0].save(
        webp_path,
        save_all=True,
        append_images=rgb_frames[1:],
        duration=int(1000 / FPS),
        loop=0,
        quality=84,
        method=4,
    )
    print(f"GIF   {gif_path} ({gif_path.stat().st_size / 1024 / 1024:.2f} MiB)")
    print(f"WebP  {webp_path} ({webp_path.stat().st_size / 1024 / 1024:.2f} MiB)")
    print(f"PNG   {poster_path} ({poster_path.stat().st_size / 1024:.0f} KiB)")
    print(f"PNG   {cloud_poster_path} ({cloud_poster_path.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
