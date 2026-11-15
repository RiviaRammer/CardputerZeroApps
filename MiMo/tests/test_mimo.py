import base64
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mimo


class Response:
    def __init__(self, lines=(), status=200, headers=None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/event-stream"}
        self.lines = lines
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_lines(self, **kwargs):
        yield from self.lines


def events(*chunks, done=True):
    lines = []
    for chunk in chunks:
        lines += [("data: " + json.dumps(chunk, ensure_ascii=False)).encode(), b""]
    if done:
        lines += [b"data: [DONE]", b""]
    return lines


def delta(content):
    return {"choices": [{"delta": {"content": content}, "finish_reason": None}]}


class ConfigTests(unittest.TestCase):
    def test_create_and_atomic_edit(self):
        with tempfile.TemporaryDirectory() as root:
            store = mimo.ConfigStore(Path(root) / "MiMo")
            config = store.load()
            self.assertEqual(config, mimo.DEFAULTS)
            config.update(api_key="test-key", extension={"keep": True})
            store.save(config)
            self.assertEqual(store.load(), config)
            self.assertEqual(list(store.root.iterdir()), [store.path])
            if os.name != "nt":
                self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_invalid_json_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            store = mimo.ConfigStore(root)
            store.path.write_text("{broken", encoding="utf-8")
            controller = mimo.Controller(store, lambda *args: None)
            with self.assertRaises(mimo.MimoError):
                controller.command("set\tapi_key\t74657374")
            self.assertEqual(store.path.read_text(), "{broken")

    def test_bom_and_partial_config(self):
        with tempfile.TemporaryDirectory() as root:
            store = mimo.ConfigStore(root)
            store.path.write_text('{"api_key":"test"}', encoding="utf-8-sig")
            self.assertEqual(store.load()["chat_model"], "mimo-v2.6-flash")

    def test_validation(self):
        for update in ({"max_record_seconds": 0}, {"history_turns": True}, {"voice": "bad"},
                       {"base_url": "http://example.com"}, {"api_key": "key\nheader"},
                       {"tts_model": "mimo-v2.5-tts-voicedesign"}, {"base_url": "https://user:pass@example.com"}):
            with self.subTest(update=update), self.assertRaises(mimo.MimoError):
                mimo.validate(update)

    def test_lcd_edit_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as root:
            store = mimo.ConfigStore(root)
            store.save(dict(mimo.DEFAULTS, api_key="test", custom=7))
            output = []
            controller = mimo.Controller(store, lambda *x: output.append(x))
            controller.command("set\thistory_turns\t32")
            self.assertEqual(store.load()["history_turns"], 2)
            self.assertEqual(store.load()["custom"], 7)
            self.assertIn(("status", "配置已保存"), output)

    def test_config_migration_preserves_active_settings_and_prefers_new_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "home", return_value=Path(directory)):
            legacy = Path(directory) / "MiMo" / "config.json"
            legacy.parent.mkdir()
            old_json = '{"api_key":"old","voice":"Mia","voice_reference":true}'
            legacy.write_text(old_json, encoding="utf-8")
            (legacy.parent / "demo.mp3").write_bytes(b"sample")
            store = mimo.ConfigStore()
            self.assertEqual(store.path, Path(directory) / "mimo_talk" / "config.json")
            config = store.load()
            self.assertEqual((config["api_key"], config["voice"]), ("old", "Mia"))
            self.assertNotIn("voice_reference", json.loads(store.path.read_text(encoding="utf-8")))
            self.assertEqual(legacy.read_text(encoding="utf-8"), old_json)
            self.assertEqual(list(store.root.iterdir()), [store.path])
            store.save(dict(config, api_key="new"))
            self.assertEqual(mimo.ConfigStore().load()["api_key"], "new")


class StreamTests(unittest.TestCase):
    def api(self, cancel=None):
        return mimo.API(dict(mimo.DEFAULTS, api_key="fake-key"), cancel or threading.Event(), lambda _: None)

    def test_sse_comments_crlf_multiline_unicode(self):
        raw = [b": heartbeat\r", b"event: message", b'data: {"text":', 'data: "你好"}\r'.encode(), b"", b"data:[DONE]"]
        self.assertEqual(list(mimo.sse_events(raw)), ['{"text":\n"你好"}', "[DONE]"])

    def test_stream_contract_and_usage(self):
        response = Response(events(delta("你"), {"choices": []}, delta("好")))
        with patch.object(mimo.requests, "post", return_value=response) as post:
            self.assertEqual(list(self.api().stream({"model": "test"})), [{"content": "你"}, {"content": "好"}])
            args, kwargs = post.call_args
            self.assertEqual(args[0], "https://api.xiaomimimo.com/v1/chat/completions")
            self.assertEqual(kwargs["headers"]["api-key"], "fake-key")
            self.assertTrue(kwargs["json"]["stream"])
            self.assertFalse(kwargs["allow_redirects"])
            self.assertTrue(response.closed)

    def test_truncated_stream_does_not_retry(self):
        with patch.object(mimo.requests, "post", return_value=Response(events(delta("部分"), done=False))) as post:
            with self.assertRaisesRegex(mimo.MimoError, "中断"):
                list(self.api().stream({}))
            self.assertEqual(post.call_count, 1)

    def test_finish_reason_without_done(self):
        response = Response(events({"choices": [{"delta": {}, "finish_reason": "stop"}]}, done=False))
        with patch.object(mimo.requests, "post", return_value=response):
            self.assertEqual(list(self.api().stream({})), [{}])

    def test_invalid_json_and_in_stream_error(self):
        for lines in ([b"data: {bad}", b""], events({"error": {"message": "failure"}})):
            with patch.object(mimo.requests, "post", return_value=Response(lines)):
                with self.assertRaises(mimo.MimoError):
                    list(self.api().stream({}))

    def test_auth_error_is_not_retried_and_no_secret(self):
        with patch.object(mimo.requests, "post", return_value=Response(status=401)) as post:
            with self.assertRaises(mimo.MimoError) as error:
                list(self.api().stream({}))
            self.assertNotIn("fake-key", str(error.exception))
            self.assertEqual(post.call_count, 1)

    def test_retry_after_then_success(self):
        cancel = threading.Event()
        first = Response(status=429, headers={"Retry-After": "3"})
        with patch.object(cancel, "wait", return_value=False) as wait, patch.object(mimo.requests, "post", side_effect=[first, Response(events(delta("ok")))]):
            self.assertEqual(list(self.api(cancel).stream({})), [{"content": "ok"}])
            wait.assert_called_once_with(3)
        self.assertTrue(first.closed)

    def test_cancel_during_backoff(self):
        cancel = threading.Event()
        with patch.object(cancel, "wait", return_value=True), patch.object(mimo.requests, "post", return_value=Response(status=429)) as post:
            with self.assertRaises(mimo.Cancelled):
                list(self.api(cancel).stream({}))
            self.assertEqual(post.call_count, 1)

    def test_network_failure_has_safe_message(self):
        with patch.object(mimo.requests, "post", side_effect=mimo.requests.Timeout("fake-key")):
            with self.assertRaises(mimo.MimoError) as error:
                list(self.api().stream({}))
            self.assertNotIn("fake-key", str(error.exception))

    def test_cancellation_during_sse_heartbeats(self):
        cancel = threading.Event()

        def heartbeat():
            yield b": ping"
            cancel.set()
            yield b": ping"

        with patch.object(mimo.requests, "post", return_value=Response(heartbeat())):
            with self.assertRaises(mimo.Cancelled):
                list(self.api(cancel).stream({}))

    def test_audio_payload_and_invalid_base64(self):
        api = self.api()
        chunk = {"audio": {"data": base64.b64encode(b"\x01\x02\x03").decode()}}
        with patch.object(api, "stream", return_value=iter([{}, chunk])) as stream:
            self.assertEqual(list(mimo.audio_chunks(api, "你好")), [b"\x01\x02\x03"])
            body = stream.call_args.args[0]
            self.assertEqual(body["model"], "mimo-v2.5-tts")
            self.assertEqual(body["messages"][-1], {"role": "assistant", "content": "你好"})
            self.assertEqual(body["audio"]["format"], "pcm16")
        with patch.object(api, "stream", return_value=iter([{"audio": {"data": "??"}}])):
            with self.assertRaises(mimo.MimoError):
                list(mimo.audio_chunks(api, "你好"))

    def test_sentences_preserve_text(self):
        sentences, rest = mimo.split_sentences("你好。Hello. 世界！尾巴")
        self.assertEqual(sentences, ["你好。", "Hello.", "世界！"])
        self.assertEqual(rest, "尾巴")
        long = "长" * 321
        sentences, rest = mimo.split_sentences(long)
        self.assertEqual([len(s) for s in sentences], [160, 160])
        self.assertEqual("".join(sentences) + rest, long)
        self.assertEqual(mimo.split_sentences(rest, True), (["长"], ""))
        sentences, rest = mimo.split_sentences(long + "。")
        self.assertTrue(all(len(sentence) <= 160 for sentence in sentences))
        self.assertEqual("".join(sentences) + rest, long + "。")

    def test_wave_format(self):
        pcm = b"\x01\x00" * 16000
        with wave.open(io.BytesIO(mimo.wav_bytes(pcm))) as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (16000, 1, 2))
            self.assertEqual(wav.readframes(16000), pcm)


class TurnTests(unittest.TestCase):
    def run_turn(self, controller, cfg):
        controller.cancel.clear()
        controller.record_stop.clear()
        controller.audio = FakeAudio(cfg, controller.cancel)
        controller.turn(cfg)

    def test_pipeline_streams_before_chat_finishes_and_keeps_history(self):
        output, requests, spoken = [], [], []
        started = threading.Event()

        def stream(api, body):
            requests.append(body)
            if body["model"] == mimo.DEFAULTS["asr_model"]:
                yield {"content": "你好"}
            elif body["model"] == mimo.DEFAULTS["chat_model"]:
                yield {"content": "第一句。"}
                self.assertTrue(started.wait(2), "TTS must begin before chat stream ends")
                yield {"content": "第二句"}
            else:
                spoken.append(body["messages"][-1]["content"])
                started.set()
                yield {"audio": {"data": "AAA="}}

        with tempfile.TemporaryDirectory() as root, patch.object(mimo.API, "stream", stream):
            cfg = dict(mimo.DEFAULTS, api_key="test", history_turns=1)
            controller = mimo.Controller(mimo.ConfigStore(root), lambda *x: output.append(x))
            self.run_turn(controller, cfg)
            self.run_turn(controller, cfg)
            self.assertEqual(spoken, ["第一句。", "第二句", "第一句。", "第二句"])
            self.assertEqual(len(controller.history), 2)
            chats = [r for r in requests if r["model"] == cfg["chat_model"]]
            self.assertEqual(len(chats[1]["messages"]), 4)
            self.assertEqual(chats[0]["thinking"], {"type": "disabled"})
            self.assertEqual(output[-1], ("state", "idle"))
            self.assertFalse(any(e[0] == "error" for e in output))

    def test_playback_failure_unblocks_producer_and_preserves_history(self):
        output = []

        def stream(api, body):
            if body["model"] == mimo.DEFAULTS["asr_model"]:
                yield {"content": "问题"}
            elif body["model"] == mimo.DEFAULTS["chat_model"]:
                for _ in range(40):
                    yield {"content": "一句话。"}
            else:
                raise mimo.MimoError("HTTP 401: API Key 无效")

        with tempfile.TemporaryDirectory() as root, patch.object(mimo.API, "stream", stream):
            controller = mimo.Controller(mimo.ConfigStore(root), lambda *x: output.append(x))
            self.run_turn(controller, dict(mimo.DEFAULTS, api_key="test"))
            self.assertEqual(controller.history, [])
            self.assertTrue(any(e[0] == "error" and "401" in e[1] for e in output))
            self.assertEqual(output[-1], ("state", "idle"))

    def test_record_cancel_never_sends_network_request(self):
        with tempfile.TemporaryDirectory() as root, patch.object(mimo.API, "stream") as stream:
            output = []
            controller = mimo.Controller(mimo.ConfigStore(root), lambda *x: output.append(x))
            controller.audio = FakeAudio({}, controller.cancel)
            controller.cancel.set()
            controller.turn(mimo.DEFAULTS)
            stream.assert_not_called()
            self.assertEqual(controller.history, [])
            self.assertEqual(output[-1], ("state", "idle"))


class FakeAudio:
    def __init__(self, cfg, cancel):
        self.cancel = cancel

    def record(self, stop, status):
        if self.cancel.is_set():
            raise mimo.Cancelled()
        return mimo.wav_bytes(b"\0\0" * 8000)

    def speak(self, chunks):
        for _ in chunks:
            if self.cancel.is_set():
                raise mimo.Cancelled()

    def stop(self):
        pass


@unittest.skipUnless(os.name == "posix", "ALSA pipe IO requires POSIX")
class AudioPipeTests(unittest.TestCase):
    def test_default_route_uses_session_socket_and_keeps_explicit_pcm(self):
        with tempfile.TemporaryDirectory() as root, socket.socket(socket.AF_UNIX) as server:
            pulse = Path(root) / "pulse"
            pulse.mkdir()
            native = pulse / "native"
            env = {"XDG_RUNTIME_DIR": root}
            self.assertEqual(mimo.audio_route("default", env, os.getuid()), "default")
            server.bind(str(native))
            self.assertEqual(mimo.audio_route("plughw:0,0", env, os.getuid()), "plughw:0,0")
            self.assertNotIn("PULSE_SERVER", env)
            self.assertEqual(mimo.audio_route("default", env, os.getuid()), "pulse")
            self.assertEqual(env["PULSE_SERVER"], f"unix:{native}")

    def launch(self, audio, mode, path=""):
        original = audio.start
        helper = Path(__file__).with_name("fake_alsa.py")
        return patch.object(audio, "start", side_effect=lambda args, **kwargs:
                            original([sys.executable, str(helper), mode, str(path)], **kwargs))

    def test_pcm_carries_odd_byte_across_chunks(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "pcm"
            audio = mimo.Audio(mimo.DEFAULTS, threading.Event())
            with self.launch(audio, "play", output):
                audio.speak(iter([b"\x01", b"\x02\x03", b"\x04"]))
            self.assertEqual(output.read_bytes(), b"\x01\x02\x03\x04")
            self.assertFalse(audio.processes)

    def test_odd_final_byte_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            audio = mimo.Audio(mimo.DEFAULTS, threading.Event())
            with self.launch(audio, "play", Path(root) / "pcm"), self.assertRaises(mimo.MimoError):
                audio.speak(iter([b"\x01"]))
            self.assertFalse(audio.processes)

    def test_record_stop_builds_valid_wave_and_reaps_process(self):
        audio = mimo.Audio(mimo.DEFAULTS, threading.Event())
        stop = threading.Event()
        with self.launch(audio, "record"):
            result = audio.record(stop, lambda text: stop.set() if "录音 1s" in text else None)
        with wave.open(io.BytesIO(result)) as wav:
            self.assertEqual(wav.readframes(16000), b"\x01\x02" * 16000)
        self.assertFalse(audio.processes)

    def test_record_reports_actual_audio_failure(self):
        for detail, hint in (("Permission denied", "访问被拒绝"),
                             ("Device or resource busy", "已被占用"),
                             ("Unknown PCM missing", "无法打开")):
            with self.subTest(detail=detail):
                cfg = dict(mimo.DEFAULTS, capture_device="plughw:0,0")
                audio = mimo.Audio(cfg, threading.Event())
                with self.launch(audio, "fail", detail), patch.object(sys, "stderr", io.StringIO()) as log:
                    with self.assertRaisesRegex(mimo.MimoError, hint):
                        audio.record(threading.Event(), lambda _: None)
                    self.assertIn(detail, log.getvalue())
                self.assertFalse(audio.processes)

    def test_playback_cancel_reaps_child(self):
        cancel = threading.Event()
        audio = mimo.Audio(mimo.DEFAULTS, cancel)
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        start = time.monotonic()
        try:
            with self.launch(audio, "hang"), self.assertRaises(mimo.Cancelled):
                audio.speak(iter([b"\0" * 1000000]))
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - start, 2)
        self.assertFalse(audio.processes)


if __name__ == "__main__":
    unittest.main()
