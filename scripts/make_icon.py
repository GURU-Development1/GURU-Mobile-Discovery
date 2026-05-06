"""Generate assets/app.ico (multi-resolution) for the Windows build. Run from project root: python scripts/make_icon.py

Placeholder branding for GURU Mobile Discovery: a bold "G" on the brand-blue background.
Replace with a designed logo when one is available.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    out = root / "assets" / "app.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Brand accent #5B7FA5 from app/style.py
    bg = (91, 127, 165, 255)
    sizes = [16, 32, 48, 64, 128, 256]
    images: list[Image.Image] = []
    for s in sizes:
        img = Image.new("RGBA", (s, s), bg)
        draw = ImageDraw.Draw(img)
        margin = max(1, s // 16)
        draw.rounded_rectangle(
            [margin, margin, s - margin - 1, s - margin - 1],
            radius=max(2, s // 8),
            outline=(255, 255, 255, 220),
            width=max(1, s // 32),
        )
        # "G" monogram for GURU Mobile Discovery (readable at small sizes)
        try:
            font = ImageFont.truetype("segoeuib.ttf", size=max(10, int(s * 0.62)))
        except OSError:
            try:
                font = ImageFont.truetype("segoeui.ttf", size=max(10, int(s * 0.62)))
            except OSError:
                font = ImageFont.load_default()
        ch = "G"
        bbox = draw.textbbox((0, 0), ch, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (s - tw) // 2 - bbox[0]
        ty = (s - th) // 2 - bbox[1]
        draw.text((tx, ty), ch, fill=(255, 255, 255, 255), font=font)
        images.append(img)

    images[0].save(
        out,
        format="ICO",
        sizes=[(im.width, im.height) for im in images],
        append_images=images[1:],
    )
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
