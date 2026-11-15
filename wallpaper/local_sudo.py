"""Keep sudo authentication on the board's keyboard and LCD."""

import os
import socket
import struct
import subprocess
import tempfile

from assets import WRITER, WRITER_READY

ASKPASS = "/usr/lib/wallpaper/askpass"
SOCKET_ENV = "WALLPAPER_ASKPASS_SOCKET"
PASSWORD_LIMIT = 255


class LocalSudo:
    def __init__(self, previews):
        self.previews = previews
        self.prompt_count = 0
        self.prompt = None
        self.cancelled = False
        self.submitted = False
        self.progress = None

    def run(self, socket_path):
        bundle = bytearray()
        for preview in self.previews:
            bundle.extend(f"{preview.target} {len(preview.payload)}\n".encode("ascii"))
            bundle.extend(preview.payload)
        env = dict(os.environ, SUDO_ASKPASS=ASKPASS)
        env[SOCKET_ENV] = socket_path
        with tempfile.TemporaryFile() as progress:
            self.progress = progress
            try:
                return subprocess.run(
                    ["/usr/bin/sudo", "-A", "-k", "--", WRITER, "batch"],
                    input=bundle, stdout=progress, stderr=subprocess.PIPE, env=env, timeout=150,
                )
            finally:
                self.progress = None

    def status_message(self):
        progress = self.progress
        if progress is not None:
            try:
                if os.pread(progress.fileno(), len(WRITER_READY), 0) == WRITER_READY:
                    return "授权通过，正在写入设备文件…"
            except (OSError, ValueError):
                pass
        if self.cancelled:
            return "正在取消授权…"
        if self.prompt is not None:
            if self.prompt_count > 1:
                return "系统要求再次验证，请在板上重新输入密码。"
            return "已在板上确认，请在板上输入系统密码。"
        if self.submitted:
            return "已提交密码，正在验证…"
        return "已在板上确认，正在请求系统授权…"

    def poll(self):
        if self.prompt is not None:
            return
        try:
            connection, _ = self.listener.accept()
        except (BlockingIOError, OSError):
            return
        try:
            credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _, uid, _ = struct.unpack("3i", credentials)
            if uid != os.getuid() or self.cancelled:
                connection.close()
                return
        except OSError:
            connection.close()
            return
        connection.settimeout(1)
        self.prompt_count += 1
        self.prompt = connection

    def answer(self, password):
        connection = self.prompt
        self.prompt = None
        if connection is None:
            return
        with connection:
            if password is not None:
                try:
                    connection.sendall(password)
                    connection.sendall(b"\n")
                    self.submitted = True
                except OSError:
                    pass

    def cancel(self):
        self.cancelled = True
        self.answer(None)
        self.listener.close()


def install(server, previews):
    with tempfile.TemporaryDirectory(prefix="cp0-sudo-") as directory:
        socket_path = directory + "/prompt"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(socket_path)
            listener.listen(1)
            listener.setblocking(False)
            session = LocalSudo(previews)
            session.listener = listener
            server.sudo_session = session
            try:
                if server.closing.is_set():
                    return None, True
                result = session.run(socket_path)
                return result, session.cancelled
            finally:
                server.sudo_session = None
                session.answer(None)
