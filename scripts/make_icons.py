"""Generate favicon/PWA icons into web/icons from one geometry definition: python scripts/make_icons.py"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "web", "icons")
BG, BG2, WHITE, FOLD = "#2a78d6", "#1d5cb0", "#ffffff", "#cfe0f7"
BARS = [(120, 340, 64), (224, 300, 64), (328, 260, 64)]   # x, top, width (bottom = 400)
BOTTOM = 400
PLANE = [(404, 100), (108, 232), (226, 270), (262, 386)]
FOLDP = [(226, 270), (404, 100), (262, 386)]

def svg(maskable=False):
    s = 0.8 if maskable else 1
    t = f'transform="translate({256 - 256 * s:.1f} {256 - 256 * s:.1f}) scale({s})"' if maskable else ""
    rect = '<rect width="512" height="512"/>' if maskable else '<rect width="512" height="512" rx="112"/>'
    bars = "".join(f'<rect x="{x}" y="{y}" width="{w}" height="{BOTTOM - y}" rx="10"/>' for x, y, w in BARS)
    pts = lambda p: " ".join(f"{x},{y}" for x, y in p)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{BG}"/><stop offset="1" stop-color="{BG2}"/></linearGradient></defs>'
            f'<g fill="url(#g)">{rect}</g><g {t}><g fill="{WHITE}" fill-opacity=".55">{bars}</g>'
            f'<polygon points="{pts(PLANE)}" fill="{WHITE}"/><polygon points="{pts(FOLDP)}" fill="{FOLD}"/></g></svg>')

def png(size, maskable=False):
    k = 4; n = size * k; f = n / 512
    s = 0.8 if maskable else 1
    tf = lambda x, y: ((256 + (x - 256) * s) * f, (256 + (y - 256) * s) * f)
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0)); d = ImageDraw.Draw(im, "RGBA")
    grad = Image.new("RGBA", (n, n)); gd = ImageDraw.Draw(grad)
    c1, c2 = Image.new("RGB", (1, 1), BG).getpixel((0, 0)), Image.new("RGB", (1, 1), BG2).getpixel((0, 0))
    for y in range(n):
        a = y / (n - 1); gd.line([(0, y), (n, y)], fill=tuple(round(c1[i] + (c2[i] - c1[i]) * a) for i in range(3)) + (255,))
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], radius=0 if maskable else 112 * f, fill=255)
    im.paste(grad, (0, 0), mask)
    layer = Image.new("RGBA", (n, n), (0, 0, 0, 0)); ld = ImageDraw.Draw(layer)
    for x, y, w in BARS:
        ld.rounded_rectangle([*tf(x, y), *tf(x + w, BOTTOM)], radius=10 * f * s, fill=(255, 255, 255, 140))
    ld.polygon([tf(*p) for p in PLANE], fill=WHITE)
    ld.polygon([tf(*p) for p in FOLDP], fill=FOLD)
    im = Image.alpha_composite(im, layer)
    return im.resize((size, size), Image.LANCZOS)

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    w = lambda n, t: open(os.path.join(OUT, n), "w").write(t)
    w("icon.svg", svg()); w("icon-maskable.svg", svg(True))
    for sz in (192, 512):
        png(sz).save(os.path.join(OUT, f"icon-{sz}.png"))
        png(sz, True).save(os.path.join(OUT, f"icon-maskable-{sz}.png"))
    png(180, True).convert("RGB").save(os.path.join(OUT, "apple-touch-icon.png"))
    png(32).save(os.path.join(OUT, "favicon-32.png"))
    png(256).save(os.path.join(OUT, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    print("icons written to", os.path.normpath(OUT))
