"""Generate the RetroShelf icon (.ico + favicon PNG) — pixel-art amber
cartridge on a dark CRT tile. Build-time helper, needs Pillow."""

import base64
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent

# 16x16 pixel design. Legend:
#   . transparent   b background tile   B tile edge highlight
#   a amber cart    d dark label window A light amber top edge
#   g green power-led pixel
DESIGN = [
    ".bbbbbbbbbbbbbb.",
    "bbbbbbbbbbbbbbbb",
    "bbAAAAAAAAAAAAbb",
    "bbaaaaaaaaaaaabb",
    "bbaaddddddddaabb",
    "bbaaddddddddaabb",
    "bbaaddddddddaabb",
    "bbaaaaaaaaaaaabb",
    "bbaaaaaaaaaaaabb",
    "bbaaaaaaaaaaaabb",
    "bbbaaaaaaaaaabbb",
    "bbbaabbbbbbaabbb",
    "bbbaabbbbbbaabbb",
    "bbbbbbbbbbbbgbbb",
    "bbbbbbbbbbbbbbbb",
    ".bbbbbbbbbbbbbb.",
]

COLORS = {
    ".": (0, 0, 0, 0),
    "b": (10, 9, 6, 255),        # near-black tile
    "B": (30, 26, 16, 255),
    "A": (255, 215, 94, 255),    # light amber edge
    "a": (255, 176, 0, 255),     # amber cart body
    "d": (122, 85, 0, 255),      # dark label window
    "g": (57, 255, 136, 255),    # green power pixel
}


def base16():
    img = Image.new("RGBA", (16, 16))
    for y, row in enumerate(DESIGN):
        for x, ch in enumerate(row):
            img.putpixel((x, y), COLORS[ch])
    # faint scanlines over the cart body
    for y in range(2, 13, 2):
        for x in range(16):
            r, g, b, a = img.getpixel((x, y))
            if a and (r, g, b) != COLORS["b"][:3]:
                img.putpixel((x, y), (int(r * .85), int(g * .85), int(b * .85), a))
    return img


def main():
    small = base16()
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [small.resize((s, s), Image.NEAREST) for s in sizes]
    ico = HERE / "retroshelf.ico"
    frames[-1].save(ico, format="ICO",
                    sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])
    fav = HERE / "favicon32.png"
    small.resize((32, 32), Image.NEAREST).save(fav)
    b64 = base64.b64encode(fav.read_bytes()).decode()
    print("wrote", ico.name, "and", fav.name)
    print("FAVICON_B64:", b64)


if __name__ == "__main__":
    main()
