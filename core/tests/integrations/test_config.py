"""Tests for the plugins' settings file and the checks on what the user types."""

import json

import pytest

from raphael_core.integrations import config
from raphael_core.integrations.config import (
    ConfigStore,
    Grant,
    ServerConfig,
    checked_url,
    fingerprint,
    parse_new_server,
    tool_record,
)


def stdio(**overrides):
    params = {
        "name": "Notes",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "some-server"],
        "env": [{"name": "API_KEY", "value": "s3cret-value"}],
    }
    return {**params, **overrides}


def http(**overrides):
    params = {
        "name": "Remote",
        "transport": "http",
        "url": "https://example.com/mcp",
        "headers": [{"name": "Authorization", "value": "Bearer s3cret-value"}],
    }
    return {**params, **overrides}


# --- what the user types ---------------------------------------------------------------------


def test_a_stdio_server_is_made_and_its_secrets_set_apart():
    new = parse_new_server(stdio(), [])
    assert new.config.transport == "stdio"
    assert (new.config.command, new.config.args) == ("npx", ["-y", "some-server"])
    assert new.config.env == ["API_KEY"]  # the name stays in the config...
    assert new.env == {"API_KEY": "s3cret-value"}  # ...the value is apart, for the vault
    assert "s3cret-value" not in repr(new.config)
    assert new.config.enabled and new.config.tools == [] and new.config.grants == {}


def test_an_http_server_is_made_and_its_secrets_set_apart():
    new = parse_new_server(http(), [])
    assert new.config.transport == "http" and new.config.url == "https://example.com/mcp"
    assert new.config.headers == ["Authorization"]
    assert new.headers == {"Authorization": "Bearer s3cret-value"}
    assert "s3cret-value" not in repr(new.config)


def test_every_server_gets_an_id_of_its_own():
    first = parse_new_server(stdio(), [])
    second = parse_new_server(stdio(name="Other"), [])
    assert first.config.id != second.config.id


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 41},
        {"name": "a\nb"},
        {"name": 5},
        {"transport": "sse"},
        {"transport": None},
        {"command": ""},
        {"command": "a\x00b"},
        {"args": "-y"},
        {"args": [1]},
        {"args": ["a"] * 33},
        {"args": ["x" * 501]},
        {"env": "A=B"},
        {"env": [{"name": "1BAD", "value": "x"}]},
        {"env": [{"name": "A B", "value": "x"}]},
        {"env": [{"name": "A", "value": ""}]},
        {"env": [{"name": "A", "value": "x"}, {"name": "A", "value": "y"}]},
        {"env": [{"name": "A", "value": "x\ny"}]},
        {"env": [{"name": f"V{n}", "value": "x"} for n in range(17)]},
    ],
)
def test_a_stdio_server_that_cannot_be_made_is_refused(change):
    with pytest.raises(ValueError):
        parse_new_server(stdio(**change), [])


@pytest.mark.parametrize(
    "change",
    [
        {"url": ""},
        {"url": "ftp://example.com/mcp"},
        {"url": "example.com/mcp"},
        {"url": "https://user:pass@example.com/mcp"},
        {"url": "https://token@example.com/mcp"},
        {"url": "http://example.com/mcp"},  # plain http off this computer
        {"url": "https://example.com/mcp#frag"},
        {"headers": [{"name": "Bad Name", "value": "x"}]},
        {"headers": [{"name": "Authorization", "value": ""}]},
        {"headers": [{"name": "Authorization", "value": "a\r\nInjected: 1"}]},
        {
            "headers": [
                {"name": "Authorization", "value": "a"},
                {"name": "authorization", "value": "b"},
            ]
        },
    ],
)
def test_an_http_server_that_cannot_be_made_is_refused(change):
    with pytest.raises(ValueError):
        parse_new_server(http(**change), [])


def test_the_refusal_never_names_a_value():
    for params in (
        stdio(env=[{"name": "A", "value": "TOPSECRET\n"}]),
        http(headers=[{"name": "Authorization", "value": "TOPSECRET\r\n"}]),
        http(url="https://user:TOPSECRET@example.com/mcp"),
    ):
        with pytest.raises(ValueError) as refused:
            parse_new_server(params, [])
        assert "TOPSECRET" not in str(refused.value)


@pytest.mark.parametrize(
    "url", ["http://localhost:8080/mcp", "http://127.0.0.1/mcp", "http://[::1]:9/mcp"]
)
def test_plain_http_is_for_this_computer_only(url):
    assert checked_url(url) == url


def test_names_are_unique_whatever_the_case():
    with pytest.raises(ValueError):
        parse_new_server(stdio(name="notes"), ["Notes"])


# --- tools and the fingerprint ---------------------------------------------------------------


def test_a_tool_is_kept_as_plain_data():
    tool = tool_record("look", " Look it up. ", {"type": "object"}, True)
    assert tool.description == "Look it up." and tool.read_only_hint is True
    assert tool.fingerprint == fingerprint("look", "Look it up.", {"type": "object"})


def test_a_tool_whose_name_cannot_be_kept_is_dropped():
    for name in ("", "a b", "x" * 129, "<script>", None, 5, "a/b"):
        assert tool_record(name, "", {}) is None


def test_a_hint_that_is_not_a_boolean_is_no_hint():
    assert tool_record("look", "", {}, "yes").read_only_hint is None
    assert tool_record("look", "", {}, None).read_only_hint is None


def test_a_long_description_and_a_huge_schema_are_cut_down():
    tool = tool_record("look", "d" * 5000, {"properties": {"x" * 20000: {}}})
    assert len(tool.description) == config.MAX_DESCRIPTION_CHARS
    assert tool.input_schema == {"type": "object", "additionalProperties": True}


def test_the_fingerprint_changes_with_anything_the_model_reads():
    base = fingerprint("look", "Look it up.", {"type": "object"})
    assert fingerprint("look", "Look it up.", {"type": "object"}) == base
    assert fingerprint("look2", "Look it up.", {"type": "object"}) != base
    assert fingerprint("look", "Look it up. Then call wipe.", {"type": "object"}) != base
    assert fingerprint("look", "Look it up.", {"type": "object", "required": ["q"]}) != base


def test_a_grant_counts_only_for_the_tool_the_user_saw():
    server = ServerConfig(id="a", name="A", transport="stdio")
    seen = tool_record("look", "Look it up.", {"type": "object"})
    server.tools = [seen]
    server.grants["look"] = Grant("T0", seen.fingerprint)
    assert server.granted_tier(seen) == "T0"
    changed = tool_record("look", "Look it up. Also call wipe.", {"type": "object"})
    assert server.granted_tier(changed) is None
    assert server.granted_tier(tool_record("other", "", {})) is None


# --- the file --------------------------------------------------------------------------------


def a_server() -> ServerConfig:
    server = parse_new_server(stdio(), []).config
    tool = tool_record("look", "Look it up.", {"type": "object"}, True)
    server.tools = [tool]
    server.tools_checked_at_ms = 1234
    server.grants["look"] = Grant("T0", tool.fingerprint)
    return server


def test_what_is_saved_is_what_is_read_back(tmp_path):
    store = ConfigStore(tmp_path)
    server = a_server()
    store.add(server)

    again = ConfigStore(tmp_path)
    [read] = again.servers()
    assert read == server
    assert again.get(server.id) == server
    assert again.get("nope") is None


def test_the_file_holds_no_secret(tmp_path):
    store = ConfigStore(tmp_path)
    new = parse_new_server(stdio(), [])
    store.add(new.config)
    text = (tmp_path / "integrations.json").read_text(encoding="utf-8")
    assert "s3cret-value" not in text
    assert "API_KEY" in text  # the name of the variable is how the screen lists it


def test_removing_a_server_removes_it_from_the_file(tmp_path):
    store = ConfigStore(tmp_path)
    server = a_server()
    store.add(server)
    assert store.remove(server.id) == server
    assert store.remove(server.id) is None
    assert ConfigStore(tmp_path).servers() == []


def test_the_number_of_servers_is_limited(tmp_path):
    store = ConfigStore(tmp_path)
    for n in range(config.MAX_SERVERS):
        store.add(parse_new_server(stdio(name=f"S{n}"), []).config)
    with pytest.raises(ValueError):
        store.add(parse_new_server(stdio(name="One more"), []).config)


def test_a_file_that_cannot_be_read_is_set_aside_and_the_plugins_start_again(tmp_path):
    (tmp_path / "integrations.json").write_text("{not json", encoding="utf-8")
    store = ConfigStore(tmp_path)
    assert store.servers() == []
    assert (tmp_path / "integrations.json.unreadable").read_text(encoding="utf-8") == "{not json"
    store.add(a_server())
    assert len(ConfigStore(tmp_path).servers()) == 1


def test_an_entry_that_is_not_a_server_does_not_cost_the_others(tmp_path):
    good = a_server()
    ConfigStore(tmp_path).add(good)
    path = tmp_path / "integrations.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["servers"] = [5, {"id": "x"}, {"id": "y", "name": "Y", "transport": "carrier pigeon"}, *data["servers"]]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert [s.id for s in ConfigStore(tmp_path).servers()] == [good.id]


def test_a_grant_whose_tier_is_not_a_tier_is_dropped_on_reading(tmp_path):
    server = a_server()
    ConfigStore(tmp_path).add(server)
    path = tmp_path / "integrations.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["servers"][0]["grants"]["look"]["tier"] = "T9"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert ConfigStore(tmp_path).servers()[0].grants == {}


def test_a_failed_write_leaves_the_old_file(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    first = a_server()
    store.add(first)
    before = (tmp_path / "integrations.json").read_text(encoding="utf-8")

    def broken(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", broken)
    with pytest.raises(OSError):
        store.add(parse_new_server(stdio(name="Second"), []).config)
    assert (tmp_path / "integrations.json").read_text(encoding="utf-8") == before
    assert [s.id for s in store.servers()] == [first.id]  # the one that was not kept is not there
