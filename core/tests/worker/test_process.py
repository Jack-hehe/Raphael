"""The worker as a real process: `python -m raphael_core --worker`, spoken to on its stdin and stdout."""

import json
import subprocess
import sys
import time


def start():
    return subprocess.Popen(
        [sys.executable, "-m", "raphael_core", "--worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def send(process, message):
    process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
    process.stdin.flush()


def hello(request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": "worker.hello", "params": {}}


def finish(process, timeout=10):
    """Close stdin, wait for the exit, and give back (exit code, what was left of stdout, stderr)."""
    process.stdin.close()
    code = process.wait(timeout=timeout)
    out, err = process.stdout.read(), process.stderr.read()
    process.stdout.close()
    process.stderr.close()
    return code, out, err.decode("utf-8", errors="replace")


def test_the_worker_answers_hello_and_ends_by_itself_when_its_stdin_closes():
    process = start()
    began = time.monotonic()
    send(process, hello())
    reply = json.loads(process.stdout.readline())
    assert reply["id"] == 1 and reply["result"]["protocol"] == 1
    assert isinstance(reply["result"]["pid"], int)  # (a venv's python.exe is a stub: not Popen's pid)
    code, rest, err = finish(process)
    assert code == 0
    assert rest == b""  # stdout carries the protocol and nothing else
    assert "Traceback" not in err
    assert time.monotonic() - began < 30


def test_a_worker_whose_core_is_gone_in_the_middle_of_a_task_ends_too():
    process = start()
    send(process, hello())
    process.stdout.readline()
    task = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "task.run",
        "params": {
            "task_id": 1,
            "system": "S",
            "user": "U",
            "tools": [{"type": "function", "function": {"name": "t", "parameters": {}}}],
        },
    }
    send(process, task)
    request = json.loads(process.stdout.readline())  # it asks the core for a model reply...
    assert request["method"] == "host.chat" and request["params"]["task_id"] == 1
    code, _, err = finish(process)  # ...and the core goes away without answering
    assert code == 0 and "Traceback" not in err


def test_the_logs_of_the_worker_hold_nothing_of_the_task():
    process = start()
    send(process, hello())
    process.stdout.readline()
    send(
        process,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "task.run",
            "params": {"task_id": 1, "system": "PRIVATE-SYSTEM", "user": "PRIVATE-USER", "tools": []},
        },
    )
    request = json.loads(process.stdout.readline())
    send(
        process,
        {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"content": json.dumps({"prefix": "告", "text": "PRIVATE-REPORT"}), "calls": []},
        },
    )
    result = json.loads(process.stdout.readline())
    assert "PRIVATE-REPORT" in result["result"]["final"]
    code, _, err = finish(process)
    assert code == 0 and err.strip()  # it does log...
    assert "PRIVATE" not in err  # ...but not what it was given or said
