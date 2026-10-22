"""The core's narrow way to ask the shell to do something on the desktop.

The shell is the JSON-RPC client and the core the server, so the core cannot send it a request.
It sends a notification instead, and the shell answers with a request of its own:

    core  → shell   computer.request {id, op, args, run}  a notification
    shell → core    computer.reply   {id, ok, result | error}

`ShellBridge.request` sends the first and waits for the second, for at most the time it is given.
`id` is made here, fresh for every request, and a reply for an id nobody waits for (late, made up,
answered twice) changes nothing. The operations (`op`) are few, and the shell checks each of its
arguments again:

- `apps` → `{windows: [...]}`: the top-level windows of the apps on the desktop;
- `look {window}` → a snapshot of one window (`snapshot.parse_snapshot`);
- `act {window, element, action, text, keys, direction, confirmed}` → `{method}`: one step
  (`confirmed` when the owner said yes to it: only then does the shell type a line break, as
  Enter);
- `focus {window}` → `{}`: the window is brought to the front;
- `allow {exe, name}` → `{}`: the app joins the always-allowed list of the device settings.

What the shell should show while a run goes on is told with `computer.state {phase, line, run}`,
a notification nobody answers: `begin` and `step` put the overlay's 「操作中」 line up (and arm the
watch on the owner's own input), `pause` takes the watch off while a question is on screen, `end`
takes everything down (with a line to say when the run was stopped by Esc, which hid the overlay).
The shell tells the core that the owner touched the mouse or the keyboard with `computer.interrupt`.

`run` is the id of the run (`ComputerRun.run_id`) that a request or a state is for. `begin` makes
it the shell's run and `end` revokes it at once: a request that was asked for before, still queued
for the desktop or under way there, takes no step after that (the shell checks the id just before
everything it presses, clicks or sets), and one for a run that is not the shell's fails with
`stopped`. A late `end` of a run that a newer one replaced changes nothing.

A failure is `BridgeError` with one word: the shell's own (`gone`, `stale`, `password`,
`elevated`, `refused_app`, `own_window`, `bad_request`, `failed`, `keys_not_allowed`, `stopped`:
the run is over for the shell, `handoff`: the element the keys or the text would reach is a
sign-in, a payment or a code), or `unavailable` (no shell to ask, or it did not answer in time). Nothing here logs an argument or a
result: they hold what is on the owner's screen.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from ..protocol import invalid_params
from ..router import Router

logger = logging.getLogger(__name__)

REQUEST = "computer.request"
STATE = "computer.state"
REPLY = "computer.reply"
INTERRUPT = "computer.interrupt"

UNAVAILABLE = "unavailable"
REVOKED = "stopped"
HANDOFF = "handoff"
# The words a failure of the shell may be: anything else is read as "failed".
KINDS = frozenset(
    {
        "gone",
        "stale",
        "password",
        "elevated",
        "refused_app",
        "own_window",
        "bad_request",
        "failed",
        "keys_not_allowed",
        REVOKED,
        HANDOFF,
        UNAVAILABLE,
    }
)
_ID = re.compile(r"[0-9a-f]{32}")
PHASES = ("begin", "step", "pause", "end")


class BridgeError(Exception):
    """The shell could not do what it was asked: `kind` is one word of `KINDS`."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind if kind in KINDS else "failed"


class ShellBridge:
    """Requests to the shell and their replies. Used on the event loop only.

    `notify` sends a notification to the shell (`Router.notify`); without one, every request fails
    at once with `unavailable`. `interrupted` is called when the shell says the owner touched the
    mouse or the keyboard.
    """

    def __init__(self, notify: Callable[[str, dict], None] | None) -> None:
        self._notify = notify
        self._waiting: dict[str, asyncio.Future[dict]] = {}
        self.interrupted: Callable[[], None] = lambda: None

    def register(self, router: Router) -> None:
        """Put `computer.reply` and `computer.interrupt` on `router`."""
        router.register(REPLY, self._reply)
        router.register(INTERRUPT, self._interrupt)

    @property
    def waiting(self) -> int:
        """How many requests wait for their reply."""
        return len(self._waiting)

    async def request(
        self, op: str, args: dict[str, Any], timeout_s: float, *, run: str | None = None
    ) -> dict:
        """Ask the shell to do `op` for the run `run` and wait for its reply, for at most
        `timeout_s`. The reply's result, an object; BridgeError if the shell failed, or did not
        answer in time."""
        if self._notify is None:
            raise BridgeError(UNAVAILABLE)
        request_id = uuid.uuid4().hex
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._waiting[request_id] = future
        try:
            self._notify(REQUEST, {"id": request_id, "op": op, "args": args, "run": run})
            return await asyncio.wait_for(future, timeout_s)
        except TimeoutError:
            logger.warning("The shell did not answer a computer request (%s) in time", op)
            raise BridgeError(UNAVAILABLE) from None
        finally:
            self._waiting.pop(request_id, None)

    def state(self, phase: str, line: str | None = None, *, run: str | None = None) -> None:
        """Tell the shell where the run `run` is (`PHASES`), with the line the overlay shows.
        Never raises: the run goes on whether the overlay heard or not."""
        if self._notify is None or phase not in PHASES:
            return
        try:
            self._notify(STATE, {"phase": phase, "line": line, "run": run})
        except Exception as exc:
            logger.warning("Could not tell the shell where the run is: %s", type(exc).__name__)

    async def _reply(self, params: dict) -> dict:
        request_id = params.get("id")
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            raise invalid_params("id must be the id of a request")
        ok = params.get("ok")
        if type(ok) is not bool:
            raise invalid_params("ok must be true or false")
        future = self._waiting.get(request_id)
        if future is None or future.done():
            return {}  # late, or answered already: nobody waits for it
        if ok:
            result = params.get("result")
            future.set_result(result if isinstance(result, dict) else {})
        else:
            error = params.get("error")
            future.set_exception(BridgeError(error if isinstance(error, str) else "failed"))
        return {}

    async def _interrupt(self, params: dict) -> dict:
        try:
            self.interrupted()
        except Exception as exc:  # the shell is told nothing either way
            logger.warning("Stopping the run for the owner's input failed: %s", type(exc).__name__)
        return {}
