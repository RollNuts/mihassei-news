#!/usr/bin/env python3
"""Original variable-length Nejimachi manga renderer. Pillow only, no image/API credits."""
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
INK = "#23334B"
PALE = "#F6F2E9"
COPPER = "#CF936D"
LAMP = "#7ED6DE"
FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)


def font(sz):
    for name in FONT_PATHS:
        if Path(name).exists():
            return ImageFont.truetype(name, sz)
    raise RuntimeError("Install fonts-noto-cjk before rendering manga")


def textlines(draw, s, f, limit):
    lines = []
    for raw in s.split("\n"):
        buf = ""
        for char in raw:
            trial = buf + char
            if buf and draw.textbbox((0,0), trial, font=f)[2] > limit:
                lines.append(buf)
                buf = char
            else:
                buf = trial
        if buf:
            lines.append(buf)
    return lines


def gear(d, x, y, r, color, spokes=8):
    for i in range(spokes):
        a = i*math.tau/spokes
        px, py = x + math.cos(a)*r, y + math.sin(a)*r
        d.rounded_rectangle((px-8,py-8,px+8,py+8),3,fill=color)
    d.ellipse((x-r+6,y-r+6,x+r-6,y+r-6),outline=color,width=6)
    d.ellipse((x-r*.47,y-r*.47,x+r*.47,y+r*.47),outline=color,width=5)


def line(d, points, fill, width):
    d.line(points,fill=fill,width=width,joint="curve")


def scene(d, rect, typ, sign, flip):
    x,y,w,h=rect
    horizon=y+int(h*.71)
    d.rounded_rectangle((x,y,x+w,y+h),15,fill="#DBE6E7",outline=INK,width=5)
    # Hand-built mechanical skyline, not a source IP image.
    d.rectangle((x+5,y+5,x+w-5,horizon),fill="#DDE9E9")
    for k in range(7):
        xx=x+int((k+.15)*(w/7))
        tall=(k*43+flip*19)%74
        d.rectangle((xx,horizon-168-tall,xx+w//13,horizon),fill="#9FB3BC")
        for j in range(2):
            wy=horizon-148-tall+j*47
            d.rectangle((xx+12,wy,xx+27,wy+20),fill="#F3DB9F")
    line(d,[(x,horizon-122),(x+w,horizon-122)],"#8DA3B3",9)
    line(d,[(x+int(w*.2),y+8),(x+int(w*.2),horizon)],"#879CAB",11)
    line(d,[(x+int(w*.75),y+6),(x+int(w*.75),horizon-46)],"#879CAB",8)
    d.arc((x+int(w*.1),y+int(h*.07),x+int(w*.55),y+int(h*.55)),30,185,fill="#869AAA",width=9)
    gear(d,x+w-64,y+90,min(35,w//12),"#91A6B4")
    d.rectangle((x+5,horizon,x+w-5,y+h-6),fill="#B6C7C5")
    line(d,[(x,horizon),(x+w,horizon)],"#708998",4)

    if typ=="station":
        px=x+int(w*.58)
        d.rounded_rectangle((px-70,horizon-179,px+145,horizon-2),12,fill="#566A7B",outline=INK,width=5)
        d.rectangle((px-28,horizon-116,px+91,horizon-60),fill="#F5E8C8")
        d.rounded_rectangle((px+24,horizon-31,px+97,horizon+14),5,fill="#F4D7A3")
    elif typ=="lamp":
        px=x+int(w*.54)
        d.ellipse((px-91,y+30,px+96,y+110),fill="#F1D892",outline="#506E81",width=7)
        line(d,[(px,y+5),(px,y+34)],"#536B7E",9)
        d.arc((px-106,y+16,px+110,y+131),0,180,fill=LAMP,width=11)
    elif typ=="workshop":
        py=horizon-85
        d.rounded_rectangle((x+w//3,py,x+w-35,py+27),5,fill="#BB855B",outline=INK,width=4)
        for dx in [w//3+20,w-70]:
            line(d,[(x+dx,py+26),(x+dx,py+94)],INK,6)
        if flip%2==0:
            for dx,dy in [(w//3+18,12),(w//3+58,39),(w//3+108,20)]:
                d.ellipse((x+dx,py+dy,x+dx+16,py+dy+16),fill="#D6A064",outline=INK,width=2)
    if sign:
        fw=min(w-30, 320 if w>650 else 190)
        d.rounded_rectangle((x+17,y+14,x+17+fw,y+61),7,fill="#40576B")
        f=font(26 if w>650 else 20)
        lab=sign[:13]
        d.text((x+31,y+22),lab,font=f,fill="white")


def coords(cx,cy,s,xx,yy):
    return (cx+int(xx*s),cy+int(yy*s))


def figure(d,kind,cx,cy,s=1.0,mood="plain"):
    """Distinct proprietary geometry: copper broken-hex, horizontal one-eye screen, steam plume."""
    def xy(a,b):
        return coords(cx,cy,s,a,b)
    def box(a,b,c,e,**kwargs):
        d.rectangle((*xy(a,b),*xy(c,e)),**kwargs)
    def ell(a,b,c,e,**kwargs):
        d.ellipse((*xy(a,b),*xy(c,e)),**kwargs)
    def ln(a,b,c,e,color=INK,width=6):
        line(d,[xy(a,b),xy(c,e)],color,max(2,int(width*s)))
    face=INK
    if kind=="neji":
        # Wide hexagonal head, visibly notched top-left, yellow scarf and jacket.
        polygon=[xy(-81,-78),xy(-34,-112),xy(38,-112),xy(86,-66),xy(88,12),xy(46,51),xy(-41,51),xy(-88,5)]
        d.polygon(polygon,fill=COPPER)
        d.line(polygon+[polygon[0]],fill=INK,width=max(3,int(6*s)))
        d.polygon([xy(-83,-81),xy(-42,-113),xy(-27,-86),xy(-59,-68)],fill="#DBE6E7")
        ell(53,-126,78,-97,fill="#D6B878",outline=INK,width=max(2,int(3*s)))
        ln(65,-96,65,-79)
        # Asymmetric eyes and mouth
        ell(-37,-37,-25,-23,fill=face)
        ell(25,-38,37,-24,fill=face)
        if mood in ("sad","confused","sleepy"):
            ln(-19,5,18,8,width=4)
        elif mood=="wink":
            ln(-38,-30,-21,-30,width=4)
            d.arc((*xy(-22,-6),*xy(22,21)),0,155,fill=face,width=max(2,int(4*s)))
        else:
            d.arc((*xy(-23,-8),*xy(22,24)),0,170,fill=face,width=max(2,int(4*s)))
        d.rounded_rectangle((*xy(-42,58),*xy(45,143)),radius=max(3,int(12*s)),fill="#47647C",outline=INK,width=max(2,int(5*s)))
        d.polygon([xy(-58,54),xy(56,54),xy(31,80),xy(-53,70)],fill="#F2CE72")
        ln(-42,92,-85,106)
        ln(43,92,82,114)
        ln(-26,141,-34,170)
        ln(24,141,33,170)
    elif kind=="pito":
        # Off-centre single eye on a horizontal glass display, magnetic floating ring.
        d.rounded_rectangle((*xy(-105,-84),*xy(106,56)),radius=max(9,int(25*s)),fill="#7DADB6",outline=INK,width=max(3,int(6*s)))
        d.rounded_rectangle((*xy(-90,-69),*xy(87,40)),radius=max(6,int(17*s)),fill="#D8F0EE",outline="#3A6674",width=max(3,int(5*s)))
        ell(-20,-45,19,-2,fill="#315B6A")
        ell(-8,-39,3,-26,fill="#EFFAF4")
        ln(40,-12,61,-12,color="#315B6A",width=4)
        ln(-50,18,57,18,color="#8EC2C4",width=3)
        ell(-52,63,51,90,outline="#D4E6E2",width=max(3,int(9*s)))
        ln(-73,-103,-38,-84)
        ell(-86,-117,-67,-99,fill="#EBA56E")
        box(-19,47,20,65,fill="#465E6A")
    elif kind=="moku":
        # Faceted asymmetrical steam body; gloves and small boots are character identifiers.
        pts=[(-82,51),(-115,5),(-94,-40),(-58,-56),(-57,-99),(-14,-113),(11,-83),(62,-94),(91,-56),(89,-7),(105,33),(63,69),(-38,77)]
        d.polygon([xy(a,b) for a,b in pts],fill="#BEADD6")
        d.line([xy(a,b) for a,b in pts]+[xy(*pts[0])],fill=INK,width=max(3,int(5*s)))
        ell(-41,-27,-28,-13,fill=face)
        ell(24,-27,37,-13,fill=face)
        if mood=="sad":
            d.arc((*xy(-20,14),*xy(19,39)),180,360,fill=face,width=max(2,int(4*s)))
        else:
            d.arc((*xy(-21,2),*xy(23,37)),0,170,fill=face,width=max(2,int(4*s)))
        ell(-108,45,-71,80,fill="#DDA76C",outline=INK,width=max(2,int(3*s)))
        ell(75,42,112,77,fill="#DDA76C",outline=INK,width=max(2,int(3*s)))
        ell(-61,63,-13,96,fill="#4C5F76")
        ell(15,63,65,96,fill="#4C5F76")
        box(-20,33,23,58,fill="#E8B981")
    else:
        raise ValueError("Unknown figure: "+kind)


def layout(n):
    x0,y0,w,h,g=28,178,1024,1125,14
    if n<=3:
        ph=(h-g*(n-1))//n
        return [(x0,y0+i*(ph+g),w,ph) for i in range(n)]
    tw=(w-g)//2
    if n==4:
        ph=(h-g)//2
        return [(x0+(i%2)*(tw+g), y0+(i//2)*(ph+g),tw,ph) for i in range(n)]
    if n==5:
        first_h=330
        ph=(h-first_h-2*g)//2
        return [(x0,y0,w,first_h)]+[
          (x0+(i%2)*(tw+g),y0+first_h+g+(i//2)*(ph+g),tw,ph)
          for i in range(4)]
    ph=(h-2*g)//3
    return [(x0+(i%2)*(tw+g),y0+(i//2)*(ph+g),tw,ph) for i in range(6)]


def paint_prop(d, prop, cx, cy, s, effect="none"):
    """Small original machine-city objects and visible action cues.

    For the cup, vapor can resemble Moku's uneven outline. It remains ordinary
    steam: the gag is a character's interpretation, not a new creature.
    """
    if effect not in {"none","steam-one","steam-fade","steam-two"}:
        raise ValueError("Unsupported prop effect: "+str(effect))
    if effect != "none" and prop != "cup":
        raise ValueError("Steam effects require a cup")
    stroke = max(2, int(4*s))
    if prop == "none":
        return
    if prop == "bolt":
        d.polygon([(cx-26*s,cy-5*s),(cx-15*s,cy-24*s),(cx+17*s,cy-24*s),
                   (cx+28*s,cy-5*s),(cx+17*s,cy+13*s),(cx-15*s,cy+13*s)],
                  fill="#D49B69")
        d.ellipse((cx-11*s,cy-11*s,cx+11*s,cy+10*s),fill="#668999",outline=INK,width=stroke)
    elif prop == "ticket":
        d.rounded_rectangle((cx-46*s,cy-25*s,cx+46*s,cy+25*s),radius=max(3,int(5*s)),fill="#F8E9B7",outline=INK,width=stroke)
        d.line((cx-12*s,cy-17*s,cx-12*s,cy+18*s),fill="#BF976B",width=stroke)
        d.ellipse((cx+9*s,cy-8*s,cx+29*s,cy+10*s),outline="#697C8A",width=stroke)
    elif prop == "lamp":
        d.ellipse((cx-38*s,cy-24*s,cx+38*s,cy+17*s),fill="#BDE1EA",outline=INK,width=stroke)
        d.ellipse((cx-22*s,cy-12*s,cx+22*s,cy+12*s),fill="#F5D77E")
        d.line((cx,cy-40*s,cx,cy-25*s),fill=INK,width=stroke)
    elif prop == "cup":
        d.rounded_rectangle((cx-30*s,cy-22*s,cx+22*s,cy+29*s),radius=max(3,int(7*s)),fill="#FFF3E0",outline=INK,width=stroke)
        d.arc((cx+8*s,cy-18*s,cx+42*s,cy+16*s),290,85,fill=INK,width=stroke)
        if effect == "none":
            d.arc((cx-18*s,cy-62*s,cx+12*s,cy-28*s),0,160,fill="#A4BED2",width=stroke)
        elif effect == "steam-fade":
            # Specks drift away: no dialogue is needed to see the loss.
            for dx,dy,r in ((-17,-46,8),(10,-64,6),(21,-85,3)):
                d.ellipse((cx+(dx-r)*s,cy+(dy-r)*s,
                           cx+(dx+r)*s,cy+(dy+r)*s),fill="#D6CDD9")
        else:
            # A deliberately irregular original outline: the steam merely
            # *resembles* Moku, with no proof it is alive.
            offsets=[(-9,-74,1.03)] if effect=="steam-one" else [(-43,-78,.78),(30,-83,.78)]
            for dx,dy,scale in offsets:
                px=cx+dx*s
                py=cy+dy*s
                ss=s*scale
                p=[(-39,12),(-49,-9),(-33,-27),(-12,-27),(-7,-43),
                   (18,-37),(25,-24),(41,-19),(42,4),(26,23),(-21,25)]
                points=[(px+a*ss,py+b*ss) for a,b in p]
                d.polygon(points,fill="#BBAED0",outline="#776F8E",width=max(2,int(3*s)))
                for ex in (-12,13):
                    d.ellipse((px+(ex-4)*ss,py-7*ss,px+(ex+4)*ss,py+1*ss),fill="#655C78")
                d.arc((px-9*ss,py+2*ss,px+11*ss,py+13*ss),0,165,
                      fill="#776F8E",width=max(1,int(2*s)))
                d.arc((px-7*ss,py+21*ss,px+5*ss,py+54*ss),210,330,
                      fill="#A4BED2",width=stroke)
    else:
        raise ValueError("Unsupported narrative prop: " + str(prop))


def paint_panel(d,p,rect,idx):
    x,y,w,h=rect
    scene(d,rect,p["scene"],p.get("sign",""),idx)
    wide=w>=750
    scale=min(1.0, (h-124)/350, w/710) if wide else min(.87,(h-140)/360,w/630)
    # Wide shots show the district; close shots make facial acting legible.
    shot = p.get("shot", "medium")
    if shot not in {"wide", "medium", "close"}:
        raise ValueError("Unsupported visual shot: " + str(shot))
    scale *= {"wide": 0.72, "medium": 1.0, "close": 1.14}[shot]
    # Silent beats have more room for acting (no caption bar).
    if p.get("silent",False):
        base_y=y+int(h*(.47 if wide else .57))
    else:
        base_y=y+int(h*(.31 if wide and h<430 else (.43 if wide else .48)))
    focus=p["focus"]
    if p.get("secondary"):
        px=x+int(w*.68)
        figure(d,focus,px,base_y,scale,p.get("mood","plain"))
        figure(d,p["secondary"],x+int(w*.28),base_y+int(24*scale),scale*.66,"plain")
    else:
        figure(d,focus,x+int(w*.54),base_y,scale,p.get("mood","plain"))
    # Make a tangible object part of the scene, not just an explanatory subtitle.
    prop=p.get("prop","none")
    if prop!="none":
        px=x+int(w*(.80 if not wide else .84))
        py=y+int(h*.65)
        paint_prop(d,prop,px,py,max(.65,scale*.9),p.get("effect","none"))
    # Silence is a genuine acting beat: no empty dialogue strip.
    if p.get("silent",False):
        if p.get("line","").strip():
            raise ValueError("Silent panel must not contain spoken dialogue")
        return
    # Narrative text strip: accessible readable lettering rather than cramped speech bubbles.
    bh=119 if wide else 123
    d.rounded_rectangle((x+9,y+h-bh-9,x+w-9,y+h-9),radius=10,fill="#FFF8EB",outline="#6B7783",width=2)
    dial=p["line"]
    f=font(37 if wide else 26)
    lines=textlines(d,dial,f,w-48)
    if len(lines)>3:
        raise ValueError("Unrenderable dialogue: "+dial)
    line_h=46 if wide else 35
    pos=y+h-bh+int((bh-len(lines)*line_h)/2)-3
    for row in lines:
        d.text((x+22,pos),row,font=f,fill=INK)
        pos+=line_h
    # Micro caption only, not the same line twice.
    if p.get("caption") and wide:
        txt=p["caption"][:25]
        d.text((x+28,y+h-bh-34),txt,font=font(19),fill="#344E62")


def draw_manga(post):
    n=len(post["panels"])
    if n not in range(2,7):
        raise ValueError("Manga must have 2-6 panels")
    im=Image.new("RGB",(1080,1350),PALE)
    d=ImageDraw.Draw(im)
    d.rounded_rectangle((18,19,1062,160),radius=14,fill="#273A51")
    d.text((38,36),"ネジまち",font=font(65),fill="#F7D6A0")
    d.text((412,36),f"第{post['episode']:02d}話",font=font(36),fill="#B2DCE3")
    d.text((412,88),post["title"],font=font(42),fill="white")
    for k,p in enumerate(post["panels"]):
        paint_panel(d,p,layout(n)[k],k)
    d.text((28,1320),"NEJIMACHI   /   ORIGINAL MACHINE-CITY MANGA",font=font(18),fill="#5E7080")
    d.text((1002,1320),f"{post['episode']:02d}",font=font(18),fill="#5E7080")
    return im


def main():
    posts=json.loads((HERE/"posts.json").read_text(encoding="utf-8"))["posts"]
    out=HERE/"assets"
    out.mkdir(parents=True,exist_ok=True)
    for post in posts:
        if post.get("series")!="nejimachi":
            continue
        if post["kind"]!="comic":
            continue
        path=out/(post["id"]+".png")
        # Never quietly redraw an image already linked by Buffer/X; append-only
        # scripts and GitHub history are the source of truth for released art.
        if path.is_file():
            print("Preserved published/approved manga image:",post["id"])
            continue
        image=draw_manga(post)
        image.save(path,format="PNG",optimize=True)
        if path.stat().st_size>2_000_000:
            raise ValueError("IP manga PNG exceeded 2MB: "+post["id"])
        print("Rendered",post["id"],len(post["panels"]),"panels",path.stat().st_size,"bytes")


if __name__=="__main__":
    main()
