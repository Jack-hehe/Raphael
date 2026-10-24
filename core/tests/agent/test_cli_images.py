"""Native image inputs cross the subprocess boundary without exposing source paths."""

import base64
import sys

import pytest

from raphael_core.agent.cli.drivers import Driver
from raphael_core.services.attachments import checked

PNG = b"\x89PNG\r\n\x1a\nprivate-image"


@pytest.mark.parametrize("name", ["codex", "claude"])
async def test_native_images_are_supplied_in_provider_format_and_cleaned(tmp_path, name):
    script = tmp_path / "images.py"
    script.write_text('''import json, sys
from pathlib import Path
def send(value): print(json.dumps(value), flush=True)
name, record, encoded = sys.argv[1:4]
if name == "claude":
    content = json.loads(sys.stdin.readline())["message"]["content"]
    assert content == [{"type":"text", "text":"Inspect this"},
        {"type":"image", "source":{"type":"base64", "media_type":"image/png", "data":encoded}}]
    send({"type":"result", "subtype":"success", "result":"Seen"})
else:
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize": send({"id":msg["id"], "result":{}})
        elif msg.get("method") == "thread/start": send({"id":msg["id"], "result":{"thread":{"id":"one"}}})
        elif msg.get("method") == "turn/start":
            content = msg["params"]["input"]
            assert content[0] == {"type":"text", "text":"Inspect this"}
            assert len(content) == 2 and content[1]["type"] == "localImage"
            path = Path(content[1]["path"])
            import base64
            assert path.is_absolute() and path.read_bytes() == base64.b64decode(encoded)
            assert path.name != "outside.png"
            Path(record).write_text(str(path), encoding="utf-8")
            send({"method":"item/completed", "params":{"item":{"type":"agentMessage", "id":"answer", "phase":"final_answer", "text":"Seen"}}})
            send({"method":"turn/completed", "params":{"turn":{"status":"completed"}}})
            break
''', encoding="utf-8")
    record = tmp_path / "path.txt"
    driver = Driver(name, [sys.executable, str(script), name, str(record), base64.b64encode(PNG).decode()])
    events = [event async for event in driver.run(
        user="Inspect this", system="Persona", endpoint="http://127.0.0.1:1/mcp", token="ephemeral",
        permission=None, images=[checked("../../outside.png", "image/png", PNG)],
    )]
    assert events[-1].kind == "final" and events[-1].text == "Seen"
    if name == "codex":
        from pathlib import Path
        assert not Path(record.read_text(encoding="utf-8")).exists()
