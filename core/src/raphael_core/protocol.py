"""JSON-RPC 2.0 messages for the boundary between the device side, the brain and the app.

One JSON object per message. This module only builds and validates messages; dispatch is in
router.py and the transport is in stdio_server.py.
"""

import json
from typing import Any

PROTOCOL_VERSION = 1

# Errors defined by JSON-RPC 2.0.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Raphael's own errors. The Rust side matches on these numbers.
NOT_CONFIGURED = 1001
BUDGET_EXCEEDED = 1002
BUSY = 1003
NO_MICROPHONE = 1004
NOT_FOUND = 1005
# The cloud speech-to-text failed and there is no local model to fall back on.
NO_LOCAL_MODEL = 1006
# The core refuses a model call or a tool call of a background task (it was cancelled, a limit was
# reached, the model failed); `data` says which. Only the worker is ever answered this.
WORKER_STOPPED = 1100


class RpcError(Exception):
    """An error that crosses the boundary: raised by handlers, sent as a response's `error`."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(code, message, data)  # keeps copy and pickle working
        self.code = code
        self.message = message
        self.data = data

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


def invalid_params(message: str) -> RpcError:
    """An INVALID_PARAMS error. `message` names a param, never a value: a value may be a key, or
    what the user said."""
    return RpcError(INVALID_PARAMS, f"Invalid params: {message}")


def request(id: int | str, method: str, params: dict | None) -> dict:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def notification(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def response(id: int | str | None, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def error_response(id: int | str | None, err: RpcError) -> dict:
    error: dict[str, Any] = {"code": err.code, "message": err.message}
    if err.data is not None:
        error["data"] = err.data
    return {"jsonrpc": "2.0", "id": id, "error": error}


def _is_valid_id(value: Any) -> bool:
    # Our peers only use integers and strings; bool is an int subclass but never an id.
    is_integer = isinstance(value, int) and not isinstance(value, bool)
    return value is None or isinstance(value, str) or is_integer


def peek_id(line: str) -> int | str | None:
    """The id to answer a rejected line with: its own if it has a valid one, else None (null)."""
    try:
        msg = json.loads(line)
    except (ValueError, RecursionError):
        return None
    value = msg.get("id") if isinstance(msg, dict) else None
    return value if _is_valid_id(value) else None


def parse_message(line: str) -> dict:
    """Parse and validate one incoming line: a request, a notification or a response.

    Raises RpcError(PARSE_ERROR) when the line is not JSON and RpcError(INVALID_REQUEST) when it
    is JSON but not a JSON-RPC 2.0 message. Batches (arrays) are not supported. The line itself is
    never put in an error message: it may carry the OpenRouter key.
    """
    try:
        msg = json.loads(line)
    except (ValueError, RecursionError) as exc:  # JSONDecodeError is a ValueError
        raise RpcError(PARSE_ERROR, f"Parse error: {exc}") from exc
    if not isinstance(msg, dict):
        raise RpcError(INVALID_REQUEST, "Invalid Request: a message must be a JSON object")
    if msg.get("jsonrpc") != "2.0":
        raise RpcError(INVALID_REQUEST, 'Invalid Request: "jsonrpc" must be "2.0"')
    if "method" in msg:
        if not isinstance(msg["method"], str):
            raise RpcError(INVALID_REQUEST, 'Invalid Request: "method" must be a string')
    elif "result" not in msg and "error" not in msg:
        raise RpcError(INVALID_REQUEST, 'Invalid Request: expected "method", "result" or "error"')
    if "id" in msg and not _is_valid_id(msg["id"]):
        raise RpcError(INVALID_REQUEST, 'Invalid Request: "id" must be an integer or a string')
    return msg
