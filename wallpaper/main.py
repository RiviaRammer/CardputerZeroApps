#!/usr/bin/python3
"""Show the web address and confirm writes on the CardputerZero LCD."""

import argparse
from contextlib import ExitStack
import fcntl
import json
import mmap
import os
import pwd
import signal
import struct
import subprocess
import threading
import time

from PIL import Image, ImageDraw, ImageFont

from assets import ASSETS
from keyboard import CONTROL, ENTER, Keyboard, ecodes
from local_sudo import PASSWORD_LIMIT
from web import UploadServer

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
LCD_SIZE = (320, 170)
FB_FIXED = struct.Struct("@16sL4I3HI")
PIXEL_FORMATS = {
    (16, (11, 5, 0, 5, 6, 0, 0, 5, 0)): (2, "RGB565"),
    (32, (16, 8, 0, 8, 8, 0, 0, 8, 0)): (4, "BGRX"),
}
stopping = False


def stop(signum, frame):
    global stopping
    stopping = True


def framebuffer_device():
    return (os.environ.get("LV_LINUX_FBDEV_DEVICE")
            or os.environ.get("APPLAUNCH_LINUX_FBDEV_DEVICE")
            or ("/dev/fb_lcd" if os.path.exists("/dev/fb_lcd") else "/dev/fb0"))


class Screen:
    def __init__(self, stack):
        fd = stack.enter_context(open(framebuffer_device(), "r+b", buffering=0)).fileno()
        variable = bytearray(160)
        fixed = bytearray(128)
        fcntl.ioctl(fd, 0x4600, variable)
        fcntl.ioctl(fd, 0x4602, fixed)
        width, height, _, _, xoff, yoff, bpp = struct.unpack_from("7I", variable)
        channels = struct.unpack_from("9I", variable, 8 * 4)
        if (width, height) not in (LCD_SIZE, LCD_SIZE[::-1]) or (bpp, channels) not in PIXEL_FORMATS:
            raise OSError(f"Unsupported LCD framebuffer: {width}×{height}, {bpp} bpp")
        fields = FB_FIXED.unpack_from(fixed)
        self.length = fields[2]
        self.stride = fields[-1]
        self.width = width
        self.height = height
        pixel_bytes, self.format = PIXEL_FORMATS[bpp, channels]
        self.row_bytes = width * pixel_bytes
        self.offset = yoff * self.stride + xoff * pixel_bytes
        end = self.offset + (height - 1) * self.stride + self.row_bytes
        if (width + xoff) * pixel_bytes > self.stride or end > self.length:
            raise OSError("Invalid LCD framebuffer stride or offset")
        self.memory = stack.enter_context(mmap.mmap(fd, self.length))
        self.fonts = {size: ImageFont.truetype(FONT, size) for size in (12, 17, 20)}

    def show(self, address, port, approval, session, password_length):
        image = Image.new("RGB", LCD_SIZE, "#10201d")
        draw = ImageDraw.Draw(image)

        def label(y, text, size=17, color="white"):
            draw.text((160, y), text, anchor="mm", font=self.fonts[size], fill=color)

        if session and session.prompt:
            label(27, "System sudo password", 20, "#fff1d9")
            label(58, pwd.getpwuid(os.getuid()).pw_name[:26])
            label(92, "•" * min(password_length, 20) or "Type on the keyboard", 17, "#a9f4d0")
            if session.prompt_count > 1:
                label(122, "Try again", 12, "#fff1d9")
            label(150, "Enter: authorize    Esc: cancel", 12, "#a9f4d0")
        elif session:
            label(66, "Applying wallpaper", 20, "#fff1d9")
            label(104, "Keep the device powered on", 12, "#a9f4d0")
        elif approval:
            label(20, "Confirm overwrite", 20, "#fff1d9")
            titles = [ASSETS[preview.target].lcd_title for preview in approval.previews]
            if len(titles) == 1:
                preview = approval.previews[0]
                asset = ASSETS[preview.target]
                label(46, titles[0])
                if asset.size:
                    thumbnail = Image.frombytes("RGB", asset.size, preview.payload)
                    thumbnail.thumbnail((204, 74), Image.Resampling.LANCZOS)
                    image.paste(thumbnail, ((320 - thumbnail.width) // 2, 67))
            else:
                for index, title in enumerate(titles):
                    label(49 + index * 18, title, 12)
            label(154, "Enter: continue    Esc: cancel", 12, "#a9f4d0")
        else:
            label(29, "Wallpaper", 20, "#fff1d9")
            url = f"http://{address}:{port}" if address else "Connect to Wi-Fi or Ethernet"
            label(79, url)
            label(111, "Confirm each write on this device", 12, "#a9f4d0")
            label(150, "Esc to exit", 12, "#a5c8bc")
        if self.width != LCD_SIZE[0]:
            image = image.transpose(Image.Transpose.ROTATE_90)
        if self.format == "RGB565":
            pixels = (
                ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                for r, g, b in image.getdata()
            )
            encoded = struct.pack(f"<{self.width * self.height}H", *pixels)
        else:
            encoded = image.tobytes("raw", "BGRX")
        for y in range(self.height):
            first = y * self.row_bytes
            offset = self.offset + y * self.stride
            self.memory[offset:offset + self.row_bytes] = encoded[first:first + self.row_bytes]


def find_addresses():
    try:
        result = subprocess.run(
            ["/usr/sbin/ip", "-j", "-4", "address", "show", "up", "scope", "global"],
            capture_output=True, check=True, timeout=2,
        )
        interfaces = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    candidates = [
        (not device["ifname"].startswith(("wl", "en", "eth")), address["local"])
        for device in interfaces
        for address in device.get("addr_info", [])
    ]
    return [address for _, address in sorted(candidates)]


def clear_password(password):
    password[:] = b"\x00" * len(password)
    password.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("Run the web app as the APPLaunch user, without sudo")
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535")
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    with ExitStack() as stack:
        screen = Screen(stack)
        keyboard = Keyboard(stack)
        try:
            server = stack.enter_context(UploadServer(("0.0.0.0", args.port)))
        except OSError:
            if args.port != 8765:
                raise
            server = stack.enter_context(UploadServer(("0.0.0.0", 0)))
        worker = threading.Thread(target=server.serve_forever, name="upload-server")
        worker.start()
        password = bytearray()
        try:
            shown = None
            address = ""
            next_scan = 0
            last_prompt = None
            while not stopping:
                if time.monotonic() >= next_scan:
                    address = next(iter(find_addresses()), "")
                    next_scan = time.monotonic() + 2
                approval = server.approval
                session = server.sudo_session
                if session:
                    session.poll()
                prompt = session.prompt if session else None
                if last_prompt is not prompt:
                    clear_password(password)
                    last_prompt = prompt
                display = (address, approval, session, prompt, len(password))
                if shown != display:
                    screen.show(address, server.server_port, approval, session, len(password))
                    shown = display
                for key in keyboard.events():
                    if session and session is server.sudo_session and prompt and session.prompt is prompt:
                        if key == ecodes.KEY_ESC:
                            session.cancel()
                            clear_password(password)
                        elif key in ENTER:
                            session.answer(password)
                            clear_password(password)
                        elif key == ecodes.KEY_BACKSPACE and password:
                            password.pop()
                        elif key == ecodes.KEY_U and keyboard.held & CONTROL:
                            clear_password(password)
                        elif len(password) < PASSWORD_LIMIT:
                            password.extend(keyboard.character(key).encode("ascii"))
                    elif approval and approval is server.approval and not approval.answered.is_set():
                        if key in ENTER or key == ecodes.KEY_ESC:
                            approval.resolve(key in ENTER)
                    elif not session and not approval and key == ecodes.KEY_ESC:
                        stop(None, None)
        finally:
            clear_password(password)
            server.closing.set()
            session = server.sudo_session
            if session and (session.prompt or not session.prompt_count):
                session.cancel()
            server.shutdown()
            worker.join()
            if server.active_job:
                server.active_job.thread.join()


if __name__ == "__main__":
    raise SystemExit(main())
