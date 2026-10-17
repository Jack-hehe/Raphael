import json
import pickle

import pytest

from raphael_core.protocol import (
    BUDGET_EXCEEDED,
    BUSY,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    NO_MICROPHONE,
    NOT_CONFIGURED,
    NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    RpcError,
    error_response,
    notification,
    parse_message,
    peek_id,
    request,
    response,
)


def test_parse_rejects_missing_jsonrpc():
    with pytest.raises(RpcError) as e:
        parse_message('{"id": 1, "method": "x"}')
    assert e.value.code == INVALID_REQUEST


def test_parse_rejects_bad_json():
    with pytest.raises(RpcError) as e:
        parse_message("{nope")
    assert e.value.code == PARSE_ERROR


def test_error_response_shape():
    msg = error_response(7, RpcError(BUSY, "busy"))
    assert msg == {"jsonrpc": "2.0", "id": 7, "error": {"code": 1003, "message": "busy"}}


# --- beyond the brief's examples -------------------------------------------------------------


def test_protocol_version_and_error_codes_are_the_wire_contract():
    # The Rust side hard-codes these numbers: changing one is a protocol change.
    assert PROTOCOL_VERSION == 1
    assert [PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR] == [
        -32700,
        -32600,
        -32601,
        -32602,
        -32603,
    ]
    assert [NOT_CONFIGURED, BUDGET_EXCEEDED, BUSY, NO_MICROPHONE, NOT_FOUND] == [
        1001,
        1002,
        1003,
        1004,
        1005,
    ]


def test_rpc_error_exposes_code_message_and_data():
    err = RpcError(NOT_FOUND, "no such item", {"id": 3})
    assert (err.code, err.message, err.data) == (1005, "no such item", {"id": 3})
    assert RpcError(BUSY, "busy").data is None
    assert str(RpcError(BUSY, "busy")) == "[1003] busy"


def test_rpc_error_survives_pickling():
    copy = pickle.loads(pickle.dumps(RpcError(NOT_FOUND, "no such item", {"id": 3})))
    assert (copy.code, copy.message, copy.data) == (1005, "no such item", {"id": 3})


def test_request_and_notification_shapes():
    assert request(1, "a.b", {"x": 1}) == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "a.b",
        "params": {"x": 1},
    }
    assert request("r-1", "a.b", None) == {"jsonrpc": "2.0", "id": "r-1", "method": "a.b"}
    assert notification("audio.level", {"level": 0.5}) == {
        "jsonrpc": "2.0",
        "method": "audio.level",
        "params": {"level": 0.5},
    }


def test_response_shape():
    assert response(3, {"ok": True}) == {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
    # A null result is still a result: the member must be present.
    assert response(4, None) == {"jsonrpc": "2.0", "id": 4, "result": None}


def test_error_response_carries_data_and_allows_a_null_id():
    msg = error_response(None, RpcError(PARSE_ERROR, "Parse error", {"at": 3}))
    assert msg == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Parse error", "data": {"at": 3}},
    }


@pytest.mark.parametrize(
    "line",
    [
        '{"jsonrpc":"2.0","id":1,"method":"a.b","params":{"x":1}}',
        '{"jsonrpc":"2.0","id":"abc","method":"a.b"}',
        '{"jsonrpc":"2.0","id":null,"method":"a.b"}',
        '{"jsonrpc":"2.0","method":"audio.level","params":{"level":0.5}}',
        '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}',
        '{"jsonrpc":"2.0","id":1,"result":null}',
        '{"jsonrpc":"2.0","id":2,"error":{"code":1004,"message":"no mic"}}',
    ],
    ids=[
        "request",
        "string-id",
        "null-id",
        "notification",
        "result",
        "null-result",
        "error",
    ],
)
def test_parse_accepts_valid_messages(line):
    assert parse_message(line) == json.loads(line)


@pytest.mark.parametrize(
    "line",
    [
        "[]",
        '[{"jsonrpc":"2.0","id":1,"method":"x"}]',
        "5",
        '"text"',
        "null",
        '{"jsonrpc":"1.0","id":1,"method":"x"}',
        '{"jsonrpc":2.0,"id":1,"method":"x"}',
        '{"jsonrpc":"2.0","id":1}',
        '{"jsonrpc":"2.0","id":1,"method":5}',
        '{"jsonrpc":"2.0","id":1,"method":["a"]}',
        '{"jsonrpc":"2.0","id":1.5,"method":"x"}',
        '{"jsonrpc":"2.0","id":true,"method":"x"}',
        '{"jsonrpc":"2.0","id":[1],"method":"x"}',
        '{"jsonrpc":"2.0","id":{"a":1},"method":"x"}',
    ],
    ids=[
        "empty-array",
        "batch-not-supported",
        "number",
        "string",
        "null",
        "wrong-version",
        "version-not-a-string",
        "no-method-result-or-error",
        "method-is-a-number",
        "method-is-a-list",
        "float-id",
        "bool-id",
        "list-id",
        "object-id",
    ],
)
def test_parse_rejects_invalid_requests(line):
    with pytest.raises(RpcError) as e:
        parse_message(line)
    assert e.value.code == INVALID_REQUEST


@pytest.mark.parametrize(
    "line",
    ["", "   ", "not json", '{"jsonrpc":"2.0"', "[" * 100_000],
    ids=["empty", "blank", "text", "truncated", "too-deeply-nested"],
)
def test_parse_rejects_unparseable_text_as_a_parse_error(line):
    with pytest.raises(RpcError) as e:
        parse_message(line)
    assert e.value.code == PARSE_ERROR


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('{"id": 7, "method": "x"}', 7),
        ('{"jsonrpc":"2.0","id":"abc","method":5}', "abc"),
        ('{"jsonrpc":"2.0","id":0}', 0),
        ('{"jsonrpc":"2.0","method":"x"}', None),
        ('{"jsonrpc":"2.0","id":null,"method":5}', None),
        ('{"id":[1]}', None),
        ('{"id":true}', None),
        ('{"id":1.5}', None),
        ("[1, 2]", None),
        ("{nope", None),
        ("", None),
    ],
    ids=[
        "int",
        "string",
        "zero",
        "no-id",
        "null-id",
        "list-id",
        "bool-id",
        "float-id",
        "array",
        "bad-json",
        "empty",
    ],
)
def test_peek_id_reads_a_usable_id_from_a_rejected_line(line, expected):
    result = peek_id(line)
    assert result == expected
    assert type(result) is type(expected)  # True must not pass for 1


def test_parse_errors_never_echo_the_rejected_text():
    # secrets.set_openrouter_key sends the API key through this channel.
    with pytest.raises(RpcError) as e:
        parse_message('{"jsonrpc":"2.0","method":"secrets.set_openrouter_key","params":{"key":"sk-or-v1-SECRET"')
    assert "SECRET" not in e.value.message
