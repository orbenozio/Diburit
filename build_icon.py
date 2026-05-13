#!/usr/bin/env python3
"""Build Diburit.icns from a single procedurally-drawn 1024x1024 PNG.

Renders a squircle backdrop with a violet -> magenta gradient, a soft
inner shadow, a stylised microphone in the center, and three Hebrew
sound-wave arcs to the side. Then writes a .iconset with all the macOS
icon-required sizes and invokes `iconutil` to produce Diburit.icns.

Run inside the venv (Pillow must be installed):

    /Users/orbenozio/Diburit/.venv/bin/python build_icon.py

This script is a build-time tool only - it is not imported by the app
and does not need to be packaged into the bundle.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parent
ICONSET_DIR = REPO / "Diburit.iconset"
PNG_OUT = REPO / "Diburit-1024.png"
ICNS_OUT = REPO / "Diburit.icns"

CANVAS = 1024


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def _squircle_mask(size: int, radius_ratio: float = 0.225) -> Image.Image:
    """macOS-style rounded-rect mask. radius_ratio matches Big Sur+ where
    icon corners are ~22-23% of the canvas."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    r = int(size * radius_ratio)
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=r, fill=255)
    return mask


def _gradient_fill(size: int, top: tuple, bottom: tuple) -> Image.Image:
    """Vertical linear gradient from `top` to `bottom`."""
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        grad.putpixel((0, y), _lerp(top, bottom, y / max(size - 1, 1)))
    return grad.resize((size, size))


def _draw_microphone(canvas: Image.Image) -> None:
    """Stylised mic: rounded capsule body, stem, and base bar. White with a
    very faint inner highlight."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    cx = CANVAS // 2

    # Capsule body
    body_w = 280
    body_h = 460
    body_top = 230
    body_left = cx - body_w // 2
    body_right = cx + body_w // 2
    body_bot = body_top + body_h
    draw.rounded_rectangle(
        [(body_left, body_top), (body_right, body_bot)],
        radius=body_w // 2,
        fill=(255, 255, 255, 255),
    )

    # Subtle highlight stripe on the capsule
    hi_w = 70
    hi_h = body_h - 120
    hi_left = body_left + 38
    hi_top = body_top + 60
    draw.rounded_rectangle(
        [(hi_left, hi_top), (hi_left + hi_w, hi_top + hi_h)],
        radius=hi_w // 2,
        fill=(255, 255, 255, 90),
    )

    # U-shape stand: arc + vertical stem + base bar
    arc_box = [(cx - 220, body_bot - 120), (cx + 220, body_bot + 120)]
    draw.arc(arc_box, start=0, end=180, fill=(255, 255, 255, 255), width=34)

    stem_top = body_bot + 120
    stem_bot = stem_top + 90
    draw.rounded_rectangle(
        [(cx - 17, stem_top), (cx + 17, stem_bot)],
        radius=17,
        fill=(255, 255, 255, 255),
    )

    base_w = 240
    base_h = 36
    draw.rounded_rectangle(
        [(cx - base_w // 2, stem_bot), (cx + base_w // 2, stem_bot + base_h)],
        radius=base_h // 2,
        fill=(255, 255, 255, 255),
    )


def _draw_sound_waves(canvas: Image.Image) -> None:
    """Three crescent-shaped sound waves emanating from the mic on the right."""
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    cx = CANVAS // 2
    cy = 460

    for i, (radius, alpha) in enumerate([(260, 230), (340, 170), (420, 110)]):
        box = [(cx - radius, cy - radius), (cx + radius, cy + radius)]
        thickness = 28 - i * 4
        draw.arc(box, start=-35, end=35, fill=(255, 255, 255, alpha), width=thickness)

    canvas.alpha_composite(overlay)


def build_master_png() -> Path:
    base = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

    # Gradient backdrop. Indigo -> magenta is friendly and Hebrew-ish via
    # the same palette family that macOS Voice Control uses.
    top = (96, 60, 220)
    bottom = (217, 70, 180)
    gradient = _gradient_fill(CANVAS, top, bottom).convert("RGBA")

    mask = _squircle_mask(CANVAS)
    base.paste(gradient, (0, 0), mask)

    # Soft inner shadow at the top edge for depth
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        [(0, 0), (CANVAS - 1, CANVAS - 1)],
        radius=int(CANVAS * 0.225),
        outline=(0, 0, 0, 110),
        width=14,
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    base.alpha_composite(shadow)

    _draw_sound_waves(base)
    _draw_microphone(base)

    # Re-mask to keep the corners crisp after compositing
    final = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    final.paste(base, (0, 0), mask)

    final.save(PNG_OUT, "PNG")
    return PNG_OUT


def build_iconset(master_png: Path) -> Path:
    if ICONSET_DIR.exists():
        shutil.rmtree(ICONSET_DIR)
    ICONSET_DIR.mkdir()

    # macOS .iconset requires this exact filename matrix. iconutil refuses
    # to compile a .icns if any are missing.
    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]

    src = Image.open(master_png)
    for px, name in sizes:
        scaled = src.resize((px, px), Image.LANCZOS)
        scaled.save(ICONSET_DIR / name, "PNG")

    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET_DIR), "-o", str(ICNS_OUT)],
        check=True,
    )
    return ICNS_OUT


def main() -> None:
    png = build_master_png()
    icns = build_iconset(png)
    print(f"Wrote {png}")
    print(f"Wrote {icns}")


if __name__ == "__main__":
    main()
