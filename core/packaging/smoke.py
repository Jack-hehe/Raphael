"""Exercise an installed or relocated core using isolated data and no credential access."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class RpcProcess:
    def __init__(self, executable: Path, root: Path) -> None:
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
        for name in ("home", "appdata", "localappdata", "data", "empty-models"):
            (root / name).mkdir(exist_ok=True)
        self.process = subprocess.Popen(
            [str(executable)], cwd=executable.parent, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="strict", bufsize=1,
        )
        self.messages: queue.Queue = queue.Queue()
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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def smoke(executable: Path) -> dict:
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
                "raw_text": "\u8f6f\u4ef6 smoke token", "audio_seconds": 1.0, "stt_ms": 0,
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
            return {"passed": True, "core": str(executable), "version": hello["version"],
                    "elapsed_seconds": round(time.monotonic() - began, 2), "requests": len(rpc.calls),
                    "device_count": len(devices["devices"]), "exit_code": returncode,
                    "keyring_backend": "keyring.backends.fail.Keyring", "models": "empty",
                    "microphone_opened": False, "paid_api_called": False,
                    "calls": rpc.calls, "stderr": rpc.stderr}
        finally:
            rpc.close()


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
