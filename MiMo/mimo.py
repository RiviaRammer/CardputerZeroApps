#!/usr/bin/env python3
"""MiMo's audio/network worker. LCD IPC: tab-separated fields, UTF-8 hex values."""
import base64
import binascii
import io
import json
import os
from pathlib import Path
import queue
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from urllib.parse import urlsplit

import requests

DEFAULTS = {
    "api_key": "",
    "base_url": "https://api.xiaomimimo.com/v1",
    "chat_model": "mimo-v2.6-flash",
    "asr_model": "mimo-v2.5-asr",
    "tts_model": "mimo-v2.5-tts",
    "voice": "mimo_default",
    "capture_device": "default",
    "playback_device": "default",
    "max_record_seconds": 30,
    "max_completion_tokens": 512,
    "history_turns": 4,
    "system_prompt": "你是 MiMo 语音助手。请用简洁自然的中文回答，通常不超过三句话，不使用 Markdown。",
}
LIMITS = {"max_record_seconds": (1, 60), "max_completion_tokens": (64, 2048),
          "history_turns": (0, 10)}
VOICES = ("mimo_default", "冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean")
MAX_SSE_EVENT_BYTES = 2 * 1024 * 1024


class MimoError(Exception):
    pass


class Cancelled(Exception):
    pass


def validate(config):
    if not isinstance(config, dict):
        raise MimoError("config.json 必须是 JSON 对象")
    result = dict(DEFAULTS)
    result.update(config)  # Preserve unknown fields on LCD edits.
    result.pop("voice_reference", None)  # Discard the retired setting when loading or saving.
    for key, default in DEFAULTS.items():
        value = result[key]
        if type(value) is not type(default):
            raise MimoError(f"配置 {key} 类型错误")
        if key in LIMITS:
            low, high = LIMITS[key]
            if not low <= value <= high:
                raise MimoError(f"{key} 范围: {low}..{high}")
        elif len(value) > (2048 if key == "system_prompt" else 512) or any(ord(c) < 32 for c in value):
            raise MimoError(f"配置 {key} 过长或包含控制字符")
        elif not value and key not in ("api_key", "system_prompt"):
            raise MimoError(f"配置 {key} 不能为空")
    url = urlsplit(result["base_url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise MimoError("base_url 必须是无账号/查询参数的 HTTPS 地址")
    if result["tts_model"] != "mimo-v2.5-tts":
        raise MimoError("流式预置音色模式需使用 mimo-v2.5-tts")
    if result["voice"] not in VOICES:
        raise MimoError("未知音色，请使用 mimo_default / Mia / Chloe / Milo / Dean / 冰糖 / 茉莉 / 苏打 / 白桦")
    return result


class ConfigStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path.home() / "mimo_talk"
        self.legacy_path = Path.home() / "MiMo" / "config.json" if root is None else None
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / "config.json"

    def load(self):
        if self.path.exists():
            return self.read(self.path)
        config = dict(DEFAULTS)
        if self.legacy_path is not None and self.legacy_path.exists():
            try:
                config = self.read(self.legacy_path)
            except (OSError, MimoError) as exc:
                raise MimoError("无法迁移 ~/MiMo/config.json，请修复旧配置或在 ~/mimo_talk 新建配置") from exc
        self.save(config)
        return config

    @staticmethod
    def read(path):
        try:
            if path.stat().st_size > 32768:
                raise MimoError("config.json 超过 32 KiB")
            return validate(json.loads(path.read_text(encoding="utf-8-sig")))
        except (ValueError, UnicodeError) as exc:
            raise MimoError("config.json 无效，请修正 JSON；原文件已保留") from exc

    def save(self, config):
        config = validate(config)
        fd, name = tempfile.mkstemp(prefix=".config-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(config, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def sse_events(lines):
    """Parse SSE events, including comments, CRLF and multi-line data fields."""
    parts = []
    size = 0
    for raw in lines:
        if len(raw) > MAX_SSE_EVENT_BYTES:
            raise MimoError("SSE 数据块过大")
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if parts:
                yield "\n".join(parts)
            parts, size = [], 0
        elif line.startswith("data:"):
            data = line[5:]
            if data.startswith(" "):
                data = data[1:]
            size += len(data)
            if size > MAX_SSE_EVENT_BYTES:
                raise MimoError("SSE 数据块过大")
            parts.append(data)
    # A missing final blank line is allowed; completion is checked by the caller.
    if parts:
        yield "\n".join(parts)


def retry_delay(value, attempt):
    try:
        return max(1, min(60, float(value)))
    except (TypeError, ValueError):
        return 2 ** (attempt + 1)


class API:
    def __init__(self, config, cancel, status):
        self.config, self.cancel, self.status = config, cancel, status

    def stream(self, body):
        key = self.config["api_key"].strip()
        if not key:
            raise MimoError("请按 S 设置 API Key")
        url = self.config["base_url"].rstrip("/") + "/chat/completions"
        headers = {"api-key": key, "Content-Type": "application/json", "Accept": "text/event-stream"}
        body = dict(body, stream=True)
        # Retry only before receiving a successful stream; never replay spoken audio.
        for attempt in range(3):
            if self.cancel.is_set():
                raise Cancelled()
            try:
                with requests.post(url, headers=headers, json=body, stream=True,
                                   timeout=(8, 12), allow_redirects=False) as response:
                    if response.status_code in (429, 502, 503, 504) and attempt < 2:
                        delay = retry_delay(response.headers.get("Retry-After"), attempt)
                        self.status(f"HTTP {response.status_code}，{delay:g} 秒后重试")
                        if self.cancel.wait(delay):
                            raise Cancelled()
                        continue
                    if response.status_code != 200:
                        hints = {401: "API Key 无效", 403: "账号无权限", 402: "检查余额",
                                 429: "请求限流，请稍后再试", 400: "检查模型和配置"}
                        raise MimoError(f"HTTP {response.status_code}: " + hints.get(response.status_code, "服务请求失败"))
                    if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
                        raise MimoError("服务没有返回 SSE 流")
                    finished = False
                    deadline = time.monotonic() + 180
                    def lines():
                        for line in response.iter_lines(chunk_size=256):
                            if self.cancel.is_set():
                                raise Cancelled()
                            if time.monotonic() > deadline:
                                raise MimoError("请求超过 180 秒，请重试")
                            yield line

                    for payload in sse_events(lines()):
                        if self.cancel.is_set():
                            raise Cancelled()
                        if payload == "[DONE]":
                            return
                        try:
                            event = json.loads(payload)
                            if not isinstance(event, dict) or "error" in event:
                                raise MimoError("API 返回流内错误")
                            choices = event.get("choices", [])
                            if not choices:
                                continue
                            choice = choices[0]
                            reason = choice.get("finish_reason")
                            if reason and reason not in ("stop", "length"):
                                raise MimoError("回复被服务终止: " + str(reason))
                            finished |= reason is not None
                            yield choice.get("delta") or {}
                        except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
                            raise MimoError("无法解析 SSE 响应") from exc
                    if not finished:
                        raise MimoError("网络流中断，请重试")
                    return
            except requests.RequestException as exc:
                if self.cancel.is_set():
                    raise Cancelled() from exc
                # Do not include request headers, URLs or the API key in diagnostics.
                raise MimoError("网络连接失败或超时，请检查 Wi-Fi 和系统时间") from exc


def wav_bytes(pcm):
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    return out.getvalue()


def split_sentences(buffer, final=False):
    sentences = []
    while buffer:
        end = next((i + 1 for i, char in enumerate(buffer)
                    if char in "。！？!?\n" or (char == "." and i + 1 < len(buffer) and buffer[i+1].isspace())), 0)
        if (not end or end > 160) and len(buffer) >= 160:
            end = 160
        if not end:
            if final:
                end = len(buffer)
            else:
                break
        text, buffer = buffer[:end].strip(), buffer[end:]
        if text:
            sentences.append(text)
    return sentences, buffer


def audio_route(device, env, uid):
    """Use the current user's sound server when the app uses the default PCM."""
    if device not in ("default", "pulse"):
        return device
    if not env.get("PULSE_SERVER"):
        runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
        native = Path(runtime) / "pulse" / "native"
        if native.is_socket():
            env["PULSE_SERVER"] = f"unix:{native}"
    return "pulse" if env.get("PULSE_SERVER") else device


def audio_error(errors, device, component):
    # The audio child uses the C locale so diagnostic matching is independent of UI language.
    errors.seek(0)
    detail = errors.read(4096).decode("utf-8", errors="replace")
    if detail.strip():
        print(detail.rstrip(), file=sys.stderr)
    detail = detail.lower()
    if "permission denied" in detail or "access denied" in detail or "operation not permitted" in detail:
        return MimoError(f"{component}访问被拒绝 ({device})")
    if "device or resource busy" in detail:
        return MimoError(f"{component}已被占用 ({device})")
    if device == "pulse" and ("connect" in detail or "pulse" in detail):
        return MimoError(f"{component}无法连接用户音频服务")
    return MimoError(f"{component}无法打开或已断开 ({device})")


class Audio:
    def __init__(self, config, cancel):
        self.config, self.cancel = config, cancel
        self.lock = threading.Lock()
        self.processes = set()

    def start(self, args, **kwargs):
        with self.lock:
            if self.cancel.is_set():
                raise Cancelled()
            try:
                process = subprocess.Popen(args, **kwargs)
            except FileNotFoundError as exc:
                raise MimoError("缺少 ALSA 工具，请安装 alsa-utils") from exc
            self.processes.add(process)
            return process

    def stop(self):
        with self.lock:
            for process in self.processes:
                if process.poll() is None:
                    process.terminate()

    def cleanup(self, process):
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()
        with self.lock:
            self.processes.discard(process)

    def record(self, stop, status):
        env = dict(os.environ, LC_ALL="C")
        device = audio_route(self.config["capture_device"], env, os.getuid())
        with tempfile.TemporaryFile() as errors:
            process = self.start(["arecord", "-q", "-D", device,
                                  "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"],
                                 stdout=subprocess.PIPE, stderr=errors, stdin=subprocess.DEVNULL, env=env)
            pcm = bytearray()
            deadline = time.monotonic() + self.config["max_record_seconds"]
            limit = self.config["max_record_seconds"] * 32000
            last_second = -1
            try:
                while not stop.is_set() and time.monotonic() < deadline and len(pcm) < limit:
                    if self.cancel.is_set():
                        raise Cancelled()
                    ready, _, _ = select.select([process.stdout], [], [], 0.1)
                    if ready:
                        data = os.read(process.stdout.fileno(), min(4096, limit - len(pcm)))
                        if not data:
                            if self.cancel.is_set():
                                raise Cancelled()
                            raise audio_error(errors, device, "麦克风")
                        pcm.extend(data)
                    second = len(pcm) // 32000
                    if second != last_second:
                        status(f"录音 {second}s / {self.config['max_record_seconds']}s | ↵ 发送")
                        last_second = second
            finally:
                self.cleanup(process)
            if self.cancel.is_set():
                raise Cancelled()
            if not pcm and not stop.is_set():
                raise MimoError(f"麦克风未返回音频 ({device})")
            if len(pcm) < 6400:
                raise MimoError("录音过短，请至少说话 0.2 秒")
            return wav_bytes(pcm[:len(pcm) // 2 * 2])

    def speak(self, chunks):
        env = dict(os.environ, LC_ALL="C")
        device = audio_route(self.config["playback_device"], env, os.getuid())
        with tempfile.TemporaryFile() as errors:
            process = self.start(["aplay", "-q", "-D", device,
                                  "-t", "raw", "-f", "S16_LE", "-r", "24000", "-c", "1"],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors, env=env)
            os.set_blocking(process.stdin.fileno(), False)
            pending = b""
            total = 0
            try:
                for chunk in chunks:
                    pending += chunk
                    data, pending = pending[:len(pending) // 2 * 2], pending[len(pending) // 2 * 2:]
                    view = memoryview(data)
                    stalled = time.monotonic()
                    while view:
                        if self.cancel.is_set():
                            raise Cancelled()
                        if process.poll() is not None:
                            raise audio_error(errors, device, "扬声器")
                        _, ready, _ = select.select([], [process.stdin], [], 0.1)
                        if ready:
                            try:
                                count = os.write(process.stdin.fileno(), view)
                            except BlockingIOError:
                                continue
                            view = view[count:]
                            total += count
                            stalled = time.monotonic()
                        elif time.monotonic() - stalled > 10:
                            raise MimoError("音频播放超时")
                if pending or not total:
                    raise MimoError("TTS 音频为空或 PCM 数据不完整")
                process.stdin.close()
                process.stdin = None
                deadline = time.monotonic() + 15
                while process.poll() is None:
                    if self.cancel.wait(0.05):
                        raise Cancelled()
                    if time.monotonic() > deadline:
                        raise MimoError("扬声器排空超时")
                if process.returncode:
                    if self.cancel.is_set():
                        raise Cancelled()
                    raise audio_error(errors, device, "扬声器")
            except BrokenPipeError as exc:
                if self.cancel.is_set():
                    raise Cancelled() from exc
                raise audio_error(errors, device, "扬声器") from exc
            finally:
                self.cleanup(process)


def audio_chunks(api, text):
    body = {"model": api.config["tts_model"],
            "messages": [{"role": "user", "content": ""}, {"role": "assistant", "content": text}],
            "audio": {"format": "pcm16", "voice": api.config["voice"]}}
    for delta in api.stream(body):
        audio = delta.get("audio") or {}
        if audio.get("data"):
            try:
                yield base64.b64decode(audio["data"], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise MimoError("TTS Base64 音频无效") from exc


class Controller:
    def __init__(self, store, emit):
        self.store, self.emit = store, emit
        self.worker = None
        self.cancel = threading.Event()
        self.record_stop = threading.Event()
        self.audio = None
        self.history = []

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def status(self, text):
        self.emit("status", text)

    def config(self):
        config = self.store.load()
        for key in DEFAULTS:
            self.emit("config", key, str(config[key]))
        return config

    def command(self, line):
        parts = line.rstrip("\n").split("\t")
        command = parts[0]
        if command == "cancel":
            if self.busy():
                self.cancel.set()
                if self.audio:
                    self.audio.stop()
                self.status("正在取消...")
            return
        if command == "stop":
            self.record_stop.set()
            return
        if self.busy():
            return
        if command == "get":
            self.config()
            self.status("就绪 | ↵ 开始录音")
        elif command == "set" and len(parts) == 3 and parts[1] in DEFAULTS:
            key = parts[1]
            value = bytes.fromhex(parts[2]).decode("utf-8")
            config = self.store.load()  # Never overwrite an invalid file.
            config[key] = int(value) if key in LIMITS else value
            self.store.save(config)
            self.config()
            self.history.clear()
            self.status("配置已保存")
        elif command == "clear":
            self.history.clear()
            self.emit("user", "")
            self.emit("answer", "")
            self.status("对话已清空")
        elif command == "record":
            config = self.config()
            if not config["api_key"].strip():
                raise MimoError("请按 S 设置 API Key")
            self.cancel.clear()
            self.record_stop.clear()
            self.audio = Audio(config, self.cancel)
            self.emit("state", "recording")
            self.worker = threading.Thread(target=self.turn, args=(config,), daemon=True)
            self.worker.start()

    def turn(self, config):
        speaker = None
        producer_done = threading.Event()
        speech = queue.Queue(maxsize=8)
        failures = []
        try:
            self.emit("user", "")
            self.emit("answer", "")
            wav = self.audio.record(self.record_stop, self.status)
            self.emit("state", "working")
            self.status("正在识别...")
            api = API(config, self.cancel, self.status)
            body = {"model": config["asr_model"], "asr_options": {"language": "auto"},
                    "messages": [{"role": "user", "content": [{"type": "input_audio", "input_audio": {
                        "data": base64.b64encode(wav).decode("ascii"), "format": "wav"}}]}]}
            user = ""
            for delta in api.stream(body):
                user += delta.get("content") or ""
                if len(user) > 8192:
                    raise MimoError("识别结果过长")
                self.emit("user", user)
            if not user.strip():
                raise MimoError("未识别到语音，请重试")
            self.status("正在回答...")

            def play_sentences():
                try:
                    while not self.cancel.is_set():
                        try:
                            text = speech.get(timeout=0.1)
                        except queue.Empty:
                            if producer_done.is_set():
                                return
                            continue
                        self.status("正在流式播报... | Esc 取消")
                        self.audio.speak(audio_chunks(api, text))
                except Exception as exc:
                    failures.append(exc)
                    self.cancel.set()
                    self.audio.stop()

            def enqueue(text):
                while not self.cancel.is_set():
                    try:
                        speech.put(text, timeout=0.1)
                        return
                    except queue.Full:
                        pass
                raise Cancelled()

            speaker = threading.Thread(target=play_sentences, daemon=True)
            speaker.start()
            messages = [{"role": "system", "content": config["system_prompt"]}]
            turns = config["history_turns"]
            messages += self.history[-2 * turns:] if turns else []
            messages.append({"role": "user", "content": user})
            body = {"model": config["chat_model"], "messages": messages,
                    "max_completion_tokens": config["max_completion_tokens"], "thinking": {"type": "disabled"}}
            answer, pending = "", ""
            for delta in api.stream(body):
                content = delta.get("content") or ""
                answer += content
                pending += content
                if len(answer) > 16384:
                    raise MimoError("回复超出本机长度限制")
                if content:
                    self.emit("answer", answer)
                sentences, pending = split_sentences(pending)
                for sentence in sentences:
                    enqueue(sentence)
            if not answer.strip():
                raise MimoError("模型返回了空回复")
            for sentence in split_sentences(pending, final=True)[0]:
                enqueue(sentence)
            producer_done.set()
            speaker.join()
            if failures:
                raise failures[0]
            if self.cancel.is_set():
                raise Cancelled()
            self.history += [{"role": "user", "content": user}, {"role": "assistant", "content": answer}]
            self.history = self.history[-2 * turns:] if turns else []
            self.status("就绪 | ↵ 继续对话")
        except Exception as exc:
            was_cancelled = self.cancel.is_set()
            self.cancel.set()
            self.audio.stop()
            producer_done.set()
            if speaker:
                speaker.join()
            if failures and not isinstance(failures[0], Cancelled):
                exc = failures[0]
                was_cancelled = False
            if isinstance(exc, Cancelled) or (was_cancelled and not isinstance(exc, MimoError)):
                self.status("已取消 | ↵ 重新录音")
            else:
                self.emit("error", str(exc) if isinstance(exc, MimoError) else "音频或响应处理失败，请检查配置")
        finally:
            self.emit("state", "idle")


def main():
    lock = threading.Lock()

    def emit(kind, *values):
        with lock:
            print("\t".join([kind] + [value.encode("utf-8").hex() for value in values]), flush=True)

    controller = None
    try:
        controller = Controller(ConfigStore(), emit)
        controller.command("get")
    except (OSError, MimoError) as exc:
        emit("error", str(exc) if isinstance(exc, MimoError) else "无法读写 ~/mimo_talk/config.json，请检查权限")
    if controller is None:
        return 1

    def shutdown(_sig=None, _frame=None):
        controller.cancel.set()
        if controller.audio:
            controller.audio.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        for line in sys.stdin:
            try:
                if len(line) > 8192:
                    raise MimoError("配置输入过长")
                controller.command(line)
            except (ValueError, UnicodeError):
                emit("error", "配置格式不正确")
            except (OSError, MimoError) as exc:
                emit("error", str(exc) if isinstance(exc, MimoError) else "配置文件读写失败")
    finally:
        controller.cancel.set()
        if controller.audio:
            controller.audio.stop()
        if controller.worker:
            controller.worker.join(timeout=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
