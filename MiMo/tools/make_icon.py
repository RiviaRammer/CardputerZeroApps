"""Reproduce the geometric launcher icon (Pillow is a build-time tool only)."""
from pathlib import Path
import sys

# Workspace-local Pillow for Windows; Linux can use python3-pil.
local = Path(__file__).resolve().parents[3] / ".cad_tools"
if local.exists():
    sys.path.insert(0, str(local))
from PIL import Image, ImageDraw

image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((6, 6, 250, 250), radius=54, fill="#101820")
draw.rounded_rectangle((93, 42, 163, 152), radius=35, fill="#6EF1B4")
draw.arc((66, 76, 190, 181), 0, 180, fill="#F4FAFF", width=12)
draw.line((128, 178, 128, 209), fill="#F4FAFF", width=12)
draw.rounded_rectangle((96, 204, 160, 216), radius=6, fill="#F4FAFF")
for x, y in ((42, 105), (210, 105)):
    draw.rounded_rectangle((x - 5, y, x + 5, y + 36), radius=5, fill="#6EF1B4")
image.resize((128, 128), Image.Resampling.LANCZOS).save(Path(__file__).resolve().parents[1] / "mimo.png")
