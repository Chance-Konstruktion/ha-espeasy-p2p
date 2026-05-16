#!/usr/bin/env python3
"""Generate HACS-compliant brand assets from a single source image.

Specs enforced by the home-assistant/brands bot:
  - icon.png      exactly 256x256
  - icon@2x.png   exactly 512x512
  - logo.png      height max 128 (width proportional)
  - logo@2x.png   height max 256 (width proportional)

All outputs are PNG with alpha channel preserved, optimized for size.

Usage:
    python scripts/generate_brand_assets.py <source.png> [--out-dir brand]

The source image should be square for the icon variants (the script
center-crops to square before resizing). For the logo variants the full
aspect ratio is preserved.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")


def center_crop_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def save_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=True)
    print(f"  wrote {path}  ({path.stat().st_size / 1024:.1f} KB, {img.size[0]}x{img.size[1]})")


def make_icon(src: Image.Image, size: int) -> Image.Image:
    return center_crop_square(src).resize((size, size), Image.LANCZOS)


def make_logo(src: Image.Image, max_height: int) -> Image.Image:
    w, h = src.size
    if h <= max_height:
        return src.copy()
    new_w = round(w * max_height / h)
    return src.resize((new_w, max_height), Image.LANCZOS)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path, help="Source image (PNG with alpha preferred)")
    p.add_argument("--out-dir", type=Path, default=Path("brand"), help="Output directory")
    args = p.parse_args()

    if not args.source.is_file():
        sys.exit(f"Source not found: {args.source}")

    src = Image.open(args.source).convert("RGBA")
    print(f"Source: {args.source} ({src.size[0]}x{src.size[1]})")

    save_png(make_icon(src, 256), args.out_dir / "icon.png")
    save_png(make_icon(src, 512), args.out_dir / "icon@2x.png")
    save_png(make_logo(src, 128), args.out_dir / "logo.png")
    save_png(make_logo(src, 256), args.out_dir / "logo@2x.png")

    print("Done. Verify visually before pushing to home-assistant/brands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
