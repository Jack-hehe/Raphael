"""End to end: the core as the Tauri app runs it, a child process speaking JSON-RPC on stdio.

The child gets a data directory of its own, a fake LLM (no network, no key) and a keyring that
fails on every use, so it cannot reach the real Credential Manager. The test with the speech
models plays a recording through the real microphone code path (WavRecorder in real time), the real
Silero VAD and the real Qwen3-ASR; it needs RAPHAEL_TEST_MODELS=1.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

from raphael_core.device.models import ModelManager
from raphael_core.device.stt import read_wav
from raphael_core.paths import models_dir

RPC_TIMEOUT = 60.0


class Core:
    """A running `python -m raphael_core`, driven the way the app drives it."""

    def __init__(self, tmp_path, **env):
        child_env = {
            **os.environ,
            "RAPHAEL_DATA_DIR": str(tmp_path),
            "RAPHAEL_FAKE_LLM": "1",
            # Never the real Credential Manager, whatever the fake LLM does.
            "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
            "PYTHONIOENCODING": "utf-8",
            **env,
        }
        for name in ("RAPHAEL_FAKE_AUDIO", "RAPHAEL_MODELS_DIR"):
            if name not in env:
                child_env.pop(name, None)
        self.started = time.perf_counter()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "raphael_core"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
        )
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self.raw_stdout: list[bytes] = []
        self.stderr = bytearray()
        self.notifications: list[dict] = []
        self.responses: dict[int, dict] = {}
        self._next_id = 0
        self._readers = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def _read_stdout(self):
        for line in self.proc.stdout:
            self.raw_stdout.append(line)
            self.lines.put(line)
        self.lines.put(None)

    def _read_stderr(self):
        for line in self.proc.stderr:
            self.stderr += line

    def call(self, method, params=None, *, timeout=RPC_TIMEOUT):
        """The whole reply message (`result` or `error`) to a request."""
        self._next_id += 1
        request = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            if self._next_id in self.responses:
                return self.responses.pop(self._next_id)
            try:
                line = self.lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                self.kill()
                pytest.fail(f"{method} was not answered within {timeout} s\n{self.log()}")
            if line is None:
                pytest.fail(f"the core ended while {method} was waiting\n{self.log()}")
            message = json.loads(line)
            if "id" in message:
                self.responses[message["id"]] = message
            else:
                self.notifications.append(message)

    def close(self, timeout=15):
        """Close stdin, as the app does when it quits; the exit code."""
        self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            pytest.fail(
                f"the core did not exit within {timeout} s of its stdin closing\n{self.log()}"
            )

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()

    def dispose(self):
        """Kill the child if it still runs, and close everything of it (no ResourceWarnings)."""
        self.kill()
        self.proc.wait(timeout=30)
        for reader in self._readers:
            reader.join(timeout=10)
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            pipe.close()

    def log(self):
        return "--- the core's stderr ---\n" + self.stderr.decode("utf-8", errors="replace")


@pytest.fixture
def spawn(tmp_path):
    started: list[Core] = []

    def make(**env):
        core = Core(tmp_path, **env)
        started.append(core)
        return core

    yield make
    for core in started:
        core.dispose()


def test_hello_settings_and_clean_exit(spawn):
    p = spawn()
    assert p.call("core.hello")["result"]["protocol"] == 1
    assert p.call("settings.get")["result"]["settings"]["monthly_limit_usd"] == 10.0
    assert p.close() == 0


def test_stdout_carries_protocol_lines_and_nothing_else(spawn):
    p = spawn()
    p.call("core.hello")
    p.call("models.status")
    p.call("audio.devices")
    p.call("dictation.cancel")  # waits for the speech side to be there
    assert p.close() == 0
    assert p.raw_stdout
    for line in p.raw_stdout:
        assert line.endswith(b"\n") and not line.endswith(b"\r\n")
        assert json.loads(line)["jsonrpc"] == "2.0"
    assert b"INFO" in bytes(p.stderr)  # the log is on stderr, where the app reads it


def test_the_methods_the_app_calls_answer_in_the_shapes_it_reads(spawn):
    p = spawn()
    hello = p.call("core.hello")["result"]
    assert hello["protocol"] == 1 and isinstance(hello["version"], str)
    status = p.call("models.status")["result"]
    assert status == {
        "models": [{"name": "silero-vad", "ready": False}, {"name": "qwen3-asr", "ready": False}]
    }
    listing = p.call("audio.devices")["result"]
    assert listing["selected"] is None
    for device in listing["devices"]:
        assert set(device) == {"index", "name", "is_default"}
    assert p.call("audio.set_device", {"name": "No Such Microphone"})["result"] == {}
    assert p.call("audio.devices")["result"]["selected"] == "No Such Microphone"
    assert p.call("audio.set_device", {"name": None})["result"] == {}
    assert p.call("audio.monitor", {"on": False})["result"] == {}
    assert p.call("secrets.status")["result"] == {"has_key": True}  # the fake LLM's key
    assert p.close() == 0


def test_dictating_without_the_models_says_not_configured(spawn):
    p = spawn()
    error = p.call("dictation.start", {"session_id": "none"})["error"]
    assert error["code"] == 1001  # NOT_CONFIGURED
    assert p.call("core.hello")["result"]["protocol"] == 1  # and the core carries on
    assert p.close() == 0


def test_core_shutdown_answers_and_the_core_exits_with_its_stdin(spawn):
    p = spawn()
    assert p.call("core.shutdown")["result"] == {}
    assert p.close() == 0


def test_core_shutdown_ends_the_core_with_its_stdin_still_open(spawn):
    p = spawn()
    p.call("audio.devices")  # the speech side is up
    assert p.call("core.shutdown")["result"] == {}
    try:
        code = p.proc.wait(timeout=30)  # and stdin stays open
    except subprocess.TimeoutExpired:
        pytest.fail(f"the core did not exit after core.shutdown\n{p.log()}")
    assert code == 0
    for line in p.raw_stdout:
        assert json.loads(line)["jsonrpc"] == "2.0"
    assert b"Traceback" not in bytes(p.stderr), p.log()
    assert b"Fatal Python error" not in bytes(p.stderr), p.log()


def test_a_bad_request_is_an_error_not_a_crash(spawn):
    p = spawn()
    assert p.call("no.such.method")["error"]["code"] == -32601
    assert p.call("audio.monitor", {"on": "yes"})["error"]["code"] == -32602
    assert p.call("core.hello")["result"]["protocol"] == 1
    assert p.close() == 0


@pytest.mark.models
def test_dictation_end_to_end(spawn):
    wav = ModelManager(models_dir()).dir("qwen3-asr") / "test_wavs" / "raokouling.wav"
    p = spawn(RAPHAEL_FAKE_AUDIO=str(wav), RAPHAEL_MODELS_DIR=str(models_dir()))
    assert p.call("dictation.start", {"session_id": "e2e"})["result"] == {}
    # The recording plays in real time; the models are still loading for the first half of it.
    time.sleep(read_wav(wav).size / 16000 + 0.5)
    r = p.call("dictation.stop", {"session_id": "e2e", "target_app": "pytest"}, timeout=180)[
        "result"
    ]
    assert r["text"] and r["cleaned"] is True
    assert r["audio_seconds"] > 10 and r["stt_ms"] > 0
    items = p.call("history.list")["result"]["items"]
    assert len(items) == 1
    assert items[0]["text"] == r["text"] and items[0]["target_app"] == "pytest"
    assert items[0]["model"] == "fake/echo" and items[0]["cost_usd"] == 0
    # The ring of the overlay was fed with the level of the session while it played.
    levels = [n for n in p.notifications if n["method"] == "audio.level"]
    assert len(levels) > 20
    assert {n["params"]["session_id"] for n in levels} == {"e2e"}
    assert all(0.0 <= n["params"]["level"] <= 1.0 for n in levels)
    assert p.close(timeout=30) == 0
    assert b"Traceback" not in bytes(p.stderr), p.log()
