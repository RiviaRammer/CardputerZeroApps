"""Read known boot assets without elevating privileges."""

from io import BytesIO
from pathlib import Path
import shlex
import struct
import subprocess
import wave

from PIL import Image

from assets import ASSETS, LEGACY_LINUX_SPLASH
from build_splash import FIRMWARE_DISPLAY_ON, FIRMWARE_INIT, PANEL_HEIGHT, PANEL_WIDTH, ROW_BYTES

MAX_BYTES = 16 * 1024 * 1024


def firmware_image(data):
    view = memoryview(data)
    offset = len(FIRMWARE_INIT)
    if not data.startswith(FIRMWARE_INIT):
        raise ValueError("固件 BIN 的指令布局不受支持。")
    rows = []
    for y in range(PANEL_HEIGHT):
        header = struct.pack("<BBBH", 16, 0, bool(y), ROW_BYTES + (y == 0))
        if y == 0:
            header += b"\x2c"
        if view[offset:offset + len(header)] != header:
            raise ValueError("固件 BIN 的像素行结构不受支持。")
        offset += len(header)
        row = view[offset:offset + ROW_BYTES]
        if len(row) != ROW_BYTES:
            raise ValueError("固件 BIN 的像素行不完整。")
        rows.append(row)
        offset += ROW_BYTES
    if view[offset:] != FIRMWARE_DISPLAY_ON:
        raise ValueError("固件 BIN 的结尾不受支持。")
    rgb = bytearray()
    for row in rows:
        for (value,) in struct.iter_unpack(">H", row):
            rgb.extend(((value >> 11) * 255 // 31, ((value >> 5) & 63) * 255 // 63, (value & 31) * 255 // 31))
    return Image.frombytes("RGB", (PANEL_WIDTH, PANEL_HEIGHT), bytes(rgb)).transpose(Image.Transpose.ROTATE_270)


def configured_linux_splash():
    try:
        status = subprocess.run(
            ["/usr/bin/systemctl", "show", "--property=ExecStart", "--value", "fb_load.service"],
            capture_output=True, text=True, timeout=3, check=True,
        )
        _, marker, args = status.stdout.partition("argv[]=")
        if not marker:
            raise ValueError("缺少 ExecStart。")
        argv = shlex.split(args.partition(" ;")[0])
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError("无法确认 fb_load 当前配置。") from error

    if not argv or Path(argv[0]).name != "fb_load":
        raise ValueError("当前 fb_load 启动方式不受支持。")
    positional = [arg for arg in argv[1:] if arg not in ("--keep-open", "-k")]
    return Path(positional[0]) if positional else None


def current_linux():
    destination = ASSETS["linux"].destination
    path = configured_linux_splash()
    if path is None:
        return destination, "factory-reference"
    if path not in (destination, LEGACY_LINUX_SPLASH):
        raise ValueError("fb_load 使用了自定义路径。网页仅显示已知路径的文件。")
    return path, "current"


def read_asset(target):
    asset = ASSETS[target]
    if target == "linux":
        path, source = current_linux()
    else:
        path, source = asset.destination, "current"
    with path.open("rb") as file:
        data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("文件超过 16 MiB，无法预览。")

    if target == "sound":
        try:
            with wave.open(BytesIO(data), "rb") as wav:
                if not wav.getnframes():
                    raise ValueError("音频文件没有采样。")
        except (wave.Error, EOFError) as error:
            raise ValueError("无法读取 WAV。") from error
        return "audio/wav", data, source

    with BytesIO() as output:
        if target == "firmware":
            firmware_image(data).save(output, format="PNG")
        else:
            formats = ("BMP",) if target == "linux" else ("PNG",)
            with Image.open(BytesIO(data), formats=formats) as image:
                if image.width * image.height > 4_000_000:
                    raise ValueError("图片像素数超过预览限制。")
                if target == "linux":
                    image = image.transpose(Image.Transpose.TRANSPOSE)
                image.convert("RGB").save(output, format="PNG")
        return "image/png", output.getvalue(), source
