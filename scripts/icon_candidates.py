"""Render several icon/logo candidates for GURU Mobile Discovery.

Outputs PNG previews to scripts/icon_previews/. Open them in an image viewer to compare.
Pick one and we'll commit it to assets/app.ico.

Run: python scripts/icon_candidates.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter


SIZE = 512  # render large; downscaling later gives smoother edges


def _font(name: str, px: int) -> ImageFont.FreeTypeFont:
    for f in (name, "segoeuib.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(f, size=px)
        except OSError:
            continue
    return ImageFont.load_default()


def _rounded_bg(color, size=SIZE, radius_frac=0.18) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r = int(size * radius_frac)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=color)
    return img


def _vertical_gradient(top, bottom, size=SIZE, radius_frac=0.18) -> Image.Image:
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        r = int(top[0] * (1 - t) + bottom[0] * t)
        g = int(top[1] * (1 - t) + bottom[1] * t)
        b = int(top[2] * (1 - t) + bottom[2] * t)
        for x in range(size):
            px[x, y] = (r, g, b, 255)
    # mask to rounded square
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius_frac), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def _draw_phone(img: Image.Image, cx: int, cy: int, w: int, h: int,
                body_color, screen_color, outline_color=None, screen_inset_frac=0.06) -> None:
    draw = ImageDraw.Draw(img)
    radius = int(min(w, h) * 0.16)
    x0, y0 = cx - w // 2, cy - h // 2
    x1, y1 = cx + w // 2, cy + h // 2
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=body_color,
                           outline=outline_color, width=max(1, w // 64) if outline_color else 0)
    inset = int(min(w, h) * screen_inset_frac)
    sx0, sy0 = x0 + inset, y0 + inset + int(h * 0.04)
    sx1, sy1 = x1 - inset, y1 - inset - int(h * 0.04)
    draw.rounded_rectangle([sx0, sy0, sx1, sy1], radius=int(radius * 0.7), fill=screen_color)


def _draw_lens(img: Image.Image, cx: int, cy: int, r: int,
               ring_color, glass_color, handle_color, handle_angle_deg=45,
               handle_len_frac=0.95, handle_thick_frac=0.18) -> None:
    draw = ImageDraw.Draw(img)
    ring_w = max(2, int(r * 0.18))
    # ring
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ring_color)
    # glass interior
    draw.ellipse([cx - r + ring_w, cy - r + ring_w, cx + r - ring_w, cy + r - ring_w], fill=glass_color)
    # subtle highlight on glass
    hi = int(r * 0.35)
    draw.ellipse([cx - r + ring_w + 4, cy - r + ring_w + 4,
                  cx - r + ring_w + 4 + hi, cy - r + ring_w + 4 + hi],
                 fill=(255, 255, 255, 90))
    # handle
    a = math.radians(handle_angle_deg)
    hx0 = cx + math.cos(a) * (r - ring_w * 0.5)
    hy0 = cy + math.sin(a) * (r - ring_w * 0.5)
    L = int(r * handle_len_frac)
    hx1 = hx0 + math.cos(a) * L
    hy1 = hy0 + math.sin(a) * L
    thick = max(4, int(r * handle_thick_frac))
    draw.line([(hx0, hy0), (hx1, hy1)], fill=handle_color, width=thick)
    # rounded cap
    cap = thick // 2
    draw.ellipse([hx1 - cap, hy1 - cap, hx1 + cap, hy1 + cap], fill=handle_color)


def _draw_speech_bubble(img: Image.Image, cx: int, cy: int, w: int, h: int,
                        fill_color, tail_dir="left") -> None:
    draw = ImageDraw.Draw(img)
    radius = int(min(w, h) * 0.28)
    x0, y0 = cx - w // 2, cy - h // 2
    x1, y1 = cx + w // 2, cy + h // 2
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill_color)
    # tail
    t = int(min(w, h) * 0.18)
    if tail_dir == "left":
        pts = [(x0 + t, y1 - t // 2), (x0 - t, y1 + t // 2), (x0 + 2 * t, y1)]
    else:
        pts = [(x1 - t, y1 - t // 2), (x1 + t, y1 + t // 2), (x1 - 2 * t, y1)]
    draw.polygon(pts, fill=fill_color)


def candidate_phone_lens() -> Image.Image:
    """Phone with a magnifying glass overlay — literal Mobile Discovery motif."""
    img = _vertical_gradient(top=(60, 30, 110), bottom=(170, 50, 130))  # purple → magenta
    _draw_phone(img, cx=SIZE // 2 - 30, cy=SIZE // 2 - 20,
                w=int(SIZE * 0.46), h=int(SIZE * 0.74),
                body_color=(245, 240, 235, 255),
                screen_color=(35, 35, 60, 255))
    # tiny speech bubble inside phone screen
    _draw_speech_bubble(img, cx=SIZE // 2 - 30, cy=SIZE // 2 - 30,
                        w=int(SIZE * 0.22), h=int(SIZE * 0.14),
                        fill_color=(120, 220, 230, 255), tail_dir="left")
    # magnifying glass overlapping bottom-right of phone
    _draw_lens(img, cx=int(SIZE * 0.66), cy=int(SIZE * 0.66),
               r=int(SIZE * 0.22),
               ring_color=(255, 200, 80, 255),       # warm gold ring
               glass_color=(180, 240, 255, 200),     # cyan tinted glass
               handle_color=(255, 200, 80, 255),
               handle_angle_deg=45)
    return img


def candidate_g_lens() -> Image.Image:
    """Letter G stylized so its negative-space curve reads as a magnifying glass lens."""
    img = _vertical_gradient(top=(20, 110, 130), bottom=(15, 60, 90))  # teal → deep blue
    draw = ImageDraw.Draw(img)
    # giant G in white
    font = _font("segoeuib.ttf", int(SIZE * 0.78))
    ch = "G"
    bbox = draw.textbbox((0, 0), ch, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (SIZE - tw) // 2 - bbox[0] - int(SIZE * 0.02)
    ty = (SIZE - th) // 2 - bbox[1] - int(SIZE * 0.02)
    draw.text((tx, ty), ch, fill=(255, 255, 255, 255), font=font)
    # magnifying glass handle protruding from bottom-right of G
    handle_color = (255, 180, 60, 255)
    L = int(SIZE * 0.30)
    a = math.radians(45)
    hx0 = SIZE * 0.66
    hy0 = SIZE * 0.66
    hx1 = hx0 + math.cos(a) * L
    hy1 = hy0 + math.sin(a) * L
    thick = int(SIZE * 0.06)
    draw.line([(hx0, hy0), (hx1, hy1)], fill=handle_color, width=thick)
    cap = thick // 2
    draw.ellipse([hx1 - cap, hy1 - cap, hx1 + cap, hy1 + cap], fill=handle_color)
    return img


def candidate_chat_burst() -> Image.Image:
    """Three colorful speech bubbles — communications/messaging energy."""
    img = _rounded_bg((24, 28, 50, 255))  # deep navy
    _draw_speech_bubble(img, cx=int(SIZE * 0.40), cy=int(SIZE * 0.34),
                        w=int(SIZE * 0.50), h=int(SIZE * 0.28),
                        fill_color=(255, 110, 100, 255), tail_dir="left")  # coral
    _draw_speech_bubble(img, cx=int(SIZE * 0.62), cy=int(SIZE * 0.56),
                        w=int(SIZE * 0.46), h=int(SIZE * 0.26),
                        fill_color=(255, 200, 80, 255), tail_dir="right")  # gold
    _draw_speech_bubble(img, cx=int(SIZE * 0.40), cy=int(SIZE * 0.76),
                        w=int(SIZE * 0.42), h=int(SIZE * 0.22),
                        fill_color=(110, 220, 200, 255), tail_dir="left")  # teal
    return img


def candidate_compass_phone() -> Image.Image:
    """Phone outline with a compass rose overlay — discovery/exploration angle."""
    img = _vertical_gradient(top=(30, 70, 130), bottom=(15, 35, 70))  # blue
    draw = ImageDraw.Draw(img)
    _draw_phone(img, cx=SIZE // 2, cy=SIZE // 2,
                w=int(SIZE * 0.50), h=int(SIZE * 0.78),
                body_color=(255, 255, 255, 255),
                screen_color=(20, 35, 65, 255))
    # compass rose centered on screen
    cx, cy = SIZE // 2, SIZE // 2
    r = int(SIZE * 0.20)
    # outer circle
    draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                 outline=(255, 200, 80, 255), width=max(2, SIZE // 80))
    # 4 cardinal points (diamond)
    gold = (255, 200, 80, 255)
    cream = (255, 240, 200, 255)
    L = int(r * 0.92)
    pts_n = [(cx, cy - L), (cx - r // 5, cy), (cx, cy - r // 6), (cx + r // 5, cy)]
    pts_s = [(cx, cy + L), (cx - r // 5, cy), (cx, cy + r // 6), (cx + r // 5, cy)]
    pts_e = [(cx + L, cy), (cx, cy - r // 5), (cx + r // 6, cy), (cx, cy + r // 5)]
    pts_w = [(cx - L, cy), (cx, cy - r // 5), (cx - r // 6, cy), (cx, cy + r // 5)]
    draw.polygon(pts_n, fill=gold)
    draw.polygon(pts_s, fill=cream)
    draw.polygon(pts_e, fill=cream)
    draw.polygon(pts_w, fill=cream)
    return img


def candidate_gmd_stripes() -> Image.Image:
    """G·M·D monogram across three colored stripes — bold, modern, brand-mark style."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    # three diagonal stripes
    stripe = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stripe)
    colors = [
        (95, 60, 175, 255),   # purple
        (30, 165, 175, 255),  # teal
        (240, 145, 55, 255),  # orange
    ]
    h = SIZE // 3
    for i, c in enumerate(colors):
        sd.rectangle([0, i * h, SIZE, (i + 1) * h], fill=c)
    # mask to rounded square
    mask = Image.new("L", (SIZE, SIZE), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=int(SIZE * 0.18), fill=255)
    img.paste(stripe, (0, 0), mask)
    # letters G M D centered in each stripe
    draw = ImageDraw.Draw(img)
    font = _font("segoeuib.ttf", int(SIZE * 0.22))
    for i, ch in enumerate(("G", "M", "D")):
        bbox = draw.textbbox((0, 0), ch, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        cx = SIZE // 2
        cy = i * h + h // 2
        tx = cx - tw // 2 - bbox[0]
        ty = cy - th // 2 - bbox[1]
        draw.text((tx, ty), ch, fill=(255, 255, 255, 255), font=font)
    return img


def main() -> None:
    out_dir = Path(__file__).resolve().parent / "icon_previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        ("1_phone_lens", candidate_phone_lens),
        ("2_g_lens", candidate_g_lens),
        ("3_chat_burst", candidate_chat_burst),
        ("4_compass_phone", candidate_compass_phone),
        ("5_gmd_stripes", candidate_gmd_stripes),
    ]
    for name, fn in candidates:
        img = fn()
        path = out_dir / f"candidate_{name}.png"
        img.save(path, format="PNG")
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
