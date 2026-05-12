"""Generate assets/app.ico (multi-resolution) from the GURU Mobile Discovery square logo.

Run from project root: python scripts/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image


SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _to_square(img: Image.Image) -> Image.Image:
    """Pad to a square canvas using the top-left corner color (navy brand bg)."""
    w, h = img.size
    if w == h:
        return img
    side = max(w, h)
    bg_pixel = img.convert("RGBA").getpixel((0, 0))
    if not isinstance(bg_pixel, tuple) or len(bg_pixel) < 4:
        bg = (12, 26, 58, 255)  # brand navy fallback
    else:
        r, g, b, _a = bg_pixel[:4]
        bg = (r, g, b, 255)
    canvas = Image.new("RGBA", (side, side), bg)
    canvas.paste(img, ((side - w) // 2, (side - h) // 2), img if img.mode == "RGBA" else None)
    return canvas


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    src = root / "assets" / "logo" / "guru_logo_square.png"
    out = root / "assets" / "app.ico"
    out.parent.mkdir(parents=True, exist_ok=True)

    if not src.is_file():
        raise FileNotFoundError(f"Source logo not found: {src}")

    base = Image.open(src).convert("RGBA")
    squared = _to_square(base)
    largest = max(SIZES, key=lambda s: s[0])
    if squared.size != largest:
        squared = squared.resize(largest, resample=Image.Resampling.LANCZOS)

    squared.save(out, format="ICO", sizes=SIZES)
    print(f"Wrote {out} from {src} ({len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
