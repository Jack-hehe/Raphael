"""The switches of the capabilities in `services.status`: each capability says whether it is on, and
the status follows the settings, including a database made before the switches existed."""

import json

from .test_hub import Rig, rig  # noqa: F401

CAPABILITIES = {
    "search": "web_search_enabled",
    "read": "web_read_enabled",
    "pdf": "pdf_enabled",
    "vision": "vision_enabled",
    "voice": "tts_enabled",
}


async def test_the_status_says_which_capabilities_are_on(rig):
    status = await rig.call("services.status")
    assert {name: status[name]["enabled"] for name in CAPABILITIES} == {
        "search": True, "read": True, "pdf": True, "vision": True, "voice": False,
    }  # fmt: skip


async def test_the_status_follows_each_switch(rig):
    for name, key in CAPABILITIES.items():
        before = (await rig.call("services.status"))[name]["enabled"]
        rig.settings.set(key, not before)
        assert (await rig.call("services.status"))[name]["enabled"] is (not before)
        assert rig.hub.enabled(name) is (not before)


async def test_the_voice_has_one_provider_and_no_none(rig):
    voice = (await rig.call("services.status"))["voice"]
    assert [option["id"] for option in voice["options"]] == ["fish"]
    assert voice["service"] == "fish"


async def test_a_database_made_before_the_switches_keeps_its_voice(conn):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('tts_service', ?), ('tts_fish_voice', ?)",
        (json.dumps("fish"), json.dumps("0123456789abcdef0123456789abcdef")),
    )
    assert Rig(conn).hub.enabled("voice") is True
    conn.execute("UPDATE settings SET value = ? WHERE key = 'tts_service'", (json.dumps("none"),))
    hub = Rig(conn).hub
    assert hub.enabled("voice") is False and hub.tts_service() == "fish"
