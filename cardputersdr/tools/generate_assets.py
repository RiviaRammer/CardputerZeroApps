from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from cardputersdr.lcd import View
from cardputersdr.radio import Radio
import time
root=Path(__file__).resolve().parents[1]
(root/'packaging').mkdir(exist_ok=True)
(root/'screenshots').mkdir(exist_ok=True)
icon=Image.new('RGB',(100,100),'#09131e');d=ImageDraw.Draw(icon)
d.rounded_rectangle((4,4,95,95),radius=20,outline='#294052',width=2)
for y in (30,50,70):d.line((14,y,85,y),fill='#203547')
d.line([(14,67),(26,64),(32,44),(37,61),(45,57),(52,21),(58,65),(67,59),(73,39),(79,64),(86,63)],fill='#53e0c4',width=3)
icon.save(root/'packaging'/'icon.png')
r=Radio(demo=True);r.open()
try:
    view=View()
    for _ in range(35):
        time.sleep(.075);frame=view.render(r.status(),r.frame())
    frame.save(root/'screenshots'/'lcd-demo.png')
    r.mode = 'web'
    view.render(r.status(), address='192.0.2.1:1420').save(root/'screenshots'/'web-mode-demo.png')
finally:r.close()
