"""Convert the actual LVGL test framebuffer renders to a review contact sheet."""
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
local = root.parents[1] / ".cad_tools"
if local.exists():
    sys.path.insert(0, str(local))
from PIL import Image, ImageDraw

canvas = Image.new("RGB", (680, 410), "#e7edf2")
draw = ImageDraw.Draw(canvas)
for i, name in enumerate(("home", "chat", "settings", "edit")):
    image = Image.open(root / "build" / "host" / (name + ".ppm"))
    x, y = 10 + (i % 2) * 340, 25 + (i // 2) * 200
    draw.text((x, y - 18), name, fill="black")
    canvas.paste(image, (x, y))
canvas.save(root / "build" / "ui-preview.png")
