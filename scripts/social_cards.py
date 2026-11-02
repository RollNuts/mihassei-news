"""Cost-free, deterministic, first-party raster social previews for new stories.

No fonts, external assets, network, APIs, or non-standard Python packages.
The images are clearly fictional editorial illustrations, not event photos.
"""
from __future__ import annotations

import hashlib
import math
import struct
import zlib
from pathlib import Path

WIDTH = 1200
HEIGHT = 630

# 5x7 pixel capitals: the English typographic treatment is deliberate.
# Japanese article headlines remain in the HTML OpenGraph/Twitter title.
GLYPHS = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10101", "10011", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "/": ("00001", "00001", "00010", "00100", "01000", "10000", "10000"),
    " ": ("00000",) * 7,
}

CATEGORY_LABELS = {
    "社会": "SOCIETY",
    "経済": "ECONOMY",
    "技術": "TECHNOLOGY",
    "政治": "POLITICS",
    "暮らし": "LIFESTYLE",
    "スポーツ": "SPORTS",
    "エンタメ": "ENTERTAINMENT",
}
CATEGORY_COLORS = {
    "社会": (239, 109, 77),
    "経済": (231, 180, 102),
    "技術": (107, 207, 201),
    "政治": (179, 165, 232),
    "暮らし": (127, 195, 144),
    "スポーツ": (231, 171, 114),
    "エンタメ": (218, 133, 191),
}


class Canvas:
    def __init__(self):
        self.raw = bytearray(bytes((16, 33, 40)) * (WIDTH * HEIGHT))

    def rect(self, x0, y0, x1, y1, color):
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(WIDTH, int(x1)), min(HEIGHT, int(y1))
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            start = (y * WIDTH + x0) * 3
            self.raw[start:start + len(row)] = row

    def circle(self, cx, cy, radius, color):
        radius = int(radius)
        for dy in range(-radius, radius + 1):
            dx = math.isqrt(max(0, radius * radius - dy * dy))
            self.rect(cx - dx, cy + dy, cx + dx + 1, cy + dy + 1, color)

    def line(self, x0, y0, x1, y1, color, thick=4):
        steps = max(abs(int(x1 - x0)), abs(int(y1 - y0)), 1)
        half = thick // 2
        for i in range(steps + 1):
            x = round(x0 + (x1 - x0) * i / steps)
            y = round(y0 + (y1 - y0) * i / steps)
            self.rect(x - half, y - half, x - half + thick, y - half + thick, color)

    def text(self, words, x, y, size, color):
        words = words.upper()
        for ch in words:
            glyph = GLYPHS.get(ch, GLYPHS[" "])
            for gy, row in enumerate(glyph):
                for gx, bit in enumerate(row):
                    if bit == "1":
                        self.rect(x + gx*size, y + gy*size,
                                  x + (gx+1)*size, y + (gy+1)*size, color)
            x += 6 * size

    def save(self, path: Path):
        def chunk(kind, data):
            import binascii
            return (struct.pack(">I", len(data)) + kind + data +
                    struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff))
        scan = b"".join(
            b"\x00" + self.raw[y*WIDTH*3:(y+1)*WIDTH*3]
            for y in range(HEIGHT)
        )
        payload = (b"\x89PNG\r\n\x1a\n" +
                   chunk(b"IHDR", struct.pack(">2I5B", WIDTH, HEIGHT, 8, 2, 0, 0, 0)) +
                   chunk(b"IDAT", zlib.compress(scan, 6)) +
                   chunk(b"IEND", b""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def illustrate(canvas, art, accent, seed):
    """An obviously fictional, abstract visual inspired by the article's art type."""
    ink = (18, 47, 56)
    dark = (36, 65, 71)
    pale = (229, 235, 218)
    background = (28, 66, 76)
    offset = (seed[0] % 35) - 17
    canvas.rect(720, 104, 1166, 525, background)
    for i in range(16):
        canvas.line(732, 120 + i*25, 1160, 120+i*25, (37, 78, 86), 1)
    canvas.circle(961 + offset, 306, 187, (38, 82, 88))
    canvas.circle(961 + offset, 306, 155, background)
    if art == "robot":
        canvas.rect(793, 423, 1110, 460, pale)
        canvas.rect(827, 378, 892, 430, accent)
        canvas.line(854, 393, 919, 284, pale, 33)
        canvas.circle(919, 284, 33, accent)
        canvas.line(919, 284, 1043, 338, pale, 35)
        canvas.circle(1043, 338, 28, accent)
        canvas.line(1043, 338, 1025, 391, pale, 22)
        canvas.line(1025, 391, 997, 408, accent, 9)
    elif art == "tower":
        for i, (x, high) in enumerate(((796, 253), (913, 183), (1040, 290))):
            canvas.rect(x, high, x+93, 459, pale if i != 1 else accent)
            for yy in range(high+24, 440, 43):
                for xx in range(x+19, x+80, 28):
                    canvas.rect(xx, yy, xx+12, yy+19, ink)
        canvas.rect(780, 450, 1144, 466, dark)
    elif art == "meeting":
        canvas.rect(810, 171, 1096, 324, pale)
        canvas.rect(831, 190, 1075, 304, dark)
        canvas.circle(955, 247, 35, accent)
        canvas.circle(955, 247, 15, pale)
        canvas.rect(785, 385, 1136, 422, accent)
        for cx in (832, 956, 1082):
            canvas.circle(cx, 355, 24, pale)
            canvas.rect(cx-35, 388, cx+35, 443, ink)
    elif art == "cards":
        for x, y in ((785, 220), (847, 169), (907, 207)):
            canvas.rect(x, y, x+158, y+229, ink)
            canvas.rect(x+8, y+9, x+150, y+219, pale)
            canvas.rect(x+25, y+34, x+125, y+46, accent)
            canvas.rect(x+25, y+72, x+114, y+80, dark)
            canvas.rect(x+25, y+91, x+119, y+99, dark)
            canvas.circle(x+79, y+164, 25, accent)
    elif art == "notice":
        canvas.rect(801, 160, 1106, 464, ink)
        canvas.rect(818, 173, 1090, 451, pale)
        canvas.rect(846, 212, 1061, 234, accent)
        canvas.rect(846, 256, 1027, 266, dark)
        canvas.rect(846, 281, 1047, 291, dark)
        canvas.rect(846, 306, 1010, 316, dark)
        canvas.circle(982, 381, 45, accent)
        canvas.line(982, 358, 982, 387, pale, 11)
        canvas.circle(982, 405, 6, pale)
    else:
        for cx, cy, radius in ((862, 260, 64), (1047, 241, 44), (993, 394, 81)):
            canvas.circle(cx, cy, radius, pale)
            canvas.circle(cx, cy, radius-19, accent)
        canvas.line(862, 260, 1047, 241, pale, 11)
        canvas.line(1047, 241, 993, 394, pale, 11)
    # Deterministic microdetails vary every story and do not depict real events.
    for i in range(5):
        x = 759 + i*79 + seed[i+2] % 18
        y = 489 + seed[i+7] % 15
        canvas.rect(x, y, x + 25, y + 4, accent)


def render_social_card(article: dict, output: Path) -> None:
    slug = article["slug"]
    if not slug or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in slug):
        raise ValueError("Invalid article slug for social preview")
    seed = hashlib.sha256(slug.encode("utf-8")).digest()
    accent = CATEGORY_COLORS.get(article["category"], (239, 109, 77))
    canvas = Canvas()
    cream = (248, 245, 228)
    faded = (157, 186, 182)
    canvas.rect(0, 0, WIDTH, 23, accent)
    canvas.rect(0, 23, 690, 630, (17, 34, 39))
    canvas.rect(63, 70, 74, 514, accent)
    canvas.rect(105, 74, 646, 79, (95, 130, 129))
    canvas.text("MIHASSEI", 111, 105, 8, cream)
    canvas.text("NEWS", 111, 184, 7, faded)
    year = str(article["fiction_at"][:4])
    canvas.text(year, 98, 265, 20, cream)
    canvas.rect(110, 432, 370, 438, accent)
    canvas.text("FICTION", 110, 461, 6, accent)
    canvas.text("5 YEARS LATER", 109, 528, 4, faded)
    canvas.rect(0, 558, WIDTH, 630, (8, 22, 28))
    canvas.rect(0, 558, 1199, 562, accent)
    canvas.text("VERIPSA.COM", 69, 580, 5, cream)
    canvas.text("FUTURE NEWS", 782, 580, 4, faded)
    canvas.text(CATEGORY_LABELS.get(article["category"], "FUTURE"), 747, 56, 5, accent)
    illustrate(canvas, article.get("art", "generic"), accent, seed)
    canvas.save(output)


def render_issue_card(day: str, output: Path) -> None:
    """Shareable issue cover with a real publication date, not a fictional timestamp.

    Stable for the whole publishing day, even as more articles join the issue.
    It uses only the existing first-party pixel canvas: no fonts, HTTP or APIs.
    """
    from datetime import date
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("Non-canonical issue date")
    except ValueError as exc:
        raise ValueError("Invalid issue publication date") from exc

    canvas = Canvas()
    paper, ink = (248, 245, 234), (22, 38, 42)
    accent, sage, pale = (190, 61, 47), (115, 144, 132), (218, 223, 208)
    canvas.rect(0, 0, WIDTH, HEIGHT, paper)
    canvas.rect(0, 0, WIDTH, 21, accent)
    canvas.rect(0, 21, WIDTH, 62, ink)
    canvas.rect(67, 97, 80, 515, accent)
    canvas.text("MIHASSEI", 109, 108, 9, ink)
    canvas.text("NEWS", 108, 192, 8, accent)
    canvas.rect(108, 269, 776, 275, sage)
    canvas.text(day.replace("-", "."), 106, 302, 12, ink)
    canvas.text("DAILY EDITION", 109, 425, 6, sage)
    canvas.text("FICTION", 110, 492, 5, accent)

    # A stylised stack of fictional papers, not a real-world event photograph.
    canvas.rect(832, 92, 1147, 524, ink)
    canvas.rect(853, 115, 1125, 497, pale)
    canvas.rect(876, 141, 1103, 158, accent)
    canvas.rect(875, 178, 1098, 184, ink)
    canvas.rect(875, 198, 1066, 204, sage)
    canvas.rect(876, 230, 1098, 355, paper)
    seed = hashlib.sha256(day.encode("ascii")).digest()
    for i in range(5):
        y = 246 + i * 21
        span = 92 + seed[i] % 106
        canvas.rect(894, y, 894 + span, y + 8, sage if i % 2 else ink)
    canvas.rect(875, 382, 978, 470, accent)
    canvas.rect(991, 384, 1099, 390, ink)
    canvas.rect(991, 407, 1082, 413, sage)
    canvas.rect(991, 430, 1095, 436, sage)
    canvas.rect(991, 454, 1070, 460, sage)
    canvas.rect(0, 545, WIDTH, HEIGHT, ink)
    canvas.rect(0, 545, WIDTH, 549, accent)
    canvas.text("VERIPSA.COM", 75, 572, 5, paper)
    canvas.text("5 YEARS LATER", 777, 575, 4, pale)
    canvas.save(output)
