"""Shell-free children, private run files and bounded process-tree cleanup."""

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

API_KEYS = frozenset({"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"})


def subscription_env(environ=None):
    return {key: value for key, value in (os.environ if environ is None else environ).items()
            if key.upper() not in API_KEYS}


class PrivateFiles:
    def __init__(self):
        self._directory = tempfile.TemporaryDirectory(prefix="raphael-cli-")
        self.path = Path(self._directory.name)

    async def secure(self):
        await secure_directory(self.path)

    def write(self, name, text):
        path = self.path / name
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        return str(path)

    async def aclose(self):
        """Remove the run's private files. Windows keeps a killed child's files open for a moment, so this
        tries again briefly rather than let that error replace how the run ended (live, a PermissionError
        replaced a cancellation one run in fifteen and the files stayed)."""
        for _ in range(40):
            try:
                self._directory.cleanup()
                return
            except PermissionError:
                await asyncio.sleep(.05)
        shutil.rmtree(self.path, ignore_errors=True)
        logger.warning("Could not remove a CLI run's private files at once")


async def secure_directory(path):
    path.chmod(0o700)
    if os.name == "nt":
        # Remove inherited grants before writing prompts or bearer credentials.
        import ctypes
        import ctypes.wintypes
        from ctypes import wintypes
        token = wintypes.HANDLE()
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise OSError("private_directory")
        try:
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
                raise OSError("private_directory")
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            value = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(value)):
                raise OSError("private_directory")
            try:
                identity = value.value
            finally:
                kernel.LocalFree(value)
        finally:
            kernel.CloseHandle(token)
        proc = await asyncio.create_subprocess_exec(
            "icacls.exe", str(path), "/inheritance:r", "/grant:r", f"*{identity}:(OI)(CI)F",
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            result = await asyncio.wait_for(proc.wait(), 5)
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        if result != 0:
            raise OSError("private_directory")


class _WindowsJob:
    """Kill descendants even when the leader exits before cancellation or teardown."""

    def __init__(self, pid):
        import ctypes as c
        from ctypes import wintypes as w

        class Basic(c.Structure):
            _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
                        ("flags", w.DWORD), ("min_ws", c.c_size_t), ("max_ws", c.c_size_t),
                        ("active", w.DWORD), ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class Limits(c.Structure):
            _fields_ = [("basic", Basic), ("io", c.c_ulonglong * 6),
                        ("process_memory", c.c_size_t), ("job_memory", c.c_size_t),
                        ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]

        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = self.kernel.OpenProcess(0x0101, False, pid)
        try:
            if not (self.handle and process and self.kernel.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits))
                    and self.kernel.AssignProcessToJobObject(self.handle, process)):
                self.close()
                raise OSError("job_assignment")
        finally:
            if process:
                self.kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Child:
    def __init__(self, command, *, environ=None, cwd=None):
        self.command = list(command)
        self.environ = subscription_env(environ)
        self.cwd = cwd
        self.process = None
        self._stderr = None
        self._job = None
        self._kill_lock = asyncio.Lock()
        self._closed = False

    async def start(self):
        if sum(len(arg) + 3 for arg in self.command) > 30000:
            raise ValueError("command_too_long")
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        self.process = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=self.environ, cwd=self.cwd, limit=4 * 1024 * 1024,
            **flags,
        )
        if os.name == "nt":
            try:
                self._job = _WindowsJob(self.process.pid)
            except OSError:
                # Older hosts that disallow nested jobs still have taskkill /T below.
                pass
        # Drain without logging or retaining potentially private diagnostics.
        self._stderr = asyncio.create_task(self._drain())
        return self

    async def _drain(self):
        while await self.process.stderr.read(65536):
            pass

    async def send(self, value):
        import json
        self.process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()

    async def kill(self):
        async with self._kill_lock:
            if self._closed:
                return
            await self._kill()
            self._closed = True

    async def _kill(self):
        if self.process is None:
            return
        if self._job:
            self._job.close()
        elif os.name == "nt" and self.process.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                "taskkill.exe", "/PID", str(self.process.pid), "/T", "/F",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                await asyncio.wait_for(killer.wait(), 5)
            except TimeoutError:
                killer.kill()
                await killer.wait()
        elif os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process.returncode is None:
            self.process.kill()
        await self.process.wait()
        if self._stderr:
            self._stderr.cancel()
            await asyncio.gather(self._stderr, return_exceptions=True)
