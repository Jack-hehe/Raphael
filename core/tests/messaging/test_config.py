"""messaging.json: version 2, and the move from version 1."""

import json

from raphael_core.messaging.config import AllowedUser, ChannelConfig, MessagingConfig, MessagingStore

V1 = {
    "version": 1,
    "telegram": {
        "enabled": True,
        "bot": {"id": 999, "username": "raph_bot"},
        "allowed": [{"user_id": 111, "name": "Ann"}, {"user_id": 222, "name": ""}],
    },
}


def write(tmp_path, document):
    (tmp_path / "messaging.json").write_text(json.dumps(document), encoding="utf-8")


def test_nothing_is_connected_without_a_file(tmp_path):
    config = MessagingStore(tmp_path).load()
    assert config.channel("telegram").connected is False


def test_version_1_is_read_as_telegram_with_string_ids(tmp_path):
    write(tmp_path, V1)
    telegram = MessagingStore(tmp_path).load().channel("telegram")
    assert (telegram.enabled, telegram.bot_id, telegram.bot_username) == (True, "999", "raph_bot")
    assert [(u.user_id, u.name, u.chat) for u in telegram.allowed] == [("111", "Ann", "111"), ("222", "", "222")]
    assert telegram.is_allowed("111") and not telegram.is_allowed("333")
    assert telegram.chat_allowed("222")


def test_version_1_is_written_as_version_2_the_next_time_something_changes(tmp_path):
    write(tmp_path, V1)
    store = MessagingStore(tmp_path)
    store.save(store.load())
    saved = json.loads((tmp_path / "messaging.json").read_text(encoding="utf-8"))
    assert saved == {
        "version": 2,
        "channels": {
            "telegram": {
                "enabled": True,
                "identity": {"id": "999", "name": "raph_bot"},
                "allowed": [
                    {"user": "111", "name": "Ann", "chat": "111"},
                    {"user": "222", "name": "", "chat": "222"},
                ],
            }
        },
    }
    assert store.load().channel("telegram").bot_id == "999"


def test_ids_larger_than_a_javascript_number_survive_as_text(tmp_path):
    big = "1234567890123456789"
    store = MessagingStore(tmp_path)
    store.save(
        MessagingConfig(
            {"discord": ChannelConfig(True, "9007199254740993", "bot", [AllowedUser(big, "Ann", "42")])}
        )
    )
    loaded = store.load().channel("discord")
    assert loaded.bot_id == "9007199254740993"
    assert (loaded.allowed[0].user_id, loaded.allowed[0].chat) == (big, "42")


def test_a_damaged_file_reads_as_nothing_connected(tmp_path):
    (tmp_path / "messaging.json").write_text("{not json", encoding="utf-8")
    assert MessagingStore(tmp_path).load().channel("telegram").connected is False
    write(tmp_path, {"version": 2, "channels": "nonsense"})
    assert MessagingStore(tmp_path).load().channel("telegram").connected is False


def test_a_channel_that_is_switched_on_without_an_identity_stays_off(tmp_path):
    write(tmp_path, {"version": 2, "channels": {"telegram": {"enabled": True, "identity": None}}})
    assert MessagingStore(tmp_path).load().channel("telegram").enabled is False
