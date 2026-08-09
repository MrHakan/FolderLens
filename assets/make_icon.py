"""Generate the FolderLens app icon (assets/icon.ico + icon.png).

Run once to (re)generate icons. Committed outputs are used by the build,
so this only needs re-running if the design changes.
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

BG_TOP = (37, 99, 235)      # #2563eb
BG_BOTTOM = (29, 78, 216)   # #1d4ed8
FOLDER = (250, 204, 21)     # amber
FOLDER_DARK = (234, 179, 8)
LENS_RING = (255, 255, 255)
GLASS = (191, 219, 254)


def _rounded(size):
    S = 512
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # vertical gradient background on a rounded square
    grad = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        r = int(BG_TOP[0] * (1 - t) + BG_BOTTOM[0] * t)
        g = int(BG_TOP[1] * (1 - t) + BG_BOTTOM[1] * t)
        b = int(BG_TOP[2] * (1 - t) + BG_BOTTOM[2] * t)
        gd.line([(0, y), (S, y)], fill=(r, g, b, 255))

    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, S, S], radius=110, fill=255)
    img.paste(grad, (0, 0), mask)

    # folder
    d.rounded_rectangle([96, 150, 300, 210], radius=18, fill=FOLDER_DARK)
    d.rounded_rectangle([96, 176, 340, 372], radius=26, fill=FOLDER)
    d.rounded_rectangle([96, 176, 340, 220], radius=26, fill=FOLDER_DARK)
    d.rounded_rectangle([96, 200, 340, 372], radius=26, fill=FOLDER)

    # magnifier
    d.ellipse([232, 214, 392, 374], outline=LENS_RING, width=30, fill=GLASS + (235,))
    d.line([372, 356, 436, 420], fill=LENS_RING, width=34)
    d.ellipse([420, 404, 452, 436], fill=LENS_RING)
    # glass highlight
    d.arc([252, 234, 340, 322], start=150, end=250, fill=(255, 255, 255, 220), width=12)

    return img.resize((size, size), Image.LANCZOS)


def main():
    base = _rounded(512)
    base.save(os.path.join(HERE, "icon.png"))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base.save(
        os.path.join(HERE, "icon.ico"),
        sizes=[(s, s) for s in sizes],
    )
    print("Wrote icon.png and icon.ico")


if __name__ == "__main__":
    main()
