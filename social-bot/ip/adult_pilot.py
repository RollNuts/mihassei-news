#!/usr/bin/env python3
"""Non-canonical ADULT visual pilot for Nejimachi, not a publishable episode.

Creates two locally reproducible preview PNGs for human critique.
No paid service, no generative model, no automatic story/queue mutations.
Existing canonical story/art images are never read or modified.

Usage: python social-bot/ip/adult_pilot.py
"""
from __future__ import annotations

import hashlib
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "previews"

PAPER = (230, 222, 205)
DARK = (35, 44, 50)
INK = (28, 33, 38)
SMOKE = (98, 109, 114)
BRONZE = (142, 104, 79)
COPPER = (169, 117, 83)
GOLD = (173, 146, 91)
VIOLET = (120, 104, 130)
MILK = (221, 211, 194)
PITO = (95, 133, 143)
PALE = (198, 188, 169)


def rng(key):
    n = hashlib.sha256(str(key).encode("utf-8")).digest()
    return random.Random(int.from_bytes(n[:8], "big"))


def font(size):
    for path in (
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("fonts-noto-cjk needed for Japanese dialogue")


def rough(d, pts, key, width=3, color=INK, jitter=1.1):
    """Repeatable slight hand pressure / line inaccuracy, not random static."""
    rr = rng(key)
    points = []
    for i in range(len(pts)-1):
        ax, ay = pts[i]
        bx, by = pts[i+1]
        segments = max(4, int(math.hypot(bx-ax,by-ay) / 9))
        for j in range(segments):
            t = j / segments
            noise = math.sin(math.pi*t) * jitter
            points.append((round(ax+(bx-ax)*t+rr.gauss(0,noise)),
                           round(ay+(by-ay)*t+rr.gauss(0,noise))))
    points.append(tuple(map(int,pts[-1])))
    shadow = tuple(round(.45*a+.55*b) for a,b in zip(color,PAPER))
    d.line([(x+1,y+1) for x,y in points],fill=shadow,width=width+2,joint="curve")
    d.line(points,fill=color,width=width,joint="curve")


def irregular(d, poly, fill, key, ink=INK, width=4):
    d.polygon(poly,fill=fill)
    rough(d,[*poly,poly[0]],key,width,ink,1.5)


def ink_text(d, at, message, size=26, fill=INK):
    d.text(at,message,font=font(size),fill=fill,stroke_width=0)


def paper_texture(image, key, count=25000):
    d = ImageDraw.Draw(image)
    rr = rng(key)
    for i in range(count):
        x=rr.randrange(image.width)
        y=rr.randrange(image.height)
        if rr.random()<.65:
            dark=rr.randrange(5,19)
            px=tuple(max(0,c-dark) for c in PAPER)
        else:
            px=(247,239,221)
        d.point((x,y),fill=px)


def boxframe(d, box, name, color=INK):
    x1,y1,x2,y2=box
    rough(d,[(x1,y1),(x2,y1),(x2,y2),(x1,y2),(x1,y1)],"frame"+name,5,color,1.2)


def old_pipes(d, box, key, perspective=True):
    """Industrial background from 2D shapes, distinct from any media property."""
    x1,y1,x2,y2=box
    rr=rng(key)
    w=x2-x1
    h=y2-y1
    shade=(65,78,80)
    if perspective:
        van=(x1+w*.65,y1+h*.57)
        for i in range(6):
            sx=x1+(i+.1)*w/5
            rough(d,[(sx,y1),(sx+(van[0]-sx)*.6,van[1])],key+"pipe"+str(i),5,shade)
    for i in range(5):
        left=x1+int((i+.1)*w/5)
        rise=(i*51+33)%130+int(h*.34)
        width=max(29,int(w*.115))
        d.rectangle((left,y2-rise,left+width,y2),fill=(89+6*(i%3),105+3*(i%2),109))
        for k in range(3):
            wy=y2-rise+24+k*39
            if wy+17<y2:
                d.rectangle((left+11,wy,left+20,wy+17),fill=(184,153,96))
    for p in (0.1,.82):
        xx=x1+w*p
        rough(d,[(xx,y1),(xx+35,y1+h*.52),(xx+70,y2)],key+"tube"+str(p),11,(73,94,97),2.1)
        for j in (0.15,0.38,0.61):
            py=y1+h*j
            d.ellipse((xx+13,py-4,xx+26,py+9),fill=(154,141,114),outline=INK,width=1)
    # randomish oil streaks / metal hatching, kept subtle
    for i in range(38):
        xx=rr.randint(int(x1+4),int(x2-6))
        yy=rr.randint(int(y1+7),int(y2-7))
        rough(d,[(xx,yy),(xx+rr.randint(5,17),yy-rr.randint(5,13))],
              key+"oil"+str(i),1,(109,113,105),.4)


def cog(d,x,y,r,key):
    for j in range(10):
        a=2*math.pi*j/10
        px=x+math.cos(a)*r
        py=y+math.sin(a)*r
        d.rectangle((px-8,py-8,px+8,py+8),fill=SMOKE)
    d.ellipse((x-r,y-r,x+r,y+r),outline=(115,128,127),width=8)
    d.ellipse((x-r*.4,y-r*.4,x+r*.4,y+r*.4),outline=INK,width=5)


def neji(d,x,y,s,view="front",emotion="plain",pose="rest"):
    """Oxidized asymmetric hexagon with notch, ONE right bolt, worn scarf."""
    p=lambda a,b:(x+a*s,y+b*s)
    body=[p(-26,25),p(26,25),p(32,100),p(-29,100)]
    irregular(d,body,(63,83,95),"neji-bod"+str(x),width=max(2,int(s*4)))
    d.polygon([p(-37,35),p(34,35),p(14,51),p(-33,54)],fill=(176,150,91))
    rough(d,[p(-37,35),p(34,35),p(14,51)],"scarf"+str(x),max(2,int(s*4)),(131,108,64))
    for j,(a,b) in enumerate(((-17,96), (15,98))):
        rr=(-12 if j==0 else 10) if pose=="step" else 0
        rough(d,[p(a,b),p(a+rr,131)],f"neji-foot-{x}-{j}",max(2,int(s*5)),INK)
    arm_end={
        "rest":[(-45,74),(50,73)],
        "signal":[(-38,67),(43,-4)],
        "shout":[(-71,44),(70,37)],
        "slump":[(-35,102),(41,105)],
    }.get(pose,[(-45,74),(50,73)])
    for i,(ex,ey) in enumerate(arm_end):
        st=(-27,56) if i==0 else (27,54)
        rough(d,[p(*st),p(ex,ey)],"n-arm"+str(x)+str(i),max(2,int(5*s)),(78,85,84))
        d.ellipse((*p(ex-4,ey-5),*p(ex+4,ey+5)),fill=(138,103,77))
    head=[p(-61,-49),p(-24,-82),p(25,-83),p(65,-45),
          p(61,-2),p(29,29),p(-27,31),p(-64,-3)]
    irregular(d,head,COPPER,"neji-face"+str(x)+str(y),width=max(2,int(5*s)))
    # notch in the upper-left is permanently visible
    d.polygon([p(-62,-50),p(-24,-83),p(-12,-55),p(-43,-31)],fill=MILK)
    rough(d,[p(-62,-50),p(-24,-83),p(-12,-55),p(-43,-31)],
          "n-notch"+str(x),max(1,int(2*s)),INK)
    d.ellipse((*p(39,-95),*p(57,-76)),fill=GOLD,outline=INK,width=max(1,int(2*s)))
    rough(d,[p(48,-76),p(48,-59)],"bolt-on-top"+str(x),max(1,int(2*s)),INK)
    # Face is restrained, dry, NOT toy/cute. Side view uses one visible eye.
    if view=="side":
        d.ellipse((*p(23,-29),*p(31,-20)),fill=INK)
        if emotion=="shout":
            # A small open side-mouth makes the failed voice input legible.
            d.ellipse((*p(35,-9),*p(49,9)),fill=(68,46,44),outline=INK,
                      width=max(1,int(2*s)))
        else:
            rough(d,[p(31,-6),p(49,-8)],"side-mouth"+str(x),max(1,int(2*s)),INK)
    elif view=="back":
        rough(d,[p(-27,8),p(26,8)],"back-metal"+str(x),2,(127,86,67))
    else:
        for dx in (-25,24):
            d.ellipse((*p(dx-3,-28),*p(dx+3,-21)),fill=INK)
        if emotion=="shout":
            d.ellipse((*p(-8,-2),*p(12,16)),fill=(70,47,49))
        elif emotion=="tired":
            rough(d,[p(-9,5),p(12,6)],"n-mouth"+str(x),max(1,int(2*s)),INK)
        else:
            rough(d,[p(-8,4),p(9,4)],"n-mouth"+str(x),max(1,int(2*s)),INK)
    # scratch marks add age, not rosy cheeks
    for i in range(3):
        rough(d,[p(-44+i*5,4),p(-41+i*5,-2)],f"patina{x}-{i}",1,(114,77,60),.3)


def pito(d,x,y,s=1.0,angle=0):
    """Old floating one-eye display, not a pet or a human substitute."""
    X=lambda a:x+a*s
    Y=lambda b:y+b*s
    body=(X(-92),Y(-53),X(97),Y(47))
    d.rounded_rectangle(body,radius=max(8,int(12*s)),fill=PITO,outline=INK,width=max(2,int(4*s)))
    d.rounded_rectangle((X(-78),Y(-42),X(76),Y(31)),
                        radius=max(5,int(9*s)),fill=(175,188,178),outline=INK,width=max(2,int(3*s)))
    d.ellipse((X(-14),Y(-25),X(25),Y(15)),fill=(46,68,75))
    d.ellipse((X(-1),Y(-19),X(9),Y(-9)),fill=(210,219,203))
    rough(d,[(X(29),Y(-7)),(X(51),Y(-7))],f"pito-eye-{x}",max(1,int(2*s)),INK)
    d.arc((X(-38),Y(44),X(42),Y(81)),10,172,fill=(150,174,177),width=max(2,int(5*s)))
    rough(d,[(X(-75),Y(-56)),(X(-60),Y(-70))],f"pito-scratched-{x}",1,INK)


def ink_note(d,box,lines,key):
    """Industrial callout: no bubbly child speech balloon."""
    x1,y1,x2,y2=box
    d.polygon([(x1,y1),(x2,y1),(x2,y2-14),(x1+18,y2)],
              fill=(226,217,196))
    rough(d,[(x1,y1),(x2,y1),(x2,y2-14),(x1+18,y2),(x1,y1)],
          key,3,INK)
    for j,line in enumerate(lines):
        ink_text(d,(x1+20,y1+11+j*34),line,size=29)


def draw_comic():
    W,H=1080,1410
    im=Image.new("RGB",(W,H),PAPER)
    paper_texture(im,"adult-proof-story",29000)
    d=ImageDraw.Draw(im)
    ink_text(d,(44,20),"ネジまち",55,DARK)
    ink_text(d,(350,44),"画稿試作 ｜ 第0話（非正史）",25,SMOKE)
    ink_text(d,(44,87),"『異常なし』",32,INK)

    # PANEL 1: towering generator, small worker. An extreme scale contrast.
    p1=(43,143,1037,540)
    d.rectangle(p1,fill=(181,180,159))
    old_pipes(d,p1,"top-city")
    cog(d,842,251,113,"central-generator")
    rough(d,[(640,183),(689,470)],"industrial-conduit",24,(65,81,81),2)
    neji(d,165,413,.52,"side","tired","slump")
    d.rectangle((79,447,455,494),fill=(217,205,169),outline=INK,width=3)
    ink_text(d,(93,448),"六番配管街　騒音相談",22,INK)
    ink_text(d,(760,400),"ゴ　ゴ　ゴ",35,(68,67,59))
    ink_note(d,(250,325,478,413),["眠れない……"],"first-complaint")
    boxframe(d,p1,"top")

    # PANEL 2: hand/microphone and bureaucratic invitation; not a frontal pose.
    p2=(43,563,542,891)
    d.rectangle(p2,fill=(179,180,165))
    old_pipes(d,p2,"kiosk-close",perspective=False)
    d.polygon([(120,730),(250,711),(326,833),(170,863)],fill=(108,113,108))
    rough(d,[(122,732),(251,711),(326,832),(170,862)],"desk-close",5)
    d.rounded_rectangle((120,620,453,764),radius=13,fill=(89,106,109),outline=INK,width=5)
    d.rounded_rectangle((144,643,429,736),radius=10,fill=(188,192,169),outline=INK,width=3)
    ink_text(d,(165,660),"音声でどうぞ",28,INK)
    d.ellipse((363,758,400,795),fill=(112,89,75),outline=INK,width=4)
    rough(d,[(380,788),(386,825)],"microphone-stem",5)
    pito(d,420,815,.47)
    ink_note(d,(60,583,283,647),["『ご用件は？』"],"kiosk-question")
    boxframe(d,p2,"kiosk")

    # PANEL 3: side view and a noisy face-to-kiosk appeal.
    p3=(563,563,1037,891)
    d.rectangle(p3,fill=(147,149,138))
    old_pipes(d,p3,"noise-close",perspective=False)
    neji(d,778,747,1.12,"side","shout","shout")
    ink_text(d,(612,592),"ガガガガ",32,(58,60,58))
    ink_text(d,(823,655),"ギギギ",27,(75,72,64))
    rough(d,[(661,705),(642,723),(662,738),(636,757)],
          "noise-wave",7,(73,69,58))
    ink_note(d,(650,805,1003,869),["うるさいんです！"],"speech-at-noise")
    boxframe(d,p3,"yell")

    # PANEL 4: oversized official counter, literal visual punchline.
    p4=(43,915,1037,1327)
    d.rectangle(p4,fill=(175,178,158))
    old_pipes(d,p4,"verdict",perspective=False)
    d.rectangle((192,940,967,1177),fill=(213,211,190),outline=INK,width=7)
    # Analogue ticker with patina lines and a single absurdly clean zero.
    ink_text(d,(244,955),"本日の騒音相談",36,INK)
    ink_text(d,(244,1026),"受理件数",32,SMOKE)
    ink_text(d,(668,1001),"0",124,(60,75,79))
    ink_text(d,(821,1056),"件",34,INK)
    rough(d,[(256,1142),(904,1142)],"ticker-line",4,(101,112,112))
    neji(d,312,1256,.57,"back","plain","slump")
    pito(d,840,1239,.58)
    # The setup's sound has not stopped, making the zero visibly ridiculous.
    ink_text(d,(587,1241),"ゴゴゴゴ……",30,(65,70,66))
    boxframe(d,p4,"verdict")

    ink_text(d,(46,1363),"非公開・非正史  /  ADULT COMIC VISUAL STUDY  /  NO BUFFER POST",17,SMOKE)
    return im


def draw_cast_sheet():
    W,H=1320,875
    im=Image.new("RGB",(W,H),PAPER)
    paper_texture(im,"adult-proof-cast",19000)
    d=ImageDraw.Draw(im)
    ink_text(d,(45,21),"ネジまち｜機械都市の住人",46,DARK)
    ink_text(d,(48,88),"大人向け造形検証 ・正史の外見を保持 ・手描き風の試作",23,SMOKE)
    cols=[(35,151,435,812),(458,151,861,812),(883,151,1285,812)]
    descriptions=[
        ("01  ネジ","銅 / 六角 / 欠け / 作業着","無骨な整備員。目より手元。"),
        ("02  ピト","一つ目 / 旧型 / 浮遊","規則を読む端末。感情は微細。"),
        ("03  モク","薄紫 / 蒸気 / 作業手袋","不定形の輪郭とためらう仕草。")
    ]
    for i,(box,data) in enumerate(zip(cols,descriptions)):
        x1,y1,x2,y2=box
        d.rectangle(box,fill=(201,195,179))
        old_pipes(d,box,"cast"+str(i),False)
        d.rectangle((x1+10,y1+10,x2-10,y1+120),fill=(222,214,193))
        ink_text(d,(x1+23,y1+19),data[0],36,INK)
        ink_text(d,(x1+22,y1+75),data[1],20,SMOKE)
        d.rectangle((x1+10,y2-104,x2-10,y2-10),fill=(222,214,193))
        ink_text(d,(x1+22,y2-86),data[2],19,INK)
        boxframe(d,box,str(i))
    neji(d,227,501,1.5,"front","plain","rest")
    pito(d,657,490,1.35)
    # Moku has no baby face, only shadowed angular vapour and work gloves.
    bx,by=1081,503
    poly=[(-82,35),(-116,-17),(-95,-77),(-59,-86),(-40,-125),(-7,-143),
          (19,-116),(68,-125),(103,-81),(98,-25),(119,22),(67,75),(-27,82)]
    points=[(bx+u*1.15,by+v*1.15) for u,v in poly]
    irregular(d,points,(140,126,149),"moku-angular",width=6)
    for dx in (-35,31):
        d.ellipse((bx+dx-5,by-18,bx+dx+5,by-8),fill=INK)
    rough(d,[(bx-15,by+20),(bx+15,by+19)],"moku-mouth",3)
    for q in (-1,1):
        rough(d,[(bx+q*86,by+23),(bx+q*128,by+2)],"moku-arm"+str(q),8,(103,98,96))
        d.ellipse((bx+q*130-15,by-19,bx+q*130+15,by+13),fill=(173,118,78),outline=INK,width=4)
        rough(d,[(bx+q*27,by+65),(bx+q*31,by+124)],"moku-foot"+str(q),7,INK)
    return im


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    for name,im in [
        ("adult_city_quiet_pilot.png", draw_comic()),
        ("adult_cast_sheet.png", draw_cast_sheet()),
    ]:
        path=OUT/name
        im.save(path,optimize=True)
        if path.stat().st_size > 2_000_000:
            raise ValueError("Preview over 2 MB: "+name)
        print(f"Preview only: {path.relative_to(HERE)} ({path.stat().st_size} bytes)")


if __name__=="__main__":
    main()
