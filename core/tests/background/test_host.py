"""Tests for the worker process as the core keeps it: starting it, a worker that dies or hangs, the
crashes that switch it off, and its idle end. The workers here are small real programs (a few lines
of Python, not the real worker), so the processes, pipes and kills are the real ones."""

import asyncio
import logging
import os
import sys

import pytest

from raphael_core.background.host import (
    HELLO_TIMEOUT_S,
    WorkerCrashed,
    WorkerHost,
    WorkerStopped,
    WorkerUnavailable,
    connect_process,
    worker_command,
    worker_environment,
)

from .conftest import until
from .test_run import Rig as RunRig

SCRIPT = r"""
import json, os, sys, time
mode = sys.argv[1]

def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()

if mode == "never_hello":
    time.sleep(60)
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "worker.hello":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"protocol": 1, "pid": os.getpid()}})
    elif method == "task.run":
        if mode == "crash":
            os._exit(3)
        if mode == "hang":
            time.sleep(60)
        if mode == "noisy":
            sys.stderr.write("Traceback (most recent call last):\n  PRIVATE-TRACE\n")
            sys.stderr.write("INFO raphael_core.worker.runner: a line that is passed on\n")
            sys.stderr.write("WARNING somebody.else: a line that is not\n")
            sys.stderr.flush()
        if mode == "bigerr":
            sys.stderr.write("x" * 300000 + "\n")
            sys.stderr.write("INFO raphael_core.worker.runner: after the big line\n")
            sys.stderr.flush()
        result = {"status": "done", "final": "ok", "kind": None, "detail": None,
                  "model_calls": 0, "tool_calls": 0}
        if mode == "env":
            result["final"] = json.dumps(sorted(os.environ))
        if mode == "priority":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
            kernel.GetPriorityClass.restype = wintypes.DWORD
            result["final"] = str(kernel.GetPriorityClass(kernel.GetCurrentProcess()))
        send({"jsonrpc": "2.0", "id": message["id"], "result": result})
"""


def connector(*modes):
    """`connect` for a WorkerHost: each start runs the next mode (the last one repeats)."""
    queue = list(modes)

    async def connect():
        mode = queue.pop(0) if len(queue) > 1 else queue[0]
        return await connect_process([sys.executable, "-c", SCRIPT, mode])

    return connect


@pytest.fixture
async def rig(conn, plugins):
    rig = RunRig(conn, plugins)
    yield rig
    await rig.meteo.http.aclose()


@pytest.fixture
async def hosts():
    made = []

    def make(*modes, **kwargs):
        host = WorkerHost(connect=connector(*modes), grace_s=0.3, hello_timeout_s=3.0, **kwargs)
        made.append(host)
        return host

    yield make
    for host in made:  # ends every worker a test left running
        await host.aclose()


SPEC = {"task_id": 1, "system": "S", "user": "U", "tools": [], "limits": {}}


async def run_once(host, rig):
    spec = {**SPEC, "task_id": rig.task_id}
    return await host.run(rig.run, spec)


async def test_a_worker_is_started_for_the_first_task_and_kept_for_the_next(hosts, rig):
    host = hosts("done")
    assert host.running is False
    first = await run_once(host, rig)
    assert first["status"] == "done" and host.running is True
    pid = host.pid
    await run_once(host, rig)
    assert host.pid == pid  # the same process
    await host.aclose()
    assert host.running is False


async def test_the_worker_has_a_lower_priority_and_no_window(hosts, rig):
    if sys.platform != "win32":
        pytest.skip("Windows priority classes")
    host = hosts("priority")
    reply = await run_once(host, rig)
    assert int(reply["final"]) == 0x4000  # BELOW_NORMAL_PRIORITY_CLASS
    await host.aclose()


async def test_the_worker_has_no_key_in_its_environment(hosts, rig, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-secret")
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "t")
    monkeypatch.setenv("MY_PASSWORD", "p")
    monkeypatch.setenv("HARMLESS_SETTING", "fine")
    host = hosts("env")
    names = (await run_once(host, rig))["final"]
    await host.aclose()
    assert "HARMLESS_SETTING" in names and "RAPHAEL_WORKER" in names
    for secret in ("OPENROUTER_API_KEY", "SOME_SERVICE_TOKEN", "MY_PASSWORD"):
        assert secret not in names


def test_the_environment_of_the_worker_leaves_out_what_looks_like_a_key():
    env = worker_environment(
        {
            "PATH": "p",
            "SystemRoot": "r",
            "OPENROUTER_API_KEY": "k",
            "github_token": "t",
            "AWS_SECRET_ACCESS_KEY": "s",
            "DB_PASSWORD": "d",
            "GOOGLE_APPLICATION_CREDENTIALS": "c",
            "HTTP_AUTHORIZATION": "a",
        }
    )
    assert set(env) == {"PATH", "SystemRoot", "PYTHONIOENCODING", "RAPHAEL_WORKER"}


def test_the_worker_is_this_executable_with_the_worker_flag(monkeypatch):
    assert worker_command() == [sys.executable, "-m", "raphael_core", "--worker"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert worker_command() == [sys.executable, "--worker"]  # the frozen executable runs itself


async def test_a_worker_that_dies_with_a_task_is_a_crash_and_the_task_is_not_started_again(hosts, rig):
    host = hosts("crash", "done")
    with pytest.raises(WorkerCrashed):
        await run_once(host, rig)
    assert not host.disabled
    # The next task finds a new worker; the one that died took its task with it.
    assert (await run_once(host, rig))["status"] == "done"
    await host.aclose()


async def test_three_crashes_in_ten_minutes_switch_the_worker_off_until_restart(hosts, rig):
    now = [0.0]
    host = hosts("crash", clock=lambda: now[0])
    for _ in range(3):
        with pytest.raises(WorkerCrashed):
            await run_once(host, rig)
        now[0] += 60
    assert host.disabled is True
    with pytest.raises(WorkerUnavailable):
        await run_once(host, rig)


async def test_crashes_far_apart_do_not_add_up(hosts, rig):
    now = [0.0]
    host = hosts("crash", clock=lambda: now[0])
    for _ in range(6):
        with pytest.raises(WorkerCrashed):
            await run_once(host, rig)
        now[0] += 400  # two crashes within the window at most
    assert host.disabled is False


async def test_a_worker_that_does_not_stop_when_told_to_is_ended_after_the_grace(hosts, rig):
    host = hosts("hang", "done")
    waiting = asyncio.ensure_future(run_once(host, rig))
    await until(lambda: host.running)
    await asyncio.sleep(0.2)
    host.cancel(rig.run)
    with pytest.raises(WorkerStopped):
        await asyncio.wait_for(waiting, 10)
    assert not host.disabled  # ending a worker that was told to stop is no crash
    assert (await run_once(host, rig))["status"] == "done"  # and a new one takes the next task
    await host.aclose()


async def test_a_task_that_ran_out_of_time_is_ended_with_its_worker(hosts, rig):
    host = hosts("hang")
    waiting = asyncio.ensure_future(run_once(host, rig))
    await until(lambda: host.running)
    await asyncio.sleep(0.2)
    rig.run.timed_out = True
    host.kill()
    with pytest.raises(WorkerStopped):
        await asyncio.wait_for(waiting, 10)
    await host.aclose()


def test_a_worker_is_given_a_minute_to_say_hello():
    # It starts below normal priority: on a busy machine that takes a while.
    assert HELLO_TIMEOUT_S == 60.0
    assert WorkerHost().start_s == HELLO_TIMEOUT_S
    assert WorkerHost(hello_timeout_s=3.0).start_s == 3.0


async def test_a_worker_that_never_says_hello_is_unavailable_and_counts_as_a_crash(hosts, rig):
    host = WorkerHost(connect=connector("never_hello"), hello_timeout_s=0.3, grace_s=0.3)
    for _ in range(3):
        assert not host.disabled
        with pytest.raises(WorkerUnavailable):
            await run_once(host, rig)
    assert host.disabled  # it will not be started for a fourth time
    await host.aclose()


class Starts:
    """`connect` for a WorkerHost: real worker processes of one mode, kept so that a test can look
    at them. With `gate`, a start waits for it after the process is there, before it is handed
    over: a start that is still under way."""

    def __init__(self, mode, gate=None):
        self.mode = mode
        self.gate = gate
        self.links = []

    async def __call__(self):
        link = await connect_process([sys.executable, "-c", SCRIPT, self.mode])
        self.links.append(link)
        if self.gate is not None:
            await self.gate.wait()
        return link

    async def ended(self, timeout=5.0):
        """Whether every worker that was started has ended within `timeout` seconds."""
        try:
            await asyncio.wait_for(
                asyncio.gather(*(link.process.wait() for link in self.links)), timeout
            )
        except TimeoutError:
            return False
        return True


async def test_a_start_that_is_cancelled_during_the_hello_ends_the_worker_at_once(rig):
    # The core closes, or the task's time is up, while the worker is still saying hello (it may
    # take a minute): the worker is ended there and then, not left behind until the core exits.
    starts = Starts("never_hello")
    host = WorkerHost(connect=starts, hello_timeout_s=60.0, grace_s=0.3)
    running = asyncio.ensure_future(run_once(host, rig))
    await until(lambda: starts.links)
    await asyncio.sleep(0.2)  # in the hello
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert await starts.ended()
    assert not host.running and host._crashes == []  # being cancelled is no crash
    await host.aclose()


async def test_a_task_the_user_stops_during_the_hello_ends_the_starting_worker(rig):
    starts = Starts("never_hello")
    host = WorkerHost(connect=starts, hello_timeout_s=60.0, grace_s=0.3)
    running = asyncio.ensure_future(run_once(host, rig))
    await until(lambda: starts.links)
    await asyncio.sleep(0.2)
    host.cancel(rig.run)
    with pytest.raises(WorkerStopped):  # stopped, as a task that was cancelled is
        await asyncio.wait_for(running, 5)
    assert await starts.ended()
    assert host._crashes == [] and not host.disabled
    await host.aclose()


async def test_a_task_stopped_while_its_worker_is_being_started_ends_that_worker(rig):
    gate = asyncio.Event()
    starts = Starts("done", gate=gate)
    host = WorkerHost(connect=starts, hello_timeout_s=60.0, grace_s=0.3)
    running = asyncio.ensure_future(run_once(host, rig))
    await until(lambda: starts.links)  # the process is there; the start is not over
    host.cancel(rig.run)
    gate.set()
    with pytest.raises(WorkerStopped):
        await asyncio.wait_for(running, 5)
    assert await starts.ended()
    assert host._crashes == [] and not host.running
    await host.aclose()


async def test_closing_the_host_during_the_hello_ends_the_starting_worker(rig):
    starts = Starts("never_hello")
    host = WorkerHost(connect=starts, hello_timeout_s=60.0, grace_s=0.3)
    running = asyncio.ensure_future(run_once(host, rig))
    await until(lambda: starts.links)
    await asyncio.sleep(0.2)
    await asyncio.wait_for(host.aclose(), 10)
    with pytest.raises(WorkerStopped):
        await asyncio.wait_for(running, 5)
    assert await starts.ended(timeout=0.5)  # aclose returned once it was over
    assert not host.running


async def test_a_worker_that_cannot_be_started_is_unavailable_and_counts_as_a_crash(rig):
    async def connect():
        return await connect_process(["this-program-does-not-exist-anywhere"])

    host = WorkerHost(connect=connect)
    for _ in range(3):
        with pytest.raises(WorkerUnavailable):
            await run_once(host, rig)
    assert host.disabled


async def test_an_idle_worker_ends_by_itself_and_the_next_task_starts_another(hosts, rig):
    host = hosts("done", idle_s=0.3)
    await run_once(host, rig)
    first = host.pid
    assert host.running
    await until(lambda: not host.running, timeout=5)
    await run_once(host, rig)
    assert host.running and host.pid != first
    await host.aclose()


async def test_a_worker_with_a_task_is_not_ended_for_idling(hosts, rig):
    host = hosts("hang", idle_s=0.2)
    waiting = asyncio.ensure_future(run_once(host, rig))
    await until(lambda: host.running)
    await asyncio.sleep(0.6)
    assert host.running  # a task is under way: that is not idle
    host.kill()
    with pytest.raises(WorkerCrashed):
        await asyncio.wait_for(waiting, 10)
    await host.aclose()


async def test_the_log_lines_of_the_worker_are_passed_on_but_not_its_tracebacks(hosts, rig, caplog):
    host = hosts("noisy")
    with caplog.at_level(logging.INFO, logger="raphael_core.background.host"):
        await run_once(host, rig)
        await host.aclose()
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "a line that is passed on" in logged
    assert "PRIVATE-TRACE" not in logged and "somebody.else" not in logged


async def test_closing_the_host_ends_the_worker_and_is_safe_twice(hosts, rig):
    host = hosts("done")
    await run_once(host, rig)
    await host.aclose()
    await host.aclose()
    assert host.running is False
    with pytest.raises(WorkerUnavailable):
        # a closed host starts nothing new... and the one that ended is no worker
        host._disabled = True
        await run_once(host, rig)


@pytest.mark.skipif(os.name != "nt", reason="the real worker's executable is started the Windows way")
async def test_the_real_worker_process_starts_and_answers_a_task(rig):
    host = WorkerHost()
    rig.client.replies = []
    # A task whose first model call the TaskRun refuses (the task is already cancelled): the real
    # worker is started, says hello, is asked for the task and ends it as cancelled.
    rig.run.cancel()
    spec = {**SPEC, "task_id": rig.task_id, "system": "S", "user": "U"}
    reply = await host.run(rig.run, spec)
    assert (reply["status"], reply["kind"]) == ("cancelled", "cancelled")
    await host.aclose()


# --- the module, the pipes, the environment -------------------------------------------------------


def test_the_host_module_defines_each_name_once():
    import ast
    from pathlib import Path

    import raphael_core.background.host as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    assert sorted({n for n in names if names.count(n) > 1}) == []


async def test_after_a_crash_our_end_of_its_stdin_is_closed_and_the_process_is_reaped(hosts, rig):
    host = hosts("crash")
    with pytest.raises(WorkerCrashed):
        await run_once(host, rig)
    link = host._link
    await until(lambda: link.process.stdin.is_closing(), timeout=3)
    await asyncio.wait_for(link.process.wait(), 3)
    assert link.process.returncode is not None


async def test_a_log_line_longer_than_the_limit_is_dropped_and_the_pipe_keeps_draining(
    hosts, rig, monkeypatch, caplog
):
    import raphael_core.background.host as module

    monkeypatch.setattr(module, "LINE_LIMIT", 1024)
    host = hosts("bigerr")
    with caplog.at_level(logging.INFO, logger="raphael_core.background.host"):
        reply = await asyncio.wait_for(run_once(host, rig), 15)  # a stalled pipe would hang here
        await host.aclose()
    assert reply["status"] == "done"
    assert "after the big line" in " ".join(r.getMessage() for r in caplog.records)
    assert "xxxx" not in " ".join(r.getMessage() for r in caplog.records)


def test_the_environment_of_the_worker_leaves_out_more_than_keys_and_keeps_what_it_needs():
    env = worker_environment(
        {
            "PATH": "p",
            "SystemRoot": "r",
            "TEMP": "t",
            "PWD": "/where/i/am",
            "SESSIONNAME": "Console",
            "DB_PASS": "x",
            "MYSQL_PWD": "x",
            "DB_PWD_FILE": "x",
            "SERVICE_CREDENTIALS": "x",
            "LOGIN_COOKIE": "x",
            "ASP_SESSION_ID": "x",
            "HTTP_PROXY": "http://user:pw@proxy:8080",
            "https_proxy": "http://user:pw@proxy:8080",
            "ALL_PROXY": "socks5://u:p@proxy:1080",
            "NO_PROXY": "localhost",
        }
    )
    assert set(env) == {"PATH", "SystemRoot", "TEMP", "PWD", "NO_PROXY", "PYTHONIOENCODING", "RAPHAEL_WORKER"}


def test_a_proxy_without_a_password_is_kept():
    env = worker_environment({"HTTP_PROXY": "http://proxy:8080", "HTTPS_PROXY": "http://proxy.example/"})
    assert env["HTTP_PROXY"] == "http://proxy:8080" and "HTTPS_PROXY" in env
