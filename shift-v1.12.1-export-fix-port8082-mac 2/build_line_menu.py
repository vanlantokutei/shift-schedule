"""Render the approved cream/forest rich menu using native vector-style UI shapes."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
BASE=Path(__file__).parent
im=Image.new('RGB',(2500,843),'#FAF8F2');d=ImageDraw.Draw(im)
green='#075B3B';gold='#C7B68D'
d.rounded_rectangle((5,5,2494,837),radius=32,outline=gold,width=3)
for x in (833,1667):d.line((x,6,x,837),fill=gold,width=3)
font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',87)
for cx,label in [(416,'Đăng ký kibo'),(1250,'Xem lịch'),(2083,'Tencho')]:
    d.text((cx,643),label,font=font,fill=green,anchor='mm')
for cx in (416,1250):
    d.rounded_rectangle((cx-133,200,cx+133,461),radius=29,outline=green,width=14)
    d.line((cx-126,277,cx+126,277),fill=green,width=13)
    for x in (cx-69,cx+69):d.line((x,167,x,231),fill=green,width=15)
d.line((416,326,416,414),fill=green,width=14);d.line((372,370,460,370),fill=green,width=14)
d.line([(1197,369),(1237,407),(1311,329)],fill=green,width=15,joint='curve')
cx=2083
d.arc((cx-139,168,cx+139,446),180,360,fill=green,width=15)
for a,b in [(cx-152,cx-99),(cx+99,cx+152)]:
    d.rounded_rectangle((a,285,b,417),radius=20,outline=green,width=14)
d.arc((cx-9,340,cx+127,476),0,90,fill=green,width=14)
d.rounded_rectangle((cx-33,454,cx+36,484),radius=14,outline=green,width=11)
im.save(BASE/'static/line/betoya-rich-menu-v2.png',optimize=True)
