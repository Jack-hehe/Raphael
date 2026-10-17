"""The only path between the device side and the brain.

Services register handlers by method name and reach each other with `await router.call(...)`.
Params and results cross a JSON round trip, so nothing that is not JSON can pass. That keeps the
in-process call identical to one made over a socket, which is what lets the brain move to another
machine later.

Never log params or results here: `secrets.set_openrouter_key` carries the OpenRouter key.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .protocol import INTERNAL_ERROR, INVALID_PARAMS, METHOD_NOT_FOUND, RpcError

logger = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[Any]]
Notifier = Callable[[str, dict], None]

# What json.dumps raises for values that are not JSON (and, with allow_nan=False, for NaN/inf,
# which serde_json on the Rust side would reject).
_NOT_JSON = (TypeError, ValueError, RecursionError)


def _through_json(value: Any) -> Any:
    """The value as the peer would receive it: tuples become lists, keys become strings."""
    return json.loads(json.dumps(value, allow_nan=False))


class Router:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._notifier: Notifier | None = None

    def register(self, method: str, handler: Handler) -> None:
        if method in self._handlers:
            raise ValueError(f"method already registered: {method}")
        self._handlers[method] = handler

    def methods(self) -> list[str]:
        return sorted(self._handlers)

    async def call(self, method: str, params: dict | None = None) -> Any:
        """Run a handler. Every failure comes out as an RpcError.

        A handler's own RpcError passes through unchanged; any other exception becomes
        RpcError(INTERNAL_ERROR) and is logged with its traceback. So does a CancelledError that is
        not the call's own cancellation: a task the handler awaited was cancelled by someone else.
        Cancelling the call itself, KeyboardInterrupt and SystemExit pass through.
        """
        handler = self._handlers.get(method)
        if handler is None:
            raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
        try:
            wire_params = _through_json({} if params is None else params)
        except _NOT_JSON as exc:
            raise RpcError(INVALID_PARAMS, f"Invalid params: {exc}") from exc
        if not isinstance(wire_params, dict):
            raise RpcError(INVALID_PARAMS, "Invalid params: expected a JSON object")
        try:
            result = await handler(wire_params)
        except RpcError:
            raise
        except asyncio.CancelledError as exc:
            # The call's own cancellation must propagate. A sub-task or future the handler awaited
            # that someone else cancelled is only a failed call: it has to be answered, or the
            # request vanishes (and serve's drain at the end of input would abort).
            task = asyncio.current_task()
            if task is None or task.cancelling():
                raise
            logger.warning("%s was cancelled from outside", method)
            raise RpcError(INTERNAL_ERROR, f"{method} was cancelled") from exc
        except Exception as exc:
            logger.exception("Handler for %s failed", method)
            raise RpcError(INTERNAL_ERROR, str(exc) or type(exc).__name__) from exc
        try:
            return _through_json(result)
        except _NOT_JSON as exc:
            logger.exception("Result of %s is not JSON", method)
            raise RpcError(INTERNAL_ERROR, f"Result of {method} is not JSON: {exc}") from exc

    def set_notifier(self, fn: Notifier | None) -> None:
        self._notifier = fn

    def notify(self, method: str, params: dict) -> None:
        """Send a notification to whoever listens; does nothing when nobody does.

        Fire and forget: it never raises, so a broken listener or a bad payload cannot take down
        the code that reports progress (the audio level, say). It may be called from any thread,
        provided the notifier is thread-safe.
        """
        notifier = self._notifier
        if notifier is None:
            return
        try:
            notifier(method, _through_json(params))
        except Exception:
            logger.exception("Dropped notification %s", method)
