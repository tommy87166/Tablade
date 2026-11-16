#!/usr/bin/env python3
"""從 assets/icon.svg 產生打包用的圖示。

    uv run --group icons python scripts/make_icons.py

產出（都會 commit）：
    assets/icon.ico    Windows 執行檔圖示（16–256）
    assets/icon.icns   macOS .app 圖示（16–1024）
    assets/icon.png    256×256，給 README 以外需要點陣圖的地方
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import resvg_py
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def render(svg: str, size: int) -> Image.Image:
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))
    return Image.open(io.BytesIO(png)).convert("RGBA")


def main() -> int:
    svg = (ASSETS / "icon.svg").read_text(encoding="utf-8")
    images = {size: render(svg, size) for size in sorted({*ICO_SIZES, *ICNS_SIZES})}

    ico = ASSETS / "icon.ico"
    images[256].save(
        ico,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[images[s] for s in ICO_SIZES if s != 256],
    )
    icns = ASSETS / "icon.icns"
    images[1024].save(
        icns, format="ICNS", append_images=[images[s] for s in ICNS_SIZES if s != 1024]
    )
    png = ASSETS / "icon.png"
    images[256].save(png, format="PNG", optimize=True)

    for path in (ico, icns, png):
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
