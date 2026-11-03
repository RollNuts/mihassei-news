#!/usr/bin/env python3
"""Render lightweight, deterministic four-panel comics without generative-image APIs."""
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "posts.json"
OUT = HERE / "assets"
FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def font(size):
    for path in FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("Japanese font not found; install fonts-noto-cjk")


def wrap_text(draw, text, f, width):
    result, line = [], ""
    for ch in text:
        candidate = line + ch
        if line and draw.textbbox((0, 0), candidate, font=f)[2] > width:
            result.append(line)
            line = ch
        else:
            line = candidate
    if line:
        result.append(line)
    return result


def star_points(cx, cy, r, inner=0.45):
    points = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * inner
        points.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return points


def character(draw, motif, cx, cy, beat):
    """Simple original vector characters, varying pose by panel."""
    ink = "#26354A"
    if motif == "star":
        draw.line((cx - 160, cy + 60, cx - 100, cy + 30), fill="#9ac4e5", width=8)
        draw.line((cx - 175, cy + 15, cx - 105, cy - 1), fill="#9ac4e5", width=7)
        draw.polygon(star_points(cx, cy, 104), fill="#FFD45C", outline=ink, width=6)
        draw.ellipse((cx-34, cy-9, cx-22, cy+4), fill=ink)
        draw.ellipse((cx+22, cy-9, cx+34, cy+4), fill=ink)
        draw.arc((cx-20,cy-4,cx+22,cy+35), 10, 160, fill=ink, width=5)
        if beat == 2:
            draw.arc((cx-125,cy-115,cx+135,cy+125), 30, 170, fill="#ec7482", width=8)
    elif motif == "alien":
        draw.ellipse((cx-120,cy-95,cx+120,cy+115), fill="#a4dfb1", outline=ink, width=7)
        draw.line((cx-40,cy-89,cx-60,cy-135), fill=ink, width=7)
        draw.line((cx+40,cy-89,cx+60,cy-135), fill=ink, width=7)
        draw.ellipse((cx-72,cy-147,cx-48,cy-123), fill="#F4B95D")
        draw.ellipse((cx+48,cy-147,cx+72,cy-123), fill="#F4B95D")
        draw.ellipse((cx-72,cy-16,cx-24,cy+42), fill=ink)
        draw.ellipse((cx+24,cy-16,cx+72,cy+42), fill=ink)
        draw.arc((cx-30,cy+29,cx+32,cy+78),0,180,fill=ink,width=6)
        if beat == 3:
            draw.line((cx-143,cy+90,cx-104,cy+65),fill=ink,width=7)
    elif motif == "vampire":
        draw.polygon([(cx-120,cy+140),(cx-85,cy-30),(cx+80,cy-30),(cx+120,cy+140),(cx,cy+74)],fill="#6E5B97",outline=ink,width=5)
        draw.ellipse((cx-85,cy-112,cx+85,cy+60),fill="#FAE5DB",outline=ink,width=6)
        draw.polygon([(cx-82,cy-40),(cx-70,cy-112),(cx,cy-145),(cx+75,cy-112),(cx+85,cy-40),(cx+40,cy-70),(cx,cy-53),(cx-35,cy-70)],fill="#3C3B50")
        draw.ellipse((cx-37,cy-12,cx-25,cy),fill=ink)
        draw.ellipse((cx+25,cy-12,cx+37,cy),fill=ink)
        draw.line((cx-26,cy+31,cx+26,cy+31),fill=ink,width=5)
        draw.polygon([(cx-19,cy+34),(cx-8,cy+51),(cx-2,cy+34)],fill="white")
        draw.polygon([(cx+5,cy+34),(cx+12,cy+51),(cx+19,cy+34)],fill="white")
    elif motif == "angel":
        draw.ellipse((cx-150,cy-32,cx-30,cy+92),fill="#eff6ff",outline="#a5bfd8",width=5)
        draw.ellipse((cx+30,cy-32,cx+150,cy+92),fill="#eff6ff",outline="#a5bfd8",width=5)
        draw.ellipse((cx-75,cy-75,cx+75,cy+90),fill="#f9e1cc",outline=ink,width=5)
        draw.ellipse((cx-93,cy-124,cx+93,cy-102),outline="#e8be4c",width=11)
        draw.ellipse((cx-32,cy-9,cx-22,cy+3),fill=ink)
        draw.ellipse((cx+22,cy-9,cx+32,cy+3),fill=ink)
        draw.arc((cx-30,cy+6,cx+30,cy+43),0,180,fill=ink,width=5)
        draw.rounded_rectangle((cx-52,cy+84,cx+52,cy+140),15,fill="#eef6fb",outline=ink,width=4)
    elif motif == "nessie":
        draw.arc((cx-185,cy+57,cx+190,cy+145), 190, 360, fill="#58b7d6", width=15)
        draw.ellipse((cx-120,cy+10,cx+155,cy+115),fill="#65c8ab",outline=ink,width=6)
        draw.rounded_rectangle((cx+58,cy-102,cx+98,cy+74),20,fill="#65c8ab",outline=ink,width=6)
        draw.ellipse((cx+45,cy-133,cx+151,cy-61),fill="#65c8ab",outline=ink,width=6)
        draw.ellipse((cx+115,cy-108,cx+126,cy-95),fill=ink)
        draw.arc((cx+116,cy-91,cx+138,cy-75),0,150,fill=ink,width=4)
        for offset in (-105,0,105):
            draw.arc((cx+offset-80,cy+105,cx+offset+65,cy+145),190,350,fill="#359dbc",width=5)
    elif motif == "dinosaur":
        draw.ellipse((cx-105,cy-25,cx+85,cy+112),fill="#8fcb80",outline=ink,width=6)
        draw.rounded_rectangle((cx+45,cy-112,cx+85,cy+55),15,fill="#8fcb80",outline=ink,width=5)
        draw.ellipse((cx+48,cy-150,cx+140,cy-80),fill="#8fcb80",outline=ink,width=5)
        draw.ellipse((cx+110,cy-130,cx+120,cy-120),fill=ink)
        draw.polygon([(cx-90,cy+20),(cx-170,cy+95),(cx-125,cy+100),(cx-57,cy+61)], fill="#8fcb80",outline=ink,width=4)
        draw.line((cx-37,cy+100,cx-37,cy+141),fill=ink,width=10)
        draw.line((cx+53,cy+100,cx+53,cy+141),fill=ink,width=10)
        for x in (-75,-37,0):
            draw.polygon([(cx+x-12,cy-15),(cx+x+6,cy-75),(cx+x+25,cy-17)],fill="#5c9d79")
    else:
        draw.ellipse((cx-90,cy-90,cx+90,cy+90),fill="#ffd99e",outline=ink,width=6)
        draw.ellipse((cx-35,cy-10,cx-23,cy+2),fill=ink)
        draw.ellipse((cx+23,cy-10,cx+35,cy+2),fill=ink)


def draw_comic(item):
    image = Image.new("RGB", (1080, 1350), "#F6F0E5")
    d = ImageDraw.Draw(image)
    brand = "どうでもいい世界"
    d.text((34,30),brand,font=font(34),fill="#486E78")
    title = item["title"]
    d.text((34,74),title,font=font(54),fill="#26354A")
    d.line((30,142,1050,142),fill="#26354A",width=5)

    left, top, gap = 29, 160, 18
    pw, ph = (1080-58-gap)//2, 560
    for n, text in enumerate(item["panels"]):
        x = left + (n%2)*(pw+gap)
        y = top + (n//2)*(ph+gap)
        d.rounded_rectangle((x,y,x+pw,y+ph),radius=17,fill="white",outline="#26354A",width=5)
        d.rounded_rectangle((x+15,y+15,x+72,y+58),radius=9,fill="#26354A")
        d.text((x+27,y+17),str(n+1),font=font(29),fill="white")
        character(d,item.get("motif",""),x+pw//2,y+225,n)
        d.line((x+18,y+377,x+pw-18,y+377),fill="#DDE5E8",width=3)
        f=font(35)
        lines=wrap_text(d,text,f,pw-52)
        while len(lines)>3:
            f=font(f.size-2)
            lines=wrap_text(d,text,f,pw-52)
            if f.size<22:
                raise ValueError("Panel text too long: "+text)
        total=len(lines)*48
        ty=y+402+(125-total)//2
        for line in lines:
            bbox=d.textbbox((0,0),line,font=f)
            d.text((x+(pw-(bbox[2]-bbox[0]))/2,ty),line,font=f,fill="#26354A")
            ty+=48
    d.text((30,1319),"DOUDEMOII SEKAI  •  ORIGINAL 4-KOMA",font=font(17),fill="#75818B")
    return image


def main():
    items=json.loads(SOURCE.read_text(encoding="utf-8"))["posts"]
    OUT.mkdir(parents=True,exist_ok=True)
    for item in items:
        if item["kind"] != "comic" or item.get("series") == "nejimachi":
            continue
        path=OUT/(item["id"]+".png")
        image=draw_comic(item)
        image.save(path,format="PNG",optimize=True)
        if path.stat().st_size > 5_000_000:
            raise ValueError("Image exceeds X limit: "+str(path))
        print(path.relative_to(HERE),path.stat().st_size)


if __name__ == "__main__":
    main()
