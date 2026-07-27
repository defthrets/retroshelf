"""Generate the RetroShelf icon (.ico + favicon PNG) — an 8-bit game
controller in orange with red and green buttons. Build-time helper, needs
Pillow."""

import base64
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent

# 16x16 pixel design of a game controller. Legend:
#   . transparent      o dark outline       a orange body
#   A light top edge   d d-pad              r red button   g green button
DESIGN = [
    "................",
    "................",
    "................",
    "..oooooooooooo..",
    ".oAAAAAAAAAAAAo.",
    "oaaaaaaaaaaaaaao",
    "oaadaaaaaaaaaaao",
    "oadddaaaaaaragao",
    "oaadaaaaaaaaaaao",
    "oaaaaaaaaaaaaaao",
    ".oaaaaaaaaaaaao.",
    "..oooooooooooo..",
    "................",
    "................",
    "................",
    "................",
]

COLORS = {
    ".": (0, 0, 0, 0),
    "o": (18, 12, 26, 255),      # dark outline
    "A": (255, 190, 120, 255),   # light top edge
    "a": (255, 122, 24, 255),    # orange body
    "d": (40, 22, 12, 255),      # d-pad
    "r": (255, 47, 74, 255),     # red button
    "g": (0, 255, 136, 255),     # green button
}


def base16():
    img = Image.new("RGBA", (16, 16))
    for y, row in enumerate(DESIGN):
        for x, ch in enumerate(row):
            img.putpixel((x, y), COLORS[ch])
    # shade the lower half of the body so it reads as a moulded shell
    for y in range(9, 11):
        for x in range(16):
            r, g, b, a = img.getpixel((x, y))
            if a and (r, g, b) == COLORS["a"][:3]:
                img.putpixel((x, y), (int(r * .82), int(g * .82), int(b * .82), a))
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
