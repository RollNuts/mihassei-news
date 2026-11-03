"""Hand-ink remasters of initial Nejimachi pages; append-only alternate art."""
from pathlib import Path
from PIL import Image, ImageDraw
from ink_storyboard import PAPER, INK, font, seed, skline, strokepoly, rounded, hatch, bg_scene, neji, bubble, panel_outline

def paper():
    im=Image.new('RGB',(1080,1350),PAPER);d=ImageDraw.Draw(im);r=seed('ink-remasters')
    for i in range(33000):
        x=r.randrange(1080);y=r.randrange(1350);k=r.randrange(8,27)
        d.point((x,y),fill=(PAPER[0]-k,PAPER[1]-k,PAPER[2]-k))
    return im,d

def label(d,n,title):
    d.text((35,25),'ネジまち',font=font(52),fill=INK)
    d.text((350,48),f'第{n:02d}話   {title}',font=font(34),fill=(94,97,100))
    d.text((35,1313),'NEJIMACHI / PENCIL-INK REMASTER • ORIGINAL ART',font=font(17),fill=(106,107,109))

def pito(d,x,y,s=1):
    X=lambda a:x+s*a;Y=lambda b:y+s*b
    strokepoly(d,[(X(-92),Y(-63)),(X(77),Y(-70)),(X(99),Y(-46)),(X(101),Y(54)),(X(82),Y(77)),(X(-91),Y(75)),(X(-109),Y(41)),(X(-107),Y(-37))],f'pito-{x}-{y}',(153,193,195),width=5)
    d.rounded_rectangle((X(-83),Y(-49),X(75),Y(46)),radius=12,fill=(226,235,224),outline=INK,width=3)
    d.ellipse((X(-22),Y(-31),X(25),Y(13)),fill=(72,103,111))
    d.ellipse((X(-12),Y(-23),X(2),Y(-9)),fill=(240,243,217))
    skline(d,[(X(43),Y(-9)),(X(58),Y(-9))],f'pitobrow-{x}',3,(72,103,111))
    skline(d,[(X(-42),Y(57)),(X(45),Y(57))],f'pitobottom-{x}',3,(97,138,150))
    d.ellipse((X(-40),Y(79),X(39),Y(97)),outline=(108,151,159),width=5)
    d.ellipse((X(-100),Y(-87),X(-82),Y(-70)),fill=(234,170,112),outline=INK,width=2)

def clock(d,x,y,r,time):
    d.ellipse((x-r,y-r,x+r,y+r),fill=(244,219,162),outline=INK,width=6)
    for i in range(12):
        a=i*3.14159/6
        import math
        sx,sy=x+math.sin(a)*r*.76,y-math.cos(a)*r*.76
        ex,ey=x+math.sin(a)*r*.85,y-math.cos(a)*r*.85
        skline(d,[(sx,sy),(ex,ey)],f'clk{i}{x}',2)
    d.text((x-r*.65,y-r*.32),time,font=font(int(r*.54)),fill=INK)

def ink_morning():
    im,d=paper();label(d,1,'朝の配給')
    p1=(30,127,1050,445);bg_scene(d,p1,'morning1')
    clock(d,851,235,108,'7:05')
    # Tiny protagonist runs toward a huge slot in the machine.
    d.rounded_rectangle((546,235,695,446),radius=14,fill=(100,125,134),outline=INK,width=6)
    skline(d,[(566,289),(661,289)],'slotline',11,(52,68,75))
    neji(d,270,345,.58,'curious','walk')
    bubble(d,(50,158,274,65),'「朝をひとつ」','morning-b1',size=28)
    panel_outline(d,p1,'m-border1')
    p2=(30,461,1050,802);bg_scene(d,p2,'morning2','reaction')
    pito(d,808,657,1.15)
    neji(d,268,657,.96,'scared','startle')
    d.rounded_rectangle((443,526,584,599),radius=10,fill=(81,93,104),outline=INK,width=4)
    d.text((466,539),'終了',font=font(35),fill=(245,225,177))
    bubble(d,(55,488,365,68),'「7時3分で終了です」','morning-b2',size=26)
    panel_outline(d,p2,'m-border2')
    p3=(30,819,1050,1290);bg_scene(d,p3,'morning3')
    # An absurdly early day already powers into motion; Neji is still sleepy.
    for j in range(3):
        xpos=610+j*106
        strokepoly(d,[(xpos,1103),(xpos+24,1087),(xpos+43,1106),(xpos+56,1177),(xpos,1182)],f'machine{j}',(140,146,147),width=3)
    neji(d,314,1110,.86,'scared','walk')
    pito(d,783,1114,.5)
    bubble(d,(62,864,302,74),'「じゃあ今日、\n　朝なし？」','morning-b3',size=27)
    bubble(d,(542,918,391,74),'「昼から始めてください」','morning-b4',size=25)
    panel_outline(d,p3,'m-border3')
    return im

def ink_blue():
    im,d=paper();label(d,2,'青空の品質')
    p1=(30,127,1050,665);bg_scene(d,p1,'blue1')
    # Industrial ceiling as a false, enormous sky, with blue lamp instead.
    d.ellipse((540,144,891,380),fill=(166,212,220),outline=INK,width=8)
    d.ellipse((583,196,847,350),fill=(147,198,210),outline=(73,109,116),width=6)
    for i in range(7):
        x=555+i*48
        skline(d,[(695,371),(x,634)],'blue-ray'+str(i),3,(125,171,192),1.8)
    neji(d,318,521,.78,'smile','point')
    bubble(d,(62,165,300,80),'「今日は\n　青空なんだ！」','blue-b1',size=28)
    panel_outline(d,p1,'blue-border1')
    p2=(30,683,1050,1290);bg_scene(d,p2,'blue2')
    pito(d,825,1070,1.22)
    neji(d,290,1140,.85,'curious','walk')
    d.rounded_rectangle((400,756,815,839),radius=9,fill=(75,91,99),outline=INK,width=5)
    d.text((423,764),'青色点灯　正常',font=font(36),fill=(240,221,174))
    skline(d,[(607,840),(607,1003)],'signpost',8,(89,100,111))
    bubble(d,(73,876,523,83),'「空は見えませんが、\n　青色の点灯は正常です」','blue-b2',size=25)
    panel_outline(d,p2,'blue-border2')
    return im

if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]/'assets'
    root.mkdir(parents=True,exist_ok=True)
    for num,fn in [(1,ink_morning),(2,ink_blue)]:
        name={
            1:'nejimachi-001-morning-ink-v1.png',
            2:'nejimachi-002-blue-sky-ink-v1.png'
        }[num]
        path=root/name
        if path.exists():
            print('Preserving prior remaster:',name)
            continue
        im=fn()
        im.save(path,optimize=True)
        if path.stat().st_size>2_000_000:
            raise ValueError('Remaster exceeds 2MB: '+name)
        print('Hand-ink remaster saved:',name,path.stat().st_size)