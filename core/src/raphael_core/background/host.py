"""The worker process, kept by the core: started when a task needs it, asked to carry tasks out, and
ended when it idles, misbehaves or the core closes.

The worker is the same executable as the core with `--worker` (`python -m raphael_core --worker` in
development; `raphael-core.exe --worker` in the package). The core starts it with `asyncio`'s
subprocess support rather than `multiprocessing`, which a frozen build makes painful, at a lower
priority and without a window, with the keys of the environment taken out, and talks to it on its
stdin and stdout (`worker.channel`). It belongs to the core's Job Object like any child (the app
puts the core in one: the worker dies with it), and ends by itself when its stdin closes.

`WorkerHost` owns one worker at a time:

- `run(run, spec)` starts the worker if it is not there (and says hello to it), points the worker's
  `host.chat` and `host.tool` at the `TaskRun`, sends `task.run` and waits for how it ended. If the
  worker dies meanwhile, that is `WorkerCrashed`; a worker that cannot be started is
  `WorkerUnavailable`. A task is never started again by itself after either.
- Three crashes within ten minutes switch the worker off until the core is started again
  (`disabled`): something is wrong that starting again will not mend.
- `cancel(run)` tells the worker to stop the task, and ends the process if it has not stopped within
  a few seconds. The `TaskRun` has already begun to refuse every call by then.
- An idle worker is ended after ten minutes, and `aclose` ends it for good.

Nothing here logs what a task is, or says.
"""

import asyncio
import contextlib
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from ..protocol import WORKER_STOPPED, RpcError
from ..worker.channel import Peer, PeerClosed
from .run import TaskRun, stopped

logger = logging.getLogger(__name__)

IDLE_S = 600.0
# How long a worker that was told to stop gets before it is ended.
GRACE_S = 5.0
HELLO_TIMEOUT_S = 20.0
# How long the end of a worker's output is waited for once the worker has exited.
PIPES_END_S = 5.0
CRASH_LIMIT = 3
CRASH_WINDOW_S = 600.0
# Longer than any message the core gets from the worker (asyncio's default is 64 KiB a line).
LINE_LIMIT = 16 * 1024 * 1024
# BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW: a task in the background must not slow the
# dictation down, and a console program started by a program with no window would open one.
_WINDOWS_FLAGS = 0x00004000 | 0x08000000
# What the environment of the worker leaves out: anything that looks like a secret. (There is none
# that the core needs to give it; it makes no call of its own.)
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS|CREDENTIAL|AUTH|COOKIE|SESSION", re.IGNORECASE)
# A proxy is left out only when its address holds a password: `scheme://user:password@host`.
_PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
_PROXY_PASSWORD = re.compile(r"//[^/@\s:]*:[^/@\s]+@")
# The longest log line of the worker that is looked at; a longer one is dropped.
STDERR_LINE_MAX = 8192
# The log lines of the worker that are passed on: a level and a logger. A traceback is not.
_LOG_LINE = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL) raphael_core\.worker\.")


class WorkerCrashed(Exception):
    """The worker died while it had a task."""


class WorkerUnavailable(WorkerCrashed):
    """The worker could not be started, or did not answer hello."""


class WorkerStopped(Exception):
    """The worker ended because the core asked it to (the task was cancelled, or ran out of time)."""


class Link(Protocol):
    """A running worker, as the host sees it."""

    peer: Peer

    async def stop(self) -> None:
        """End it politely (its stdin closes), and for good if it does not go."""
        ...

    def kill(self) -> None:
        """End it now."""
        ...


Connect = Callable[[], Awaitable[Link]]


def worker_command() -> list[str]:
    """What runs a worker: this executable, with `--worker`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    return [sys.executable, "-m", "raphael_core", "--worker"]


def _leaves_out(name: str, value: str) -> bool:
    """Whether the worker's environment goes without this variable: its name looks like a key, a
    password, a credential, a cookie or a session, or it is a proxy address that holds a password.
    (The bare `PWD` is the working directory, not a password.)"""
    if _SECRET_NAME.search(name):
        return True
    if name.upper() != "PWD" and "PWD" in name.upper():
        return True
    return name.upper() in _PROXY_NAMES and bool(_PROXY_PASSWORD.search(value))


def worker_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment of the worker: that of the core without what looks like a secret (see
    `_leaves_out`). PATH, SystemRoot, TEMP and the rest stay."""
    source = os.environ if environ is None else environ
    env = {name: value for name, value in source.items() if not _leaves_out(name, value)}
    env["PYTHONIOENCODING"] = "utf-8"
    env["RAPHAEL_WORKER"] = "1"
    return env


class ProcessLink:
    """A worker process and the peer that talks to it."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.peer = Peer(self._write)
        self._tasks: list[asyncio.Task[None]] = []

    def _write(self, line: str) -> None:
        stdin = self.process.stdin
        if stdin is None or stdin.is_closing():
            raise BrokenPipeError
        stdin.write(line.encode("utf-8") + b"\n")

    async def _lines(self) -> AsyncIterator[str]:
        stdout = self.process.stdout
        assert stdout is not None
        while True:
            try:
                raw = await stdout.readline()
            except (asyncio.LimitOverrunError, ValueError):
                logger.error("The worker wrote a line that is too long")
                return
            if not raw:
                return
            yield raw.decode("utf-8", errors="replace")

    async def _read_stderr(self) -> None:
        """Pass on the worker's status lines and drop the rest. Read in chunks: a line longer than
        the limit is dropped whole and the pipe goes on being drained (a worker that cannot write to
        a full pipe stops)."""
        stderr = self.process.stderr
        assert stderr is not None
        line = b""
        dropping = False
        while chunk := await stderr.read(65536):
            *complete, line = (line + chunk).split(b"\n")
            for raw in complete:
                self._log_line(raw, dropping)
                dropping = False
            if len(line) > STDERR_LINE_MAX:
                line, dropping = b"", True  # the rest of this line, up to its end, is dropped
        if line:
            self._log_line(line, dropping)

    def _log_line(self, raw: bytes, dropping: bool) -> None:
        text = raw.decode("utf-8", errors="replace").rstrip()
        # Any other line is dropped: a traceback may quote the task, so it is not passed on.
        if not dropping and len(raw) <= STDERR_LINE_MAX and _LOG_LINE.match(text):
            logger.info("worker: %s", text)

    async def _watch(self) -> None:
        await self.peer.serve(self._lines())
        # The worker's output ended: it is gone, or about to be.
        await self._reap()

    async def _reap(self) -> None:
        """See to it that the process is over and its pipes are let go of: a process that exited is
        not finished with until our end of its stdin is closed too."""
        stdin = self.process.stdin
        if stdin is not None and not stdin.is_closing():
            with contextlib.suppress(OSError):
                stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 3.0)
        except TimeoutError:
            self.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.process.wait(), 5.0)

    def start_reading(self) -> None:
        self._tasks = [
            asyncio.ensure_future(self._watch()),
            asyncio.ensure_future(self._read_stderr()),
        ]

    async def stop(self) -> None:
        stdin = self.process.stdin
        if stdin is not None and not stdin.is_closing():
            with contextlib.suppress(OSError):
                stdin.close()
        await self._reap()
        await self._finish()

    def kill(self) -> None:
        if self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                self.process.kill()

    async def _finish(self) -> None:
        """Let go of the pipes of a worker that has exited. Its output is read to the end first:
        asyncio closes the process's transport only once both pipes have reached their end, and a
        transport left open when the core's loop closes is a "Exception ignored ... unclosed
        transport" traceback on the way out. The process is over, so the end comes at once; on a
        busy machine it can come after the exit has been seen. The readers are stopped only if it
        does not come within `PIPES_END_S`."""
        self.peer.close()
        pending = [task for task in self._tasks if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=PIPES_END_S)
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


async def connect_process(command: Sequence[str] | None = None) -> Link:
    """Start a worker process (`command` is for the tests, which start another program)."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = _WINDOWS_FLAGS
    process = await asyncio.create_subprocess_exec(
        *(command if command is not None else worker_command()),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=worker_environment(),
        limit=LINE_LIMIT,
        **kwargs,
    )
    link = ProcessLink(process)
    link.start_reading()
    return link


class WorkerHost:
    """Keeps the worker. `connect` starts one (tests give another); the times are in seconds and
    `clock` is a monotonic clock."""

    def __init__(
        self,
        connect: Connect = connect_process,
        *,
        idle_s: float = IDLE_S,
        grace_s: float = GRACE_S,
        hello_timeout_s: float = HELLO_TIMEOUT_S,
        crash_limit: int = CRASH_LIMIT,
        crash_window_s: float = CRASH_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connect = connect
        self._idle_s = idle_s
        self._grace_s = grace_s
        self._hello_timeout_s = hello_timeout_s
        self._crash_limit = crash_limit
        self._crash_window_s = crash_window_s
        self._clock = clock
        self._link: Link | None = None
        self._current: TaskRun | None = None
        self._crashes: list[float] = []
        self._disabled = False
        self._closing = False
        self._starting = asyncio.Lock()
        self._idle_timer: asyncio.TimerHandle | None = None
        self._kill_timer: asyncio.TimerHandle | None = None
        self.pid: int | None = None

    @property
    def disabled(self) -> bool:
        """Switched off after crashing too often; stays so until the core is started again."""
        return self._disabled

    @property
    def running(self) -> bool:
        return self._link is not None and not self._link.peer.closed

    # --- starting -------------------------------------------------------------------------------

    async def _ensure(self) -> Link:
        async with self._starting:
            link = self._link
            if link is not None and not link.peer.closed:
                return link
            if link is not None:  # one that ended: its pipes are let go of before another starts
                self._link = None
                with contextlib.suppress(Exception):
                    await link.stop()
            try:
                link = await self._connect()
            except Exception as exc:
                logger.warning("The worker could not be started: %s", type(exc).__name__)
                self._note_crash()  # a worker that cannot start is no better than one that dies
                raise WorkerUnavailable from None
            link.peer.register("host.chat", self._chat)
            link.peer.register("host.tool", self._tool)
            try:
                hello = await asyncio.wait_for(
                    link.peer.request("worker.hello", {}), self._hello_timeout_s
                )
            except (TimeoutError, PeerClosed, RpcError) as exc:
                logger.warning("The worker did not answer hello: %s", type(exc).__name__)
                link.kill()
                with contextlib.suppress(Exception):
                    await link.stop()
                self._note_crash()
                raise WorkerUnavailable from None
            pid = hello.get("pid") if isinstance(hello, dict) else None
            self.pid = pid if isinstance(pid, int) else None
            logger.info("The worker is running (pid %s)", self.pid)
            self._link = link
            return link

    # --- the worker's calls ---------------------------------------------------------------------

    def _run_for(self, params: dict) -> TaskRun:
        run = self._current
        if run is None or params.get("task_id") != run.task_id:
            raise stopped("cancelled")  # a task that is over, or was never started
        return run

    async def _chat(self, params: dict) -> dict:
        return await self._run_for(params).chat(params)

    async def _tool(self, params: dict) -> dict:
        return await self._run_for(params).tool(params)

    # --- carrying a task out --------------------------------------------------------------------

    async def run(self, run: TaskRun, spec: dict) -> dict:
        """Have the worker carry the task out; the result is how it ended (`runner._result`).
        WorkerCrashed if it dies on the way, WorkerUnavailable if there is none to ask,
        WorkerStopped if the task was cancelled or timed out and the worker was ended for it."""
        if self._disabled:
            raise WorkerUnavailable
        self._cancel_idle()
        link = await self._ensure()
        self._current = run
        try:
            # The worker keeps to its own limits too; this is the net under them.
            reply = await link.peer.request("task.run", spec)
        except PeerClosed:
            if run.cancelled or run.timed_out or self._closing:
                raise WorkerStopped from None
            self._note_crash()
            raise WorkerCrashed from None
        except RpcError as error:
            if error.code == WORKER_STOPPED:
                raise WorkerStopped from None
            logger.warning("The worker refused a task: code=%s", error.code)
            raise WorkerUnavailable from None
        finally:
            self._current = None
            self._cancel_kill()
            self._arm_idle()
        if not isinstance(reply, dict):
            raise WorkerUnavailable
        return reply

    def cancel(self, run: TaskRun) -> None:
        """Stop the task: the worker is told, and ended if it has not stopped in `grace_s`."""
        run.cancel()
        link = self._link
        if link is None or self._current is not run:
            return
        link.peer.notify("task.cancel", {"task_id": run.task_id})
        self._cancel_kill()
        self._kill_timer = asyncio.get_running_loop().call_later(self._grace_s, self.kill)

    def kill(self) -> None:
        """End the worker now (it comes back when the next task needs it)."""
        link = self._link
        if link is not None:
            link.kill()

    def _note_crash(self) -> None:
        now = self._clock()
        self._crashes = [t for t in self._crashes if now - t < self._crash_window_s] + [now]
        if len(self._crashes) >= self._crash_limit:
            self._disabled = True
            logger.error("The worker crashed %d times: switched off until restart", len(self._crashes))

    # --- the timers -----------------------------------------------------------------------------

    def _arm_idle(self) -> None:
        self._cancel_idle()
        if self._closing or self._link is None:
            return
        self._idle_timer = asyncio.get_running_loop().call_later(
            self._idle_s, lambda: asyncio.ensure_future(self._idle_out())
        )

    def _cancel_idle(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _cancel_kill(self) -> None:
        if self._kill_timer is not None:
            self._kill_timer.cancel()
            self._kill_timer = None

    async def _idle_out(self) -> None:
        self._idle_timer = None
        if self._current is None:
            await self._end()

    async def _end(self) -> None:
        link, self._link = self._link, None
        if link is not None:
            with contextlib.suppress(Exception):
                await link.stop()

    async def aclose(self) -> None:
        """End the worker for good."""
        self._closing = True
        self._cancel_idle()
        self._cancel_kill()
        await self._end()
