"""Exercise an installed or relocated core using isolated data and no credential access.

Three passes. The first two each have a core of its own and temporary data. The first has the
fail-closed keyring, so cleanup stops before it would ask the model, and checks the plain local
operations. The second has the fake LLM (RAPHAEL_FAKE_LLM=1: an in-memory key and a client that
returns the transcript, no network), so a dictation really goes through cleanup. That loads the
prompt resource and the OpenCC configurations (s2twp, s2hk, t2s and tw2sp) from the bundle, which the
first pass never reaches: a missing file would otherwise switch cleanup off without a word. The
second pass also hands a task to the background worker, which makes the core start its worker: the
same executable again, with --worker. That the bundle can start itself that way, and that the worker
carries the scripted task out, is checked there. Neither pass needs a key.

The third starts the worker by itself (`raphael-core.exe --worker`) and checks the handshake: it says
hello, loads none of the core's libraries (it is meant to start in a fraction of a second, with no
speech library, no key store and no database in it), and ends with its stdin.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class RpcProcess:
    def __init__(
        self, executable: Path, root: Path, extra_environment: dict[str, str] | None = None
    ) -> None:
        # Deliberately omit inherited credentials, Python paths, proxies and Raphael overrides.
        environment = {name: value for name, value in os.environ.items()
                       if name.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}}
        environment.update({
            "PATH": str(Path(os.environ["SYSTEMROOT"]) / "System32"),
            "TEMP": str(root), "TMP": str(root), "HOME": str(root / "home"),
            "USERPROFILE": str(root / "home"), "APPDATA": str(root / "appdata"),
            "LOCALAPPDATA": str(root / "localappdata"),
            "RAPHAEL_DATA_DIR": str(root / "data"),
            "RAPHAEL_MODELS_DIR": str(root / "empty-models"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
        })
        environment.update(extra_environment or {})
        for name in ("home", "appdata", "localappdata", "data", "empty-models"):
            (root / name).mkdir(exist_ok=True)
        self.process = subprocess.Popen(
            [str(executable)], cwd=executable.parent, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="strict", bufsize=1,
        )
        self.messages: queue.Queue = queue.Queue()
        self.notifications: list[dict] = []
        self.stderr: list[str] = []
        self.calls: list[dict] = []
        self.request_id = 0
        self.readers = [threading.Thread(target=self._read_stdout, daemon=True),
                        threading.Thread(target=self._read_stderr, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _read_stdout(self) -> None:
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise RuntimeError("stdout contains a non-JSON-RPC message")
                self.messages.put(message)
        except Exception as error:
            self.messages.put(error)
        finally:
            self.messages.put(EOFError("Core stdout closed"))

    def _read_stderr(self) -> None:
        for line in self.process.stderr:
            self.stderr.append(line.rstrip())

    def call(self, method: str, params: dict | None = None, *, error_code: int | None = None) -> dict:
        self.request_id += 1
        request = {"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 90
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timed out waiting for {method}")
            try:
                reply = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty as error:
                raise RuntimeError(f"Timed out waiting for {method}") from error
            if isinstance(reply, BaseException):
                raise RuntimeError(f"{method}: {reply}; stderr: {self.stderr[-12:]}")
            if "id" not in reply:
                self.notifications.append(reply)
                continue
            if reply["id"] != self.request_id:
                raise RuntimeError(f"Unexpected response id: {reply['id']}")
            self.calls.append({"method": method, "response": reply})
            if error_code is not None:
                if reply.get("error", {}).get("code") != error_code:
                    raise RuntimeError(f"{method} did not return expected error: {reply}")
                return reply["error"]
            if "error" in reply:
                raise RuntimeError(f"{method} failed: {reply['error']}")
            return reply["result"]

    def wait_for_notification(self, method: str, timeout: float = 60.0) -> dict:
        """The params of the first notification of `method` (one that came earlier counts)."""
        deadline = time.monotonic() + timeout
        while True:
            for notification in self.notifications:
                if notification.get("method") == method:
                    return notification["params"]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"Timed out waiting for {method}; stderr: {self.stderr[-12:]}")
            try:
                message = self.messages.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if isinstance(message, BaseException):
                raise RuntimeError(f"{method}: {message}; stderr: {self.stderr[-12:]}")
            if "id" not in message:
                self.notifications.append(message)

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=10)
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
            for reader in self.readers:
                reader.join(timeout=2)


# A Simplified Chinese dictation, with a word the dictionary replaces.
SPOKEN = "\u8f6f\u4ef6 smoke token"
# What the second pass dictates for each Chinese variant: (languages, speech, the cleaned text).
# Each uses another OpenCC configuration: s2twp for Taiwan, s2hk for Hong Kong, t2s for Mainland.
CLEANED_VARIANTS = (
    (["zh-TW", "en-US"], SPOKEN, "\u8edf\u9ad4 Raphael Smoke"),
    (["zh-HK", "en-US"], SPOKEN, "\u8edf\u4ef6 Raphael Smoke"),
    (["zh-CN", "en-US"], "\u8edf\u4ef6 smoke token", "\u8f6f\u4ef6 Raphael Smoke"),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def shut_down(rpc: RpcProcess, root: Path) -> int:
    """Ask the core to stop while stdin stays open, and check how it went and what it left behind."""
    rpc.call("core.shutdown")
    # Keep stdin open: shutdown must stop the core without depending on pipe EOF.
    returncode = rpc.process.wait(timeout=15)
    for reader in rpc.readers:
        reader.join(timeout=2)
    require(returncode == 0, f"Core shutdown exited with {returncode}")
    while not rpc.messages.empty():
        trailing = rpc.messages.get_nowait()
        if isinstance(trailing, BaseException) and not isinstance(trailing, EOFError):
            raise RuntimeError(f"Invalid trailing stdout: {trailing}")
    require(any("Speech libraries imported" in line for line in rpc.stderr), "Native speech libraries did not finish importing")
    require(not any(fragment in line for line in rpc.stderr for fragment in (
        "The speech side could not be started", "Could not warm up OpenCC",
        "DLL load failed", "ModuleNotFoundError", "Traceback (most recent call last)",
    )), "A bundled dependency failed during startup")
    require(not list((root / "empty-models").iterdir()), "Smoke unexpectedly downloaded models")
    require((root / "data" / "raphael.db").is_file(), "Database was not written to the isolated directory")
    return returncode


def smoke_without_a_key(executable: Path) -> dict:
    """The first pass: a core that cannot read a credential, so cleanup stops before the model."""
    began = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="raphael-frozen-smoke-") as temporary:
        root = Path(temporary)
        rpc = RpcProcess(executable, root)
        try:
            hello = rpc.call("core.hello")
            require(hello.get("protocol") == 1, "Incompatible JSON-RPC protocol")
            status = rpc.call("status.get")
            require(status["openrouter"]["state"] == "error" and status["openrouter"]["error"] == "auth",
                    "The fail-closed keyring did not produce the expected auth status")
            rpc.call("secrets.status", error_code=-32603)
            models = rpc.call("models.status")["models"]
            require(bool(models) and all(not item["ready"] for item in models), "The isolated models directory is not empty")
            devices = rpc.call("audio.devices")
            require(isinstance(devices.get("devices"), list), "PortAudio did not return a device list")
            require(rpc.call("history.list")["items"] == [], "History was not isolated")
            entry = rpc.call("dictionary.add", {"word": "Raphael Smoke", "misheard": ["smoke token"]})["entry"]
            require(any(item["id"] == entry["id"] for item in rpc.call("dictionary.list")["entries"]), "Dictionary write did not persist")
            finalized = rpc.call("brain.finalize_dictation", {
                "raw_text": SPOKEN, "audio_seconds": 1.0, "stt_ms": 0,
                "target_app": "packaging-smoke", "stt": "packaging-smoke",
            })
            require(finalized["text"] == "\u8edf\u9ad4 Raphael Smoke", "OpenCC data or dictionary replacement is unavailable")
            require(finalized["cleaned"] is False, "Unexpected paid cleanup")
            latest = rpc.call("history.latest")["item"]
            require(latest["id"] == finalized["id"] and latest["cost_usd"] == 0, "History write failed or incurred a cost")
            items = rpc.call("history.list", {"query": "Raphael Smoke"})["items"]
            require(len(items) == 1 and items[0]["id"] == latest["id"], "History search failed")
            require(rpc.call("history.delete", {"id": latest["id"]})["deleted"], "History delete failed")
            require(rpc.call("history.latest")["item"] is None, "History delete did not persist")
            require(rpc.call("dictionary.delete", {"id": entry["id"]})["deleted"], "Dictionary delete failed")
            require(rpc.call("dictionary.list")["entries"] == [], "Dictionary delete did not persist")
            # The voice assistant is part of the bundle. With no key it answers a refusal that says
            # why; nothing is asked of a model and nothing is done to this computer (a request that
            # is a command would be: so this one is not).
            refused = rpc.call("agent.ask", {"session_id": "smoke", "text": "What is the weather?",
                                             "ui_language": "en", "target_app": None})
            require(refused["status"] == "done" and refused["prefix"] == "Negative",
                    "The voice assistant did not refuse a request it cannot answer without a key")
            turns = rpc.call("agent.history.list")["turns"]
            require(len(turns) == 1 and turns[0]["status"] == "error", "The assistant's turn was not recorded")
            require(rpc.call("agent.history.clear") == {"deleted": 1}, "The assistant's history was not cleared")
            # The plugins are part of the bundle too, with the MCP SDK and what it imports. A server
            # whose program does not exist makes the SDK load and fail the one way that proves it:
            # "not_found". A bundle that left a module of the SDK out fails another way ("error").
            require(rpc.call("integrations.list") == {"servers": []}, "The plugins did not start empty")
            added = rpc.call("integrations.add", {"name": "Smoke", "transport": "stdio",
                                                  "command": "raphael-smoke-no-such-program"})["server"]
            refreshed = rpc.call("integrations.refresh", {"id": added["id"]})["server"]
            require(refreshed["status"] == "error" and refreshed["error"] == "not_found",
                    f"The MCP SDK did not load in the bundle (a missing program should be not_found, "
                    f"was {refreshed['error']})")
            require(rpc.call("integrations.disconnect", {"id": added["id"]}).get("removed") is True,
                    "A plugin was not removed")
            returncode = shut_down(rpc, root)
            return {"passed": True, "core": str(executable), "version": hello["version"],
                    "elapsed_seconds": round(time.monotonic() - began, 2), "requests": len(rpc.calls),
                    "device_count": len(devices["devices"]), "exit_code": returncode,
                    "keyring_backend": "keyring.backends.fail.Keyring", "models": "empty",
                    "microphone_opened": False, "paid_api_called": False,
                    "calls": rpc.calls, "stderr": rpc.stderr}
        finally:
            rpc.close()


def smoke_with_the_fake_llm(executable: Path) -> dict:
    """The second pass: a core with the fake LLM, so that a dictation goes through cleanup.

    The text is cleaned only if the cleanup prompt (a data file of the bundle) loads, the reply
    passes the content check, which converts both texts with OpenCC (t2s and tw2sp), and the final
    text is made with the OpenCC configuration of the chosen Chinese (s2twp, s2hk or t2s). If any of
    it is missing from the bundle the dictation falls back to its raw text and `cleaned` is false.
    """
    began = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="raphael-frozen-smoke-llm-") as temporary:
        root = Path(temporary)
        rpc = RpcProcess(executable, root, {"RAPHAEL_FAKE_LLM": "1"})
        try:
            require(rpc.call("core.hello").get("protocol") == 1, "Incompatible JSON-RPC protocol")
            require(rpc.call("secrets.status")["has_key"] is True, "The fake LLM did not get its in-memory key")
            rpc.call("dictionary.add", {"word": "Raphael Smoke", "misheard": ["smoke token"]})
            for languages, spoken, expected in CLEANED_VARIANTS:
                rpc.call("settings.set", {"key": "languages", "value": languages})
                finalized = rpc.call("brain.finalize_dictation", {
                    "raw_text": spoken, "audio_seconds": 1.0, "stt_ms": 0,
                    "target_app": "packaging-smoke", "stt": "packaging-smoke",
                })
                require(finalized["cleaned"] is True and finalized["fallback_reason"] is None,
                        f"{languages[0]}: the dictation was not cleaned ({finalized['fallback_reason']}): "
                        "the cleanup prompt or an OpenCC configuration is missing from the bundle")
                require(finalized["text"] == expected, f"{languages[0]}: the cleaned text was converted wrongly")
                latest = rpc.call("history.latest")["item"]
                require(latest["id"] == finalized["id"] and latest["cleaned"] is True
                        and latest["model"] == "fake/echo" and latest["cost_usd"] == 0,
                        f"{languages[0]}: the cleaned dictation was not saved as cleaned at no cost")
            # The voice assistant answers small talk with the fake LLM, in the language asked for.
            spoken = rpc.call("agent.ask", {"session_id": "smoke", "text": "hello",
                                            "ui_language": "en", "target_app": None})
            require(spoken == {"status": "done", "turn_id": 1, "prefix": "Answer", "text": "hello"},
                    "The voice assistant did not answer with the fake LLM")
            require([turn["question"] for turn in rpc.call("agent.history.list")["turns"]] == ["hello"],
                    "The assistant's turn was not recorded")
            # A task for the background worker: the core starts its worker (this executable again,
            # with --worker), which carries out the fake model's script and reports. If the bundle
            # could not start itself that way the task would end interrupted or failed.
            handed = rpc.call("agent.ask", {"session_id": "smoke", "text": "[delegate] what time is it",
                                            "ui_language": "en", "target_app": None})
            require(handed["status"] == "done" and handed["text"] == "Handed to the background.",
                    "The task was not handed to the background worker")
            task = rpc.wait_for_notification("worker.report", timeout=90)
            require(task["state"] == "done" and task["text"] == "The scripted task saw 1 results.",
                    f"The background worker did not carry the task out: {task['state']}")
            steps = rpc.call("worker.get", {"id": task["task_id"]})["task"]["steps"]
            require([step["kind"] for step in steps] == ["model", "tool", "model"],
                    "The background worker's steps were not written down")
            returncode = shut_down(rpc, root)
            return {"passed": True, "fake_llm": True, "cleaned": True, "background_task": True,
                    "languages": [languages[0] for languages, _, _ in CLEANED_VARIANTS],
                    "elapsed_seconds": round(time.monotonic() - began, 2), "requests": len(rpc.calls),
                    "exit_code": returncode, "calls": rpc.calls, "stderr": rpc.stderr}
        finally:
            rpc.close()


# What the worker process must not have loaded: the core's libraries and everything secret. The worker
# says so itself when asked to (worker.hello), but the smoke test cannot look inside a frozen process,
# so it checks what it can see: how fast it starts and that it says nothing on stderr but its log.
WORKER_START_SECONDS = 20.0


def smoke_the_worker(executable: Path, extra_environment: dict[str, str] | None = None) -> dict:
    """The third pass: `raphael-core.exe --worker` on its own, as the core starts it."""
    began = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="raphael-frozen-smoke-worker-") as temporary:
        root = Path(temporary)
        environment = {name: value for name, value in os.environ.items()
                       if name.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}}
        environment.update({
            "PATH": str(Path(os.environ["SYSTEMROOT"]) / "System32"),
            "TEMP": str(root), "TMP": str(root), "HOME": str(root), "USERPROFILE": str(root),
            "APPDATA": str(root), "LOCALAPPDATA": str(root),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring", "RAPHAEL_WORKER": "1",
        })
        environment.update(extra_environment or {})
        process = subprocess.Popen(
            [str(executable), "--worker"], cwd=executable.parent, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="strict", bufsize=1,
        )
        try:
            hello = {"jsonrpc": "2.0", "id": 1, "method": "worker.hello", "params": {}}
            process.stdin.write(json.dumps(hello) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()  # the worker answers in a fraction of a second
            started = round(time.monotonic() - began, 2)
            require(bool(line), f"The worker said nothing; it exited with {process.poll()}")
            reply = json.loads(line)
            require(reply.get("id") == 1 and reply.get("result", {}).get("protocol") == 1,
                    "The worker did not answer hello in the protocol the core speaks")
            require(isinstance(reply["result"].get("pid"), int), "The worker did not say its process id")
            require(started < WORKER_START_SECONDS, f"The worker took {started} s to start")
            process.stdin.close()  # its core is gone: it ends by itself
            returncode = process.wait(timeout=15)
            rest, log = process.stdout.read(), process.stderr.read()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        require(returncode == 0, f"The worker exited with {returncode}")
        require(rest == "", "The worker wrote something besides protocol lines to stdout")
        require(not re.search(r"Traceback|ModuleNotFoundError|DLL load failed", log),
                f"The worker failed while it started: {log[-400:]}")
        require("The worker is serving on stdio" in log, "The worker did not report that it was serving")
        return {"passed": True, "start_seconds": started, "exit_code": returncode,
                "elapsed_seconds": round(time.monotonic() - began, 2)}


def smoke_the_voice_decoder(executable: Path) -> dict:
    """The fourth pass: `raphael-core.exe --check-voice-decoder` decodes a small Opus clip that is
    built into the core, which it can do only if the bundle carries libsndfile with its codecs."""
    began = time.monotonic()
    result = subprocess.run(
        [str(executable), "--check-voice-decoder"], cwd=executable.parent, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    require(result.returncode == 0 and "voice decoder ok" in result.stdout,
            f"The bundled audio decoder did not decode an Opus clip (exit {result.returncode}): "
            f"{(result.stdout + result.stderr)[-400:]}")
    return {"passed": True, "elapsed_seconds": round(time.monotonic() - began, 2)}


def smoke(executable: Path) -> dict:
    """The four passes. The report is the first pass's, with the second under `cleanup_pass`, the
    third under `worker_pass` and the fourth under `voice_pass`."""
    began = time.monotonic()
    report = smoke_without_a_key(executable)
    cleanup = smoke_with_the_fake_llm(executable)
    report["cleanup_pass"] = {name: value for name, value in cleanup.items() if name not in {"calls", "stderr"}}
    report["requests"] += cleanup["requests"]
    report["calls"] = [*report["calls"], *cleanup["calls"]]
    report["stderr"] = [*report["stderr"], *cleanup["stderr"]]
    report["worker_pass"] = smoke_the_worker(executable)
    report["voice_pass"] = smoke_the_voice_decoder(executable)
    report["elapsed_seconds"] = round(time.monotonic() - began, 2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True, type=Path, help="Installed or relocated raphael-core.exe")
    parser.add_argument("--report", type=Path, help="Optional JSON evidence file")
    args = parser.parse_args()
    executable = args.core.resolve(strict=True)
    report = smoke(executable)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: value for name, value in report.items() if name not in {"calls", "stderr"}}, indent=2))


if __name__ == "__main__":
    main()
