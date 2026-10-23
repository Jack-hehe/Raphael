"""The owner's switches of the capabilities (Settings › 拉斐爾 › 服務) in a background task: what is off
is not offered to the task and is refused if the task asks for it anyway, with nothing sent to any
service. Fakes for every service: no test reaches the network."""

from raphael_core.background.snapshot import take_snapshot
from raphael_core.services.attachments import checked

from .test_web_tools import PNG, web_rig  # noqa: F401


def names(snapshot):
    return [d["function"]["name"] for d in snapshot.definitions]


async def test_a_search_and_a_page_read_that_are_off_are_refused_to_the_task_and_not_made(web_rig):
    rig = web_rig()
    rig.settings.set("web_search_service", "brave")
    rig.keys.set("brave", "k")
    rig.settings.set("web_search_enabled", False)
    rig.settings.set("web_read_enabled", False)
    search = await rig.tool("web_search", {"query": "something"})
    read = await rig.tool("web_read", {"url": "https://docs.example/guide"})
    assert (search["ok"], search["kind"]) == (False, "disabled")
    assert (read["ok"], read["kind"]) == (False, "disabled")
    assert rig.web.requests == [] and rig.client.text_calls == []


async def test_a_file_of_a_kind_that_is_off_is_refused_to_the_task_and_not_sent_anywhere(web_rig):
    image = checked("photo.png", "image/png", PNG)
    rig = web_rig([image])
    rig.settings.set("vision_enabled", False)
    reply = await rig.tool("read_attachment", {"index": 1})
    assert (reply["ok"], reply["kind"]) == (False, "disabled")
    assert rig.client.text_calls == []


async def test_what_the_owner_turned_off_is_not_offered_to_a_task_at_all(web_rig):
    rig = web_rig()
    rig.settings.set("web_search_enabled", False)
    rig.settings.set("web_read_enabled", False)
    off = rig.run._tools.turned_off()
    assert off == {"web_search", "web_read"}
    shown = names(take_snapshot(None, attachments=True, turned_off=off))
    assert "web_search" not in shown and "web_read" not in shown
    assert {"weather", "local_time", "read_attachment"} <= set(shown)
    rig.settings.set("vision_enabled", False)
    rig.settings.set("pdf_enabled", False)
    off = rig.run._tools.turned_off()
    assert "read_attachment" not in names(take_snapshot(None, attachments=True, turned_off=off))


def test_the_snapshot_leaves_out_what_it_is_told_is_off_whoever_the_speaker():
    snapshot = take_snapshot(None, attachments=True, turned_off={"web_search", "read_attachment"})
    assert names(snapshot) == ["weather", "local_time", "web_read"]
