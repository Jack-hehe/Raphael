"""The owner's switches of the capabilities (Settings › 拉斐爾 › 服務) in the fast tier: a capability
that is off is not used at all, whichever service is chosen for it. The search is not made and the
answer says that Raphael cannot search now; pages are not read; an image or a PDF is refused in one
line before any model is asked. Fakes for every service: no test reaches the network."""

import pytest

from raphael_core.agent.persona import PHRASES
from raphael_core.agent.tools import Tools

from .conftest import decision
from .test_service import found, make_rig, spoken  # noqa: F401
from .test_services_turns import PNG, ask, services_rig, steps, web, wire  # noqa: F401


async def test_a_search_that_is_off_is_not_made_and_the_answer_is_told_it_cannot_search(
    services_rig, client, web
):
    rig = services_rig
    rig.settings.set("web_search_service", "brave")
    rig.service_keys.set("brave", "brave-key")
    rig.settings.set("web_search_enabled", False)
    client.decisions.append(decision("answer", search=0.9))
    client.replies.append(spoken("否", "現在無法搜尋。"))
    await rig.ask("明天台北會下雨嗎")
    assert web.requests == [] and client.text_calls == []  # neither Brave nor OpenRouter's search
    user = client.json_calls[0]["user"]
    assert "web_search: turned off by the owner; say briefly that you cannot do this now" in user
    (turn,) = await rig.turns()
    (step,) = steps(turn, "web_search")
    assert (step["outcome"], step["reason"]) == ("error", "blocked")


async def test_the_openrouter_search_is_switched_off_too(services_rig, client):
    services_rig.settings.set("web_search_enabled", False)
    client.decisions.append(decision("answer", search=0.9))
    client.replies.append(spoken("否", "現在無法搜尋。"))
    await services_rig.ask("明天會下雨嗎")
    assert client.text_calls == []  # the default service is OpenRouter's, and it is not asked


async def test_pages_are_not_read_while_reading_them_is_off(services_rig, client, web):
    rig = services_rig
    rig.settings.set("web_read_enabled", False)
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("否", "現在無法讀網頁。"))
    await rig.ask("幫我看 https://page.example/one")
    assert web.requests == []
    assert "web_read: turned off by the owner" in client.json_calls[0]["user"]


async def test_the_other_capabilities_stay_on_when_one_is_off(services_rig, client, web):
    rig = services_rig
    rig.settings.set("web_search_enabled", False)
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "這頁在講天氣。"))
    await rig.ask("幫我看 https://page.example/one")
    assert web.hosts() == ["page.example"]


@pytest.mark.parametrize("language", ["zh-TW", "en", "ja"])
async def test_an_image_is_refused_in_one_line_while_reading_images_is_off(
    services_rig, client, language
):
    rig = services_rig
    rig.settings.set("vision_enabled", False)
    result = await ask(rig, "這是什麼", [wire("a.png", "image/png", PNG)], language)
    assert result["text"] == PHRASES[language].vision_off and result["prefix"] != ""
    assert client.calls == 0  # no model was asked, no image was sent anywhere
    (turn,) = await rig.turns()
    assert turn["text"] == PHRASES[language].vision_off


async def test_a_pdf_is_refused_while_reading_pdfs_is_off_and_an_image_is_still_read(
    services_rig, client
):
    from services.pdfs import make_pdf

    rig = services_rig
    rig.settings.set("pdf_enabled", False)
    pdf = wire("report.pdf", "application/pdf", make_pdf(["Text."]))
    result = await ask(rig, "這份是什麼", [pdf])
    assert result["text"] == PHRASES["zh-TW"].pdf_off and client.calls == 0
    client.decisions.append(decision("chat"))
    client.texts.append(found("A cat."))
    client.replies.append(spoken("答", "一隻貓。"))
    result = await ask(rig, "這是什麼", [wire("cat.png", "image/png", PNG)])
    assert result["text"] == "一隻貓。"


async def test_a_pdf_is_still_read_with_the_images_off_but_its_scans_are_not_looked_at(
    services_rig, client
):
    from services.pdfs import make_pdf

    rig = services_rig
    rig.settings.set("vision_enabled", False)
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "報告說了這些。"))
    pdf = wire("report.pdf", "application/pdf", make_pdf(["The quarterly report text."]))
    result = await ask(rig, "這份是什麼", [pdf])
    assert result["text"] == "報告說了這些。" and client.text_calls == []
    assert "The quarterly report text." in client.json_calls[0]["user"]


async def test_the_tools_name_what_the_owner_turned_off(services_rig):
    tools = services_rig.service._tools
    assert isinstance(tools, Tools) and tools.turned_off() == frozenset()
    services_rig.settings.set("web_search_enabled", False)
    services_rig.settings.set("web_read_enabled", False)
    assert tools.turned_off() == {"web_search", "web_read"}
    services_rig.settings.set("vision_enabled", False)
    assert "read_attachment" not in tools.turned_off()  # PDFs can still be read
    services_rig.settings.set("pdf_enabled", False)
    assert tools.turned_off() == {"web_search", "web_read", "read_attachment"}


def test_without_the_services_nothing_is_turned_off_and_the_search_is_openrouters():
    assert Tools(object()).turned_off() == frozenset()
