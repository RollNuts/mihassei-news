#!/usr/bin/env python3
"""Original illustrated visual scenes for X experiments. Pillow only; no AI/X API.

Independent from paused, unapproved Nejimachi canon manga. Each scene is
deterministic and its existing file is never overwritten after publication.
"""
import hashlib
import random
from pathlib import Path


W, H = 1200, 675
SCENES = {
    "xexp-ufo-ticket": "ufo",
    "xexp-invisible-nails": "nails",
    "xexp-witch-autopilot": "broom",
    "xexp-shadow-home": "shadow",
}
ROOT = Path(__file__).resolve().parents[1] / "assets"


def base(scene):
    # Keep SCENES metadata importable by text-only CI without Pillow installed.
    from PIL import Image, ImageDraw
    colors = {"ufo": ((24, 33, 52), (75, 96, 107)),
              "nails": ((36, 53, 67), (100, 128, 136)),
              "broom": ((47, 46, 65), (170, 119, 102)),
              "shadow": ((35, 40, 56), (117, 91, 82))}
    top, bottom = colors[scene]
    im = Image.new("RGB", (W, H))
    px = im.load()
    for y in range(H):
        t = y / (H - 1)
        color = tuple(int(a * (1 - t) + b * t) for a, b in zip(top, bottom))
        for x in range(W):
            px[x, y] = color
    return im, ImageDraw.Draw(im)


def starfield(d, seed, horizon=380):
    rng = random.Random(seed)
    for _ in range(90):
        x, y = rng.randint(0, W), rng.randint(20, horizon)
        r = rng.choice((1, 1, 2, 3))
        d.ellipse((x-r, y-r, x+r, y+r), fill=(182, 191, 188))


def city(d, seed, horizon=460):
    rng = random.Random(seed)
    for x in range(-20, W, 70):
        height = rng.randrange(80, 245)
        d.rectangle((x, horizon-height, x+65, horizon), fill=(33, 43, 56))
        for wy in range(horizon-height+16, horizon-12, 34):
            for wx in range(x+11, x+65, 18):
                if rng.random() > .36:
                    d.rectangle((wx, wy, wx+7, wy+13), fill=(169, 145, 112))
    d.rectangle((0, horizon, W, H), fill=(54, 58, 65))
    d.line((0, horizon, W, horizon), fill=(185, 160, 135), width=5)
    for x in range(40, W, 135):
        d.line((x, 610, x+75, 610), fill=(130, 129, 118), width=8)


def ufo(d):
    starfield(d, 31)
    city(d, 32)
    d.line((355, 640, 430, 490), fill=(215, 204, 175), width=9)
    d.line((790, 640, 865, 490), fill=(215, 204, 175), width=9)
    d.ellipse((360, 468, 828, 525), fill=(28, 31, 43))
    d.ellipse((350, 315, 842, 474), fill=(149, 167, 157),
              outline=(228, 224, 194), width=8)
    d.ellipse((450, 245, 745, 405), fill=(95, 160, 175),
              outline=(203, 231, 221), width=6)
    d.arc((472, 265, 722, 422), 185, 335, fill=(245, 248, 229), width=5)
    d.ellipse((410, 353, 795, 428), fill=(47, 65, 75),
              outline=(210, 220, 210), width=9)
    for x in (435, 505, 576, 647, 718, 786):
        d.ellipse((x-12, 383, x+12, 407), fill=(244, 197, 110))
    # Infringement ticket attached to the spaceship, not to a character.
    d.polygon(((739, 351), (803, 360), (793, 448), (733, 440)),
              fill=(233, 166, 112))
    for y in (371, 387, 405):
        d.line((748, y, 785, y+4), fill=(94, 73, 69), width=4)
    d.ellipse((841, 425, 924, 511), fill=(158, 189, 151),
              outline=(224, 223, 186), width=6)
    d.ellipse((855, 457, 878, 471), fill=(42, 61, 65))
    d.ellipse((888, 457, 911, 471), fill=(42, 61, 65))
    for line in ((872, 508, 849, 568), (904, 508, 924, 567),
                 (851, 530, 815, 500)):
        d.line(line, fill=(157, 188, 150), width=15)
    d.line((213, 250, 213, 510), fill=(197, 204, 193), width=16)
    d.ellipse((151, 204, 275, 328), fill=(213, 215, 207),
              outline=(218, 144, 120), width=15)
    d.line((174, 302, 253, 223), fill=(214, 136, 116), width=13)
    d.line((199, 250, 224, 277), fill=(67, 91, 100), width=10)


def nails(d):
    d.rectangle((70, 110, 1130, 620), fill=(152, 178, 175),
                outline=(214, 210, 192), width=14)
    for x in (115, 460, 805):
        d.rectangle((x, 160, x+296, 464), fill=(52, 83, 98),
                    outline=(204, 206, 185), width=11)
        d.rectangle((x+12, 310, x+284, 452), fill=(81, 112, 119))
    d.rectangle((40, 570, 1160, 675), fill=(58, 78, 89))
    d.line((0, 104, 1200, 104), fill=(225, 214, 188), width=20)
    d.line((650, 104, 650, 240), fill=(216, 202, 169), width=13)
    d.rounded_rectangle((584, 219, 716, 394), radius=52,
                        outline=(229, 214, 184), width=20)
    d.rounded_rectangle((613, 245, 687, 357), radius=30,
                        outline=(82, 110, 117), width=15)
    for rect, color in [
        ((584, 304, 618, 355), (209, 139, 154)),
        ((608, 319, 641, 373), (225, 181, 114)),
        ((644, 331, 675, 382), (153, 171, 168)),
        ((677, 309, 709, 362), (189, 145, 188)),
    ]:
        d.rounded_rectangle(rect, radius=12, fill=color,
                            outline=(250, 225, 197), width=4)
        d.arc((rect[0]-7, rect[1]-4, rect[2]+8, rect[3]+11),
              180, 355, fill=(225, 235, 217), width=2)
    d.arc((470, 330, 760, 640), 195, 315, fill=(214, 225, 210), width=3)
    d.arc((498, 344, 780, 650), 195, 315, fill=(183, 207, 204), width=3)
    for x in (160, 910):
        d.polygon(((x, 568), (x+46, 566), (x+52, 640), (x-14, 640)),
                  fill=(43, 48, 60))
        d.ellipse((x-18, 627, x+63, 657), fill=(31, 39, 51))


def broom(d):
    starfield(d, 114, 290)
    city(d, 115, 415)
    d.rectangle((0, 487, 1200, 675), fill=(76, 70, 77))
    d.rectangle((0, 175, 395, 625), fill=(65, 61, 72),
                outline=(139, 114, 105), width=15)
    d.rectangle((90, 240, 335, 620), fill=(97, 80, 81),
                outline=(194, 160, 133), width=9)
    d.ellipse((270, 345, 332, 410), fill=(204, 180, 143))
    d.ellipse((115, 354, 205, 460), fill=(181, 153, 146))
    d.polygon(((84, 373), (160, 205), (228, 370)), fill=(42, 49, 67))
    d.ellipse((75, 360, 239, 390), fill=(42, 49, 67))
    d.line((143, 453, 153, 553), fill=(42, 49, 67), width=64)
    d.line((160, 480, 247, 510), fill=(150, 132, 138), width=13)
    d.line((610, 260, 740, 551), fill=(195, 153, 111), width=24)
    d.polygon(((684, 500), (765, 480), (795, 614), (705, 631)),
              fill=(204, 168, 107))
    for x in range(695, 790, 15):
        d.line((x, 539, x+12, 613), fill=(139, 107, 75), width=8)
    d.line((709, 574, 642, 641), fill=(186, 159, 132), width=13)
    d.line((739, 571, 819, 637), fill=(186, 159, 132), width=13)
    d.line((712, 415, 840, 424), fill=(186, 159, 132), width=13)
    d.rounded_rectangle((818, 410, 937, 505), radius=12,
                        fill=(43, 52, 64), outline=(180, 159, 127), width=7)
    d.arc((851, 385, 905, 440), 180, 360, fill=(180, 159, 127), width=8)
    d.line((1020, 240, 1020, 550), fill=(190, 189, 174), width=14)
    d.ellipse((964, 205, 1077, 320), fill=(72, 102, 109),
              outline=(213, 191, 153), width=11)


def shadow(d):
    d.polygon(((0, 140), (1200, 140), (1200, 480), (0, 600)),
              fill=(95, 84, 83))
    d.polygon(((0, 600), (1200, 480), (1200, 675), (0, 675)),
              fill=(66, 65, 72))
    d.rectangle((110, 190, 468, 612), fill=(50, 57, 68),
                outline=(154, 137, 122), width=14)
    d.rectangle((180, 240, 415, 610), fill=(80, 90, 99),
                outline=(179, 157, 136), width=9)
    d.line((900, 55, 900, 180), fill=(183, 166, 145), width=10)
    d.ellipse((830, 160, 970, 229), fill=(226, 181, 123))
    d.ellipse((806, 212, 994, 270), fill=(249, 215, 148))
    d.ellipse((306, 225, 402, 323), fill=(174, 158, 147))
    d.polygon(((300, 324), (420, 325), (456, 508), (280, 510)),
              fill=(62, 71, 86))
    d.line((318, 470, 293, 608), fill=(46, 52, 66), width=38)
    d.line((397, 470, 422, 608), fill=(46, 52, 66), width=38)
    d.line((420, 355, 505, 425), fill=(174, 158, 147), width=23)
    d.rounded_rectangle((472, 416, 549, 520), radius=7,
                        fill=(111, 80, 71))
    d.ellipse((665, 517, 835, 593), fill=(32, 36, 47))
    d.polygon(((620, 566), (875, 560), (1020, 617), (970, 649),
               (745, 628), (578, 640)), fill=(32, 36, 47))
    d.line((770, 589, 878, 655), fill=(32, 36, 47), width=40)
    d.line((789, 582, 668, 661), fill=(32, 36, 47), width=39)
    d.line((665, 582, 573, 508), fill=(32, 36, 47), width=29)
    d.line((817, 577, 910, 482), fill=(32, 36, 47), width=29)
    d.ellipse((1080, 595, 1134, 626), fill=(191, 163, 134))
    d.ellipse((1120, 618, 1180, 650), fill=(191, 163, 134))


def draw_scene(name):
    from PIL import Image, ImageDraw
    if name not in SCENES:
        raise ValueError("Unknown original X visual: " + name)
    scene = SCENES[name]
    im, d = base(scene)
    {"ufo": ufo, "nails": nails, "broom": broom, "shadow": shadow}[scene](d)
    rng = random.Random(int(hashlib.sha256(name.encode()).hexdigest()[:12], 16))
    grain = Image.new("RGBA", im.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(grain)
    for _ in range(6000):
        x, y = rng.randrange(W), rng.randrange(H)
        color = (226, 222, 205, 22) if rng.random() > .5 else (18, 26, 37, 22)
        g.point((x, y), fill=color)
    return Image.alpha_composite(im.convert("RGBA"), grain).convert("RGB")


def render(root=ROOT):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for name in SCENES:
        path = root / (name + ".png")
        if path.exists():
            # An already-hosted image can be referenced by an accepted Buffer ID.
            # Never silently replace it with newly rendered bytes.
            print("Preserved existing X illustration:", path)
            continue
        draw_scene(name).save(path, format="PNG", optimize=True)
        if path.stat().st_size > 1_000_000:
            raise ValueError("X image exceeds 1 MB safety limit: " + name)
        print("Created original X illustration:", path)


if __name__ == "__main__":
    render()
