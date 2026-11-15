"""Local upload, preview and installation with confirmation on the device."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
import json
from math import gcd
from pathlib import Path
import re
import secrets
import subprocess
import time
from threading import Event, Thread
from typing import NamedTuple

from PIL import Image, ImageOps

from assets import ASSETS, AUDIO_RATE, AUDIO_SECONDS, valid_pcm, write_wav

from current import read_asset
from local_sudo import install

MAX_UPLOAD = 16 * 1024 * 1024
MAX_PIXELS = 16_000_000
PREVIEW_TTL = 10 * 60
APPROVAL_TTL = 45
STATUS_HISTORY = 8
IMAGE_FORMATS = ("PNG", "JPEG", "WEBP", "BMP")


class Preview(NamedTuple):
    key: str
    target: str
    deadline: float
    payload: bytes


class Approval:
    def __init__(self, previews):
        self.previews = previews
        self.answered = Event()
        self.allowed = False

    def resolve(self, allowed):
        self.allowed = allowed
        self.answered.set()


def prepare(asset, data):
    with BytesIO() as preview:
        if asset.size:
            try:
                with Image.open(BytesIO(data), formats=IMAGE_FORMATS) as image:
                    if image.width * image.height > MAX_PIXELS:
                        raise ValueError("图片像素数超过 1600 万。")
                    rgba = ImageOps.exif_transpose(image).convert("RGBA")
                    fitted = ImageOps.fit(rgba, asset.size, Image.Resampling.LANCZOS)
                    rgb = Image.new("RGB", asset.size)
                    rgb.paste(fitted, mask=fitted.getchannel("A"))
            except (OSError, SyntaxError, Image.DecompressionBombError) as error:
                raise ValueError("无法读取图片，请上传有效的 PNG、JPEG、WebP 或 BMP。") from error
            rgb.save(preview, format="PNG")
            return rgb.tobytes(), "image/png", preview.getvalue()

        try:
            result = subprocess.run(
                ["/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-protocol_whitelist", "pipe", "-format_whitelist", "wav,mp3,flac,ogg",
                 "-i", "pipe:0", "-map", "0:a:0", "-t", str(AUDIO_SECONDS + 1),
                 "-ar", str(AUDIO_RATE), "-ac", "2", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"],
                input=data, capture_output=True, timeout=20, check=True,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("无法转换音频，请上传有效的 WAV、MP3、FLAC 或 Ogg。") from error
        if not valid_pcm(result.stdout):
            raise ValueError("音频时长须在 0 到 30 秒之间。")
        write_wav(preview, result.stdout)
        return result.stdout, "audio/wav", preview.getvalue()


def aspect_ratio(size):
    width, height = size
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def render_page():
    targets = [
        {
            "id": key,
            "title": asset.title,
            "size": asset.size,
            "ratio": aspect_ratio(asset.size) if asset.size else None,
            "destination": str(asset.destination),
        }
        for key, asset in ASSETS.items()
    ]
    template = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    return template.replace("__ASSET_TARGETS__", json.dumps(targets, ensure_ascii=False)).encode()


class WriteJob:
    def __init__(self, server, previews):
        self.server = server
        self.previews = previews
        self.key = secrets.token_urlsafe(18)
        self.thread = Thread(target=self.run, name="asset-writer")
        self.message = "请在开发板上确认写入。"
        self.result = None

    def status(self):
        result = self.result
        if result is not None:
            ok, message = result
            return {"done": True, "ok": ok, "message": message}
        session = self.server.sudo_session if self.server.active_job is self else None
        return {"done": False, "message": session.status_message() if session else self.message}

    def run(self):
        server = self.server
        approval = Approval(self.previews)
        server.approval = approval
        deadline = time.monotonic() + APPROVAL_TTL
        try:
            while time.monotonic() < deadline and not server.closing.is_set():
                if approval.answered.wait(0.25):
                    break
        finally:
            server.approval = None
        if not approval.allowed or server.closing.is_set():
            self.result = (False, "开发板上未确认，写入已取消。")
            return

        self.message = "已在开发板上确认，正在请求系统授权…"
        try:
            result, cancelled = install(server, self.previews)
        except (OSError, subprocess.SubprocessError) as error:
            self.result = (False, f"无法确认写入结果。请查看当前文件。\n{error}")
            return
        if cancelled:
            self.result = (False, "已在开发板上取消授权。")
        elif result.returncode:
            error = result.stderr.decode(errors="replace").strip()
            self.result = (False, f"写入未全部完成。请检查账户权限或当前文件。\n{error}")
        else:
            titles = "、".join(ASSETS[preview.target].title for preview in self.previews)
            self.result = (True, f"已写入 {titles}\n重启开发板以检查效果。")


class UploadServer(HTTPServer):
    def __init__(self, address):
        self.approval = None
        self.closing = Event()
        self.previews = {}
        self.sudo_session = None
        self.jobs = {}
        self.active_job = None
        self.page = render_page()
        super().__init__(address, Handler)

    def start_write(self, previews):
        job = WriteJob(self, previews)
        if len(self.jobs) >= STATUS_HISTORY:
            self.jobs.pop(next(iter(self.jobs)))
        self.jobs[job.key] = self.active_job = job
        for preview in previews:
            self.previews.pop(preview.target)
        job.thread.start()
        return job.key


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, format, *args):
        pass

    def reply(self, status, data=b"", content_type="text/plain; charset=utf-8", **headers):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                         "img-src blob:; media-src blob:; connect-src 'self'; frame-ancestors 'none'")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def notice(self, status, message):
        self.reply(status, message.encode())

    def reply_json(self, status, data):
        self.reply(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path == "/":
            self.reply(200, self.server.page, "text/html; charset=utf-8")
            return
        endpoint, _, target = self.path.strip("/").partition("/")
        if endpoint == "write-status" and target in self.server.jobs:
            self.reply_json(200, self.server.jobs[target].status())
            return
        if endpoint != "current" or target not in ASSETS:
            self.notice(404, "页面不存在。")
            return
        try:
            mime, data, source = read_asset(target)
        except FileNotFoundError:
            self.notice(404, "暂时没有此文件。")
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            self.notice(422, "无法预览。" + str(error))
        else:
            self.reply(200, data, mime, **{"X-Asset-Source": source})

    def read_body(self, limit):
        length = self.headers.get("Content-Length", "")
        if not re.fullmatch(r"[0-9]{1,8}", length) or not 1 <= int(length) <= limit:
            self.notice(413, f"请求为空或大小超限（上限 {limit} 字节）。")
            return None
        remaining = int(length)
        deadline = time.monotonic() + 30
        data = bytearray()
        try:
            while remaining and time.monotonic() < deadline and not self.server.closing.is_set():
                block = self.rfile.read1(min(remaining, 65536))
                if not block:
                    break
                data.extend(block)
                remaining -= len(block)
        except TimeoutError:
            remaining = 1
        if remaining:
            self.notice(408, "上传中断或超时，请重试。")
            return None
        return data

    def do_POST(self):
        if self.path == "/apply":
            self.apply()
            return
        endpoint, _, target = self.path.strip("/").partition("/")
        if target not in ASSETS or endpoint != "preview":
            self.notice(404, "资源不存在。")
            return
        data = self.read_body(MAX_UPLOAD)
        if data is None:
            return
        try:
            payload, mime, preview = prepare(ASSETS[target], data)
        except ValueError as error:
            self.notice(422, str(error))
            return
        key = secrets.token_urlsafe(18)
        self.server.previews[target] = Preview(key, target, time.monotonic() + PREVIEW_TTL, payload)
        self.reply(200, preview, mime, **{"X-Preview-Id": key})

    def apply(self):
        if self.headers.get("Content-Type") != "application/json":
            self.notice(415, "请使用网页上的写入按钮。")
            return
        body = self.read_body(2048)
        if body is None:
            return
        try:
            requests = json.loads(body)
            if not isinstance(requests, dict) or not 1 <= len(requests) <= len(ASSETS):
                raise ValueError
        except (UnicodeError, ValueError):
            self.notice(400, "写入请求格式有误。")
            return
        previews = []
        now = time.monotonic()
        for target, key in requests.items():
            preview = self.server.previews.get(target)
            if not preview or key != preview.key or preview.deadline < now:
                self.notice(409, "上传预览已失效，请重新上传所选文件。")
                return
            previews.append(preview)

        active = self.server.active_job
        if active and active.thread.is_alive():
            self.notice(409, "已有写入正在进行，请等待完成。")
            return
        self.reply_json(202, {"id": self.server.start_write(previews)})
