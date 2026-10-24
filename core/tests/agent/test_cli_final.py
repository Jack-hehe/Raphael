"""Progress is public while a CLI works, but is never a missing final answer."""

import sys

import pytest

from raphael_core.agent.cli.drivers import Driver


@pytest.mark.parametrize("name", ["claude", "codex", "gemini"])
@pytest.mark.parametrize("answer", ["", "Final answer"])
async def test_driver_keeps_progress_out_of_final(tmp_path, name, answer):
    script = tmp_path / "final.py"
    script.write_text('''import json, sys
name, answer = sys.argv[1:3]
def send(value): print(json.dumps(value), flush=True)
def finish():
    if name == "claude":
        send({"type":"assistant", "message":{"content":[{"type":"text", "text":"Checking the image."}]}})
        send({"type":"assistant", "message":{"content":[{"type":"tool_use", "name":"mcp__raphael__read_attachment"}]}})
        send({"type":"result", "subtype":"success", "result":answer})
    elif name == "gemini":
        send({"event":"step_update", "step_update":{"step_type":"agent_response", "text_delta":"Checking the image."}})
        send({"event":"step_update", "step_update":{"step_type":"tool", "state":"ACTIVE", "tool_name":"read_attachment"}})
        send({"event":"result", "result":{"status":"SUCCESS", "response":answer}})
    else:
        send({"method":"item/started", "params":{"item":{"id":"progress", "type":"agentMessage", "phase":"commentary", "text":""}}})
        send({"method":"item/agentMessage/delta", "params":{"itemId":"progress", "delta":"Checking the image."}})
        send({"method":"item/completed", "params":{"item":{"id":"progress", "type":"agentMessage", "phase":"commentary", "text":"Checking the image."}}})
        if answer:
            send({"method":"item/completed", "params":{"item":{"id":"final", "type":"agentMessage", "phase":"final_answer", "text":answer}}})
        send({"method":"turn/completed", "params":{"turn":{"status":"completed"}}})
if name == "codex":
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize": send({"id":msg["id"], "result":{}})
        elif msg.get("method") == "thread/start": send({"id":msg["id"], "result":{"thread":{"id":"one"}}})
        elif msg.get("method") == "turn/start":
            finish()
            break
else:
    json.loads(sys.stdin.readline())
    finish()
''', encoding="utf-8")
    events = [event async for event in Driver(name, [sys.executable, str(script), name, answer]).run(
        user="Inspect", system="Persona", endpoint="http://127.0.0.1:1/mcp", token="ephemeral", permission=None,
    )]
    assert any(event.kind == "text_delta" and event.text == "Checking the image." for event in events)
    assert events[-1].kind == "final" and events[-1].text == answer


@pytest.mark.parametrize("mode,expected", [("delta_only", ""), ("before_tool", ""), ("legacy_final", "Legacy answer")])
async def test_codex_requires_a_completed_answer_after_tools(tmp_path, mode, expected):
    script = tmp_path / "codex.py"
    script.write_text('''import json, sys
mode = sys.argv[1]
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize": send({"id":msg["id"], "result":{}})
    elif msg.get("method") == "thread/start": send({"id":msg["id"], "result":{"thread":{"id":"one"}}})
    elif msg.get("method") == "turn/start":
        send({"method":"item/agentMessage/delta", "params":{"delta":"Unfinished progress"}})
        if mode == "before_tool":
            send({"method":"item/completed", "params":{"item":{"type":"agentMessage", "id":"old", "text":"Unfinished progress"}}})
            send({"method":"item/started", "params":{"item":{"type":"mcpToolCall", "id":"tool", "tool":"read_attachment"}}})
        if mode == "legacy_final":
            send({"method":"item/completed", "params":{"item":{"type":"agentMessage", "id":"final", "phase":None, "text":"Legacy answer"}}})
        send({"method":"turn/completed", "params":{"turn":{"status":"completed"}}})
        break
''', encoding="utf-8")
    events = [event async for event in Driver("codex", [sys.executable, str(script), mode]).run(
        user="Inspect", system="Persona", endpoint="http://127.0.0.1:1/mcp", token="ephemeral", permission=None,
    )]
    assert events[-1].kind == "final" and events[-1].text == expected
