"""The apps the owner connected, counted one way for everyone who shows them.

The roster every model is given (`agent.team_context`), the connections page and the sidebar's
「連結應用程式」 read an app's state and the count from here, so none of them can disagree.

An app is *connected* when its tools can be called now: it is on, it has signed in, it has no
failure, at least one of its tools is on and no other connection to the same app is counted already.
Every other connection is listed with the state that says why, and never counted:

- `needs_sign_in`: it is waiting for a sign-in, or its sign-in ran out or was taken back;
- `needs_secret`: a value it was given is gone from the vault;
- `error`: it failed (the last call or the sign-in did);
- `unchecked`: its tools were never read;
- `no_tools`: it is on and well, but every tool of it is switched off;
- `disabled`: the owner switched it off;
- `duplicate`: it works, and another connection to the same app is the one counted (the same Google
  account reached through Google and through Composio, a store service added twice). One app is one
  app however many accounts or paths reach it;
- `unknown`: it could not be read. Nothing is guessed: with one such connection the inventory is
  `partial` and has no count, and when none can be read at all it is `unknown`.

Only names, states and counts are kept here, nothing secret.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

READY = "ready"
DUPLICATE = "duplicate"
DISABLED = "disabled"
NO_TOOLS = "no_tools"
NEEDS_SIGN_IN = "needs_sign_in"
NEEDS_SECRET = "needs_secret"
ERROR = "error"
UNCHECKED = "unchecked"
UNKNOWN = "unknown"

COMPLETE = "complete"
PARTIAL = "partial"

# Which connection keeps the app when two reach it: the owner's own Google first, then Composio's.
_PRIORITY = {"google": 0, "composio": 1}


def app_state(*, enabled: bool, status: str, error: str | None, tools: int, waiting: bool = False) -> str:
    """The state of one connection by itself (duplicates are found among all of them, `mark_duplicates`).
    `status` is `ready`, `unchecked`, `error` or `disabled` as the connection reports it, `error` the kind of
    its latest failure, `tools` how many of its tools can be called now and `waiting` that a sign-in is under way."""
    if not enabled:
        return DISABLED
    if error == "auth" or waiting:
        return NEEDS_SIGN_IN
    if error == "missing_secret":
        return NEEDS_SECRET
    if error or status == "error":
        return ERROR
    if status == "unchecked":
        return UNCHECKED
    return READY if tools > 0 else NO_TOOLS


def unreadable(identifier: str, name: str, enabled: bool = False) -> dict:
    """A connection that could not be read: listed, never counted, and the inventory is partial."""
    return {"id": identifier, "name": name, "enabled": enabled, "status": UNCHECKED, "error": None, "tools": 0,
            "state": UNKNOWN, "services": [], "source": None}


def mark_duplicates(entries: list[dict]) -> list[dict]:
    """Find the connections that work and reach an app another one reaches already, and give them the
    state `duplicate`. `services` names the apps a connection reaches (a store entry, a Composio toolkit,
    the Google products whose tools can be called now); a custom server reaches none and is never a duplicate.
    The first of them by priority and then by order is the one that is counted."""
    claimed: set[str] = set()
    order = sorted(range(len(entries)), key=lambda i: (_PRIORITY.get(entries[i].get("source"), 2), i))
    for index in order:
        entry = entries[index]
        if entry["state"] != READY:
            continue
        services = set(entry.get("services") or ())
        if services and services <= claimed:
            entry["state"] = DUPLICATE
        else:
            claimed |= services
    return entries


def summarize(entries: list[Mapping]) -> tuple[str, int | None]:
    """`(state, count)`: `complete` and the number of connected apps, or `partial` and no number when
    any connection could not be read (a number over a part of the apps would only look right)."""
    if any(entry.get("state") == UNKNOWN for entry in entries):
        return PARTIAL, None
    return COMPLETE, sum(1 for entry in entries if entry.get("state") == READY)


def legacy_state(app: Mapping) -> str:
    """The state of a connection that was listed without one (a list made by something other than the hub)."""
    status = app.get("status", UNKNOWN)
    return (NEEDS_SIGN_IN if app.get("error") == "auth" else NEEDS_SECRET if app.get("error") == "missing_secret"
            else status)


def public_app(app: Mapping) -> dict:
    """Only the public metadata of a connection, never its provider configuration or a credential."""
    state = app["state"] if "state" in app else legacy_state(app)
    return {"id": app["id"], "name": app["name"], "enabled": bool(app["enabled"]), "state": state,
            "tools": app["tools"] if type(app.get("tools")) is int else 0, "connected": state == READY}


@dataclass(frozen=True)
class Inventory:
    """What the owner's apps are: `state` is `complete`, `partial` or `unknown`, `count` the connected apps
    (None unless complete) and `apps` every connection with its state."""

    state: str
    count: int | None
    apps: list[dict]

    def wire(self) -> dict:
        return {"state": self.state, "count": self.count, "apps": self.apps}


def read_inventory(read: Callable[[], list[dict]]) -> Inventory:
    """The inventory `read` lists. A list that cannot be read at all is `unknown`, with no apps and no count."""
    try:
        apps = [public_app(entry) for entry in read()]
    except Exception as exc:  # the list of connections must not break a prompt or a page
        logger.warning("Could not list the connections: %s", type(exc).__name__)
        return Inventory("unknown", None, [])
    state, count = summarize(apps)
    return Inventory(state, count, apps)
