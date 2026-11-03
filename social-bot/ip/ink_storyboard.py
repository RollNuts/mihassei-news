"""Nejimachi ink study. Uses only Pillow / standard Python (no generative API)."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from pathlib import Path
import random, math, hashlib

W,H=1080,1350
PAPER=(251,245,229); INK=(45,53,61); VIOLET=(128,111,155); COPPER=(195,131,89); GOLD=(235,191,103)
FONT='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'

def font(n):return ImageFont.truetype(FONT,n)

def seed(s):return random.Random(int.from_bytes(hashlib.sha256(s.encode()).digest()[:8],'big'))

def jitter_path(points,rng,amount=1.7,segments=6):
    result=[]
    for i in range(len(points)-1):
        x,y=points[i];x2,y2=points[i+1]
        n=max(2,int(math.dist((x,y),(x2,y2))/segments))
        for j in range(n):
            t=j/n; amp=amount*math.sin(t*math.pi)*.65+amount*.25
            result.append((x+(x2-x)*t+rng.gauss(0,amp),y+(y2-y)*t+rng.gauss(0,amp)))
    result.append(points[-1]);return result

def skline(d,points,key,width=3,ink=INK,rough=1.1):
    rng=seed(key)
    pts=jitter_path(points,rng,rough,7)
    light=tuple(int(a*.65+b*.35) for a,b in zip(ink,PAPER))
    d.line([(x+2,y+1) for x,y in pts],fill=(*light,180) if d.mode=='RGBA' else light,width=width+2,joint='curve')
    d.line(pts,fill=ink,width=width,joint='curve')
    if width>4:
        d.line([(x+rng.uniform(-1,1),y+rng.uniform(-1,1)) for x,y in pts],fill=tuple(min(255,c+35) for c in ink),width=1,joint='curve')

def strokepoly(d,vertices,key,fill,outline=INK,width=4):
    rng=seed(key)
    d.polygon(vertices,fill=fill)
    skline(d,vertices+[vertices[0]],key+'ink',width,outline,rough=1.8)
    for _ in range(15):
        # translucent specks in the pastel paint
        q=vertices[rng.randrange(len(vertices))]
        dx,dy=rng.randrange(-13,13),rng.randrange(-13,13)
        d.ellipse((q[0]+dx,q[1]+dy,q[0]+dx+max(1,rng.randrange(1,5)),q[1]+dy+max(1,rng.randrange(1,5))),fill=fill)

def rounded(d,rect,key,fill=PAPER,rad=12,width=5):
    x,y,x2,y2=rect
    d.rounded_rectangle(rect,radius=rad,fill=fill)
    rng=seed(key)
    a=[(x+rad,y),(x2-rad,y),(x2,y+rad),(x2,y2-rad),(x2-rad,y2),(x+rad,y2),(x,y2-rad),(x,y+rad),(x+rad,y)]
    skline(d,a,key+'outline',width,INK,rough=1.25)

def hatch(d,region,key,num=26,angle=.8,color=(140,133,139)):
    x,y,w,h=region; rng=seed(key)
    for j in range(num):
        xx=x+rng.uniform(0,w); yy=y+rng.uniform(0,h)
        span=rng.uniform(12,35);dy=span*angle
        skline(d,[(xx,yy),(xx+span,yy-dy)],key+str(j),1,color,rough=.4)

def bg_scene(d,rect,key,mode='wide'):
    x,y,x2,y2=rect; w=x2-x;h=y2-y; rng=seed('bg'+key)
    d.rectangle((x+4,y+4,x2-4,y2-4),fill=(228,229,214))
    if mode=='reaction':
        # pencil diagonal directional hatching for an intimate reaction shot
        for i in range(24):
            sx=x+i*35;skline(d,[(sx,y+22),(sx-50,y2-14)],key+'r'+str(i),1,(181,177,165),1)
        return
    # pipes drawn in strong 1-point perspective, frames intentionally imperfect
    vp=(x+w*.50,y+h*.31)
    for i in range(7):
        sx=x+i*w/6; skline(d,[(sx,y),(sx+(vp[0]-sx)*.68,vp[1])],key+'p'+str(i),4,(138,143,137),2.2)
    for i in range(5):
        shift=rng.randint(-20,20)
        px=x+(i+.15)*w/5
        ht=h*(.31+(i%3)*.13)
        d.polygon([(px,y2),(px,y2-ht),(px+w*.14,y2-ht+shift),(px+w*.14,y2)],fill=(154,165,160))
        for u in range(2):
            d.rectangle((px+u*24+7,y2-ht+26,px+u*24+16,y2-ht+44),fill=(242,214,152))
    for i in (0,.8):
        skline(d,[(x+w*i,y),(x+w*(i+.11),y+h*.6),(x+w*(i+.25),y+h*.76)],key+'pipe'+str(i),8,(101,118,124),2)
    # long hanging cylinder, wires, shadow lines
    skline(d,[(x+w*.68,y),(x+w*.63,y+h*.29),(x+w*.8,y+h*.64)],key+'wire',3,(78,82,80),1.8)
    d.ellipse((x+w*.65,y+h*.21,x+w*.82,y+h*.32),fill=GOLD,outline=INK,width=4)
    hatch(d,(x,y,w,h),key+'hatch',num=24,angle=.6)

def neji(d,x,y,s=1,face='curious',pose='walk',turn=0):
    rng=seed(f'n-{x}-{y}-{face}-{pose}')
    X=lambda n:x+n*s; Y=lambda n:y+n*s
    if pose=='walk':feet=[(-28,110,-48,142),(24,110,34,140)]
    elif pose=='startle':feet=[(-28,110,-61,136),(25,110,65,125)]
    else:feet=[(-25,110,-30,137),(25,110,25,137)]
    for i,(ax,ay,bx,by) in enumerate(feet):skline(d,[(X(ax),Y(ay)),(X(bx),Y(by))],f'foot{i}{x}',5,(78,92,111),1.8)
    # small worker's coveralls, scarf
    rounded(d,(X(-38),Y(45),X(36),Y(117)),f'n-body{x}-{y}',(66,100,115),rad=12,width=3)
    head=[(-75,-55),(-37,-88),(12,-87),(62,-55),(71,-3),(37,47),(-33,43),(-76,-1)]
    strokepoly(d,[(X(a),Y(b)) for a,b in head],f'nhead{x}-{y}',(209,153,112),width=5)
    # distinctive missing upper-left corner
    strokepoly(d,[(X(-75),Y(-55)),(X(-37),Y(-88)),(X(-21),Y(-59)),(X(-56),Y(-40))],f'notch{x}',(228,229,214),width=2)
    d.ellipse((X(41),Y(-104),X(63),Y(-80)),fill=(212,164,97),outline=INK,width=3)
    skline(d,[(X(52),Y(-78)),(X(52),Y(-65))],f'screw{x}',3)
    skline(d,[(X(-39),Y(48)),(X(48),Y(48)),(X(24),Y(70)),(X(-42),Y(64))],f'scarf{x}',10,GOLD,2)
    for a,b in [(-33,-18),(25,-18)]:d.ellipse((X(a-5),Y(b-6),X(a+4),Y(b+7)),fill=INK)
    if face=='scared':
        d.ellipse((X(-11),Y(11),X(5),Y(30)),fill=(95,55,63))
        for i in range(4):skline(d,[(X(-70+i*9),Y(-27)),(X(-82+i*9),Y(-38))],f'stress{i}{x}',2,(95,81,86))
        d.ellipse((X(66),Y(-29),X(74),Y(-17)),fill=(132,197,210))
    elif face=='smile':d.arc((X(-13),Y(9),X(13),Y(25)),0,160,fill=INK,width=3)
    else:skline(d,[(X(-10),Y(14)),(X(10),Y(14))],f'mouth{x}',2)
    # expressive arm act
    if pose=='startle':arms=[((-36,66),(-92,-10)),((38,66),(83,-12))]
    elif pose=='point':arms=[((-38,73),(-54,98)),((38,66),(90,22))]
    elif pose=='wave':arms=[((-38,73),(-54,98)),((38,66),(88,2))]
    else:arms=[((-38,67),(-62,99)),((38,66),(55,104))]
    for i,(a,b) in enumerate(arms):
        skline(d,[(X(a[0]),Y(a[1])),(X(b[0]),Y(b[1]))],f'arm{i}{x}',7,(98,115,128),2)
        d.ellipse((X(b[0]-8),Y(b[1]-9),X(b[0]+8),Y(b[1]+9)),fill=(214,170,102),outline=INK,width=2)

def moku(d,x,y,s=1,face='smile',pose='arms-up'):
    X=lambda v:x+v*s;Y=lambda v:y+v*s
    pts=[(-67,20),(-90,-25),(-68,-66),(-37,-72),(-25,-104),(11,-98),(30,-75),(71,-70),(89,-26),(74,12),(89,44),(53,73),(-35,69)]
    strokepoly(d,[(X(a),Y(b)) for a,b in pts],f'mok{int(x)}-{int(y)}',(187,164,205),width=5)
    for a,b in [(-32,-19),(30,-18)]:d.ellipse((X(a-4),Y(b-6),X(a+4),Y(b+6)),fill=INK)
    if face=='smile':d.arc((X(-20),Y(9),X(25),Y(31)),0,170,fill=INK,width=3)
    elif face=='surprise':d.ellipse((X(-7),Y(10),X(8),Y(31)),fill=INK)
    if pose=='arms-up':arms=[((-75,34),(-102,-68)),((77,34),(110,-71))]
    elif pose=='peek':arms=[((-75,34),(-94,56)),((77,34),(112,2))]
    else:arms=[((-75,34),(-100,46)),((77,34),(98,52))]
    for i,(a,b) in enumerate(arms):
        skline(d,[(X(a[0]),Y(a[1])),(X(b[0]),Y(b[1]))],f'm-arm{i}{int(x)}',7,(206,163,106),2)
        d.ellipse((X(b[0]-12),Y(b[1]-10),X(b[0]+13),Y(b[1]+12)),fill=(214,160,100),outline=INK,width=2)
    for i,xx in enumerate((-35,36)):
        skline(d,[(X(xx),Y(63)),(X(xx),Y(91))],f'mf{i}{x}',7,(77,86,103),1)

def bubble(d,rect,text,key,side='left',size=28):
    x,y,w,h=rect
    points=[(x+15,y),(x+w-16,y-3),(x+w,y+16),(x+w-3,y+h-10),(x+w-21,y+h+6),(x+35,y+h+6),(x+18,y+h+18),(x+10,y+h+1),(x,y+22)]
    strokepoly(d,points,key,(253,248,236),width=3)
    t=font(size)
    for i,line in enumerate(text.split('\n')):
        d.text((x+17,y+9+i*(size+5)),line,font=t,fill=INK)

def panel_outline(d,rect,key):
    x,y,x2,y2=rect
    skline(d,[(x,y),(x2,y),(x2,y2),(x,y2),(x,y)],key,4,INK,1.6)

def draw_page():
    im=Image.new('RGB',(W,H),PAPER);d=ImageDraw.Draw(im)
    rng=seed('paper-v1')
    # paper fiber texture (independent of colors)
    for i in range(46000):
        x=rng.randrange(W);y=rng.randrange(H);k=rng.randrange(10,30)
        c=(max(0,PAPER[0]-k),max(0,PAPER[1]-k),max(0,PAPER[2]-k))
        d.point((x,y),fill=c)
    d.text((40,25),'ネジまち',font=font(53),fill=INK)
    d.text((340,47),'第06話  おおきな影',font=font(35),fill=(92,99,103))
    # establishing shot x=30:1050, y=125:435
    r1=(30,130,1050,430);bg_scene(d,r1,'intro')
    # gigantic pipe engineering in overhead layer
    skline(d,[(80,155),(320,180),(530,180),(730,120)],'intro-arch',15,(104,120,126),1.6)
    # Monumental bolt-like wheel against the pipes gives the district its scale.
    cx,cy,rr=945,194,62
    for k in range(9):
        a=k*2*math.pi/9
        sx,sy=cx+rr*math.cos(a),cy+rr*math.sin(a)
        d.line((sx-8,sy-8,sx+8,sy+8),fill=(104,119,121),width=9)
    d.ellipse((cx-rr,cy-rr,cx+rr,cy+rr),outline=(74,88,92),width=7)
    d.ellipse((cx-29,cy-29,cx+29,cy+29),outline=(91,99,99),width=5)
    neji(d,185,340,.56,'curious','walk')
    # distant silhouette of Moku cast by lamp, huge but recognizably glove-shaped
    sh=[(705,280),(673,208),(700,171),(770,162),(810,214),(872,165),(928,220),(898,320)]
    strokepoly(d,sh,'shadow-far',(126,121,131),outline=(89,85,103),width=3)
    hatch(d,(685,183,217,114),'shadow-hatch',num=24,color=(105,101,113))
    bubble(d,(288,222,255,67),'「……なんか\n　いる？」','b1',size=25)
    panel_outline(d,r1,'border1')
    # middle two separate; Japanese comic right to left
    right=(540,450,1050,793)
    bg_scene(d,right,'look_up')
    # exaggerated low-angle unknown silhouette takes up almost everything
    points=[(751,749),(716,664),(742,595),(732,553),(776,508),(818,542),(869,509),(929,552),(944,660),(911,752)]
    strokepoly(d,points,'giant-shadow',(119,110,138),outline=(69,68,88),width=8)
    hatch(d,(725,510,205,230),'giant-hatch',num=48,color=(82,77,102))
    neji(d,635,738,.36,'scared','startle')
    bubble(d,(559,470,180,58),'「でっか…」','b2',size=24)
    panel_outline(d,right,'border2')
    left=(30,450,526,793)
    bg_scene(d,left,'reaction','reaction')
    neji(d,273,621,1.07,'scared','startle')
    # Nervous heartbeat marks
    for i in range(4):skline(d,[(385+i*16,535),(394+i*16,520)],'nervous'+str(i),3,(112,86,92),1.7)
    panel_outline(d,left,'border3')
    # final reveal = one large panel: who is casting the shadow?
    bottom=(30,817,1050,1289)
    bg_scene(d,bottom,'reveal')
    # Concrete lamp that explains the shadow. Small Moku stands in light.
    skline(d,[(827,817),(827,911)],'lampchain',12,(94,103,108),2)
    d.ellipse((755,885,900,942),fill=(238,210,122),outline=INK,width=5)
    for x in (715,760,810,870,918):skline(d,[(826,930),(x,1240)],'rays'+str(x),2,(183,171,129),1)
    # his shadow is a large silhouette cast on the wall to the left
    spts=[(288,1170),(242,1035),(266,973),(336,953),(353,910),(414,935),(454,992),(499,945),(544,1032),(531,1154)]
    strokepoly(d,spts,'bigshadow',(150,137,157),outline=(111,100,125),width=5)
    # small original Moku just under the lamp, arms lifted
    moku(d,833,1172,.65,'smile','arms-up')
    neji(d,622,1192,.7,'smile','point')
    bubble(d,(635,849,365,79),'「ぼく、ついに\n　大きくなれた」','b4',size=26)
    bubble(d,(85,974,282,62),'「……やるなあ」','b5',size=29)
    panel_outline(d,bottom,'border4')
    d.text((37,1310),'NEJIMACHI    PENCIL & INK STUDY / ORIGINAL CHARACTER ART',font=font(17),fill=(108,108,107))
    return im

if __name__=='__main__':
    im=draw_page();out=Path(__file__).resolve().parents[1]/'assets'/'nejimachi-006-big-shadow.png';out.parent.mkdir(parents=True,exist_ok=True);
    if out.exists():
        print('Preserved earlier hand-ink manga:',out.name)
    else:
        im.save(out,optimize=True)
    if out.stat().st_size>2_000_000:
        raise ValueError('Hand-ink PNG over 2MB')
    print(out,out.stat().st_size)