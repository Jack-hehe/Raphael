"""Room messages carry readable attachment text through the real background worker."""

import base64

import pytest

from raphael_core.members.rooms import RoomsService
from raphael_core.members.service import MembersService
from .conftest import report, until


@pytest.mark.parametrize("member_id", [0, 1])
async def test_room_html_reaches_the_speaker_without_a_tool_call(make_rig, conn, tmp_path, member_id):
    rig = make_rig()
    members = MembersService(conn, tmp_path, background=rig.service)
    assert members.store.create_member(name="Reader", role="Researcher").id == 1
    rig.service.use_team(members, None)
    rooms = RoomsService(conn, members=members, background=rig.service)
    rooms.register(rig.rpc)
    room = (await rig.rpc.call("rooms.create", {"name": "Reading", "member_ids": [member_id]}))["room"]
    rig.client.replies = [report("Article received")]
    sent = await rig.rpc.call("rooms.send", {"room_id": room["id"], "request_id": "article",
        "text": "Explain this article", "ui_language": "en", "attachments": [{"name": "article.html",
        "mime": "text/html", "data": base64.b64encode(b"<h1>Room article</h1><p>Readable article body.</p><script>SECRET_SCRIPT</script>").decode()}]})
    await until(lambda: bool(rig.client.chat_calls))
    await until(lambda: not rig.service._executing)
    prompt = rig.client.chat_calls[0]["messages"][1]["content"]
    assert "Readable article body." in prompt and "SECRET_SCRIPT" not in prompt
    assert '<tool_result untrusted="true"' in prompt
    messages = (await rig.rpc.call("rooms.messages", {"room_id": room["id"]}))["messages"]
    assert sent["message_id"] == messages[0]["id"]
    assert messages[1]["member_id"] == member_id and messages[1]["text"] == "Article received"
    assert rig.task(1).tool_calls == 0
