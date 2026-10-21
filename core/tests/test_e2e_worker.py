"""End to end: a task for the background, in the core as the Tauri app runs it.

The core is a child process with the fake LLM (`[delegate]` at the start of a request sends it to the
worker, and the fake model then plays a fixed script), a data directory of its own and a keyring that
fails on every use. Its worker is a second child, started by the core for the task: the real
`python -m raphael_core --worker`. The plugin is the fake MCP server of the plugins' tests as a real
stdio program. No key, no network, no cost.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_e2e import Core

FAKE_SERVER = Path(__file__).parent / "integrations" / "fake_server.py"
PID = re.compile(rb"The worker is running \(pid (\d+)\)")


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


def result(core, method, params=None, **kwargs):
    reply = core.call(method, params, **kwargs)
    assert "error" not in reply, (method, reply, core.log())
    return reply["result"]


def connect_the_fake_server(core, **tiers):
    added = result(
        core,
        "integrations.add",
        {"name": "Notes", "transport": "stdio", "command": sys.executable, "args": [str(FAKE_SERVER)]},
    )["server"]
    refreshed = result(core, "integrations.refresh", {"id": added["id"]}, timeout=60)
    assert refreshed["server"]["status"] == "ready", refreshed
    for tool, tier in tiers.items():
        result(core, "integrations.set_tier", {"id": added["id"], "tool": tool, "tier": tier})
    return added["id"]


def ask(core, text, language="en"):
    return result(
        core,
        "agent.ask",
        {"session_id": "e2e", "text": text, "ui_language": language, "target_app": None},
    )


def wait_for(core, predicate, timeout=60.0):
    """Keep the conversation going (so that notifications are read) until `predicate()`."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, f"timed out\n{core.log()}"
        core.call("core.hello")
        time.sleep(0.1)


def reports(core):
    return [n["params"] for n in core.notifications if n["method"] == "worker.report"]


def worker_pids(core):
    return [int(pid) for pid in PID.findall(bytes(core.stderr))]


def is_alive(pid):
    shown = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
    return str(pid) in shown.stdout


def wait_gone(pid, timeout=20.0):
    deadline = time.monotonic() + timeout
    while is_alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


def test_a_task_is_handed_over_carried_out_by_the_worker_and_reported(spawn):
    core = spawn()
    server = connect_the_fake_server(core, look="T0")
    handed = ask(core, "[delegate] look a couple of things up for me")
    assert (handed["status"], handed["prefix"], handed["text"]) == (
        "done", "Understood", "Handed to the background.",
    )  # fmt: skip
    wait_for(core, lambda: reports(core))
    [report] = reports(core)
    assert report["state"] == "done" and report["prefix"] == "Notice"
    assert report["text"] == "The scripted task saw 2 results."

    task = result(core, "worker.get", {"id": report["task_id"]})["task"]
    assert [(s["kind"], s["tool"], s["server"], s["tier"], s["outcome"]) for s in task["steps"]] == [
        ("model", None, None, None, "ok"),
        ("tool", "look", "Notes", "T0", "ok"),
        ("model", None, None, None, "ok"),
        ("tool", "look", "Notes", "T0", "ok"),
        ("model", None, None, None, "ok"),
    ]
    assert task["cost_usd"] == 0 and task["tool_calls"] == 2
    # Two audit rows, both the worker's, none with an argument or a result.
    audit = result(core, "integrations.audit", {})["entries"]
    assert [(e["tool"], e["tier"], e["approver"], e["outcome"], e["task_id"]) for e in audit] == [
        ("look", "T0", "auto", "ok", report["task_id"]),
        ("look", "T0", "auto", "ok", report["task_id"]),
    ]
    assert "alpha" not in json.dumps(audit) and "found" not in json.dumps(task)
    # The conversation shows the report where the handoff was; nothing was spent.
    [turn] = result(core, "agent.history.list")["turns"]
    assert (turn["route"], turn["prefix"], turn["text"]) == ("delegate", "Notice", report["text"])
    assert result(core, "status.get")["spend"]["month_usd"] == 0
    changes = [n["params"] for n in core.notifications if n["method"] == "worker.changed"]
    assert [c["running"] for c in changes][0] is False and any(c["running"] for c in changes)
    assert changes[-1]["running"] is False and changes[-1]["state"] == "done"

    # The worker was a process of its own, and it goes when the core does.
    [pid] = worker_pids(core)
    assert pid != core.proc.pid and is_alive(pid)
    assert server
    assert core.close(timeout=30) == 0
    assert wait_gone(pid), "the worker outlived the core"
    assert b"Traceback" not in bytes(core.stderr), core.log()


def test_the_fast_tier_goes_on_while_a_task_runs(spawn):
    core = spawn()
    connect_the_fake_server(core, slow="T0")
    ask(core, "[delegate] this one is slow")
    wait_for(core, lambda: result(core, "worker.list")["tasks"][0]["state"] == "running")
    started = time.monotonic()
    answer = ask(core, "hello there")  # small talk, while the worker is under way
    assert (answer["prefix"], answer["text"]) == ("Answer", "hello there")
    assert time.monotonic() - started < 10
    result(core, "worker.cancel", {})
    wait_for(core, lambda: reports(core))
    assert core.close(timeout=30) == 0


def test_a_task_the_user_stops_is_reported_as_stopped_and_the_worker_stays_for_the_next(spawn):
    core = spawn()
    connect_the_fake_server(core, look="T0", slow="T0")
    ask(core, "[delegate] this one is slow")
    wait_for(core, lambda: result(core, "worker.list")["tasks"][0]["state"] == "running")
    time.sleep(2.0)  # the slow tool is under way
    assert result(core, "worker.cancel", {}) == {"cancelled": True}
    wait_for(core, lambda: reports(core))
    [stopped] = reports(core)
    assert stopped["state"] == "cancelled"
    assert stopped["text"] == (
        "Task stopped after 1 tool calls. One was cut off; whether it ran is unknown."
    )
    # The next task is carried out as usual.
    ask(core, "[delegate] now look something up")
    wait_for(core, lambda: len(reports(core)) == 2)
    assert reports(core)[1]["state"] == "done"
    assert core.close(timeout=30) == 0


def test_a_worker_that_is_killed_interrupts_its_task_and_the_core_goes_on(spawn):
    core = spawn()
    connect_the_fake_server(core, slow="T0")
    ask(core, "[delegate] this one is slow")
    wait_for(core, lambda: result(core, "worker.list")["tasks"][0]["state"] == "running")
    time.sleep(1.0)
    [pid] = worker_pids(core)
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=True)
    wait_for(core, lambda: reports(core))
    [interrupted] = reports(core)
    assert interrupted["state"] == "interrupted" and interrupted["prefix"] == "Notice"
    assert interrupted["text"].startswith("The task was interrupted.")
    time.sleep(1.0)
    assert len(reports(core)) == 1 and len(worker_pids(core)) == 1  # not started again
    assert ask(core, "hello again")["text"] == "hello again"  # the assistant is fine
    assert core.close(timeout=30) == 0


def test_a_worker_whose_core_dies_ends_by_itself(spawn):
    core = spawn()
    connect_the_fake_server(core, slow="T0")
    ask(core, "[delegate] this one is slow")
    wait_for(core, lambda: result(core, "worker.list")["tasks"][0]["state"] == "running")
    time.sleep(1.0)
    [pid] = worker_pids(core)
    core.kill()  # the core is gone, as if the app crashed (nothing closes its stdin politely)
    assert wait_gone(pid), "the worker outlived its core"


def test_the_worker_is_not_given_the_core_s_secrets(spawn):
    # The fake LLM's key is in the core's memory only; a worker's environment holds none of it. (The
    # worker's modules import no key store: tests/worker/test_isolation.py.)
    core = spawn(OPENROUTER_API_KEY="sk-or-v1-in-the-environment")
    ask(core, "[delegate] what time is it")
    wait_for(core, lambda: reports(core))
    assert reports(core)[0]["state"] == "done"
    assert b"sk-or-v1" not in bytes(core.stderr) and b"sk-or-v1" not in b"".join(core.raw_stdout)
    assert core.close(timeout=30) == 0
