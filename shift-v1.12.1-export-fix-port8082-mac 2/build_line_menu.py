"""Render the three exact rich-menu hit areas from the approved UI design."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
BASE=Path(__file__).parent
im=Image.new('RGB',(2500,843),'#FCFBF7');d=ImageDraw.Draw(im)
green='#075B3B';light='#EBF5EC'
# Same three ivory tiles, green outlines and line icons as the approved preview.
d.rounded_rectangle((8,8,2491,834),radius=64,outline=green,width=5)
for x in (833,1667):d.line((x,10,x,833),fill=green,width=4)
font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',75)
small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',37)
for cx,label,sub in [(416,'Đăng ký kibo','希望シフト'),(1250,'Xem lịch','Lịch đã chốt'),(2083,'Liên hệ Tencho','Hỗ trợ · Đổi ca')]:
    d.text((cx,610),label,font=font,fill=green,anchor='mm')
    d.text((cx,707),sub if cx!=416 else 'Chọn tên · Gửi lịch rảnh',font=small,fill=green,anchor='mm')
for cx in (416,1250):
    d.rounded_rectangle((cx-120,210,cx+120,446),radius=25,outline=green,width=13)
    d.line((cx-115,278,cx+115,278),fill=green,width=12)
    for x in (cx-63,cx+63):d.line((x,179,x,236),fill=green,width=14)
for x in (1195,1250,1305):
    for y in (331,386): d.rounded_rectangle((x-14,y-14,x+14,y+14),radius=3,fill=green)
d.ellipse((450,377,554,481),fill='#FCFBF7',outline=green,width=10)
d.line((502,399,502,459),fill=green,width=9);d.line((472,429,532,429),fill=green,width=9)
# Headset icon matching the chosen preview.
cx=2083
d.arc((cx-119,189,cx+119,427),180,360,fill=green,width=14)
d.rounded_rectangle((cx-137,294,cx-83,407),radius=22,fill=green)
d.rounded_rectangle((cx+83,294,cx+137,407),radius=22,fill=green)
d.arc((cx-18,340,cx+109,462),0,90,fill=green,width=13)
d.rounded_rectangle((cx-18,438,cx+42,465),radius=12,fill=green)
im.save(BASE/'static/line/betoya-rich-menu.png',optimize=True)
Image.open('/workspace/scratch/d73faa99e9a8/betoya-reference/pho-betoya-official.webp').convert('RGB').save(BASE/'static/line/pho-betoya.jpg',quality=94)
