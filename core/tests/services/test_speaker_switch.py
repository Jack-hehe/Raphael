"""The switch of Raphael's voice (`tts_enabled`): off, he says nothing whatever service and voice are
set; a preview is still made, because it is how the voice is tried before it is turned on."""

import asyncio

from .test_speaker import rig  # noqa: F401


async def test_a_voice_that_is_off_says_nothing_whatever_service_and_voice_are_set(rig):
    rig.set_up()
    rig.settings.set("tts_enabled", False)
    await rig.speaker.say("解", "話さない。")
    await asyncio.sleep(0)
    assert rig.requests == [] and rig.player.received == []


async def test_a_voice_that_is_on_speaks(rig):
    rig.set_up()
    await asyncio.wait_for(rig.speaker.say("解", "話す。"), 2)
    for _ in range(50):
        if rig.player.received:
            break
        await asyncio.sleep(0.01)
    assert rig.player.received and len(rig.requests) == 1


async def test_a_preview_is_made_with_the_voice_off_because_it_is_how_it_is_tried(rig):
    rig.set_up()
    rig.settings.set("tts_enabled", False)
    assert await rig.rpc.call("tts.preview", {"text": "試試看"}) == {"ok": True}
    assert rig.texts() == ["告。マスター、ご指示をどうぞ。"]  # the preview line is always this one
