"""What Raphael did between a question and its answer, as the conversation view shows it.

A step is one thing done for a turn: a lookup of Raphael's own (the weather, a web search), a tool of
a plugin, a command for this computer, or, in the background worker, a model call. It keeps the tool,
the server it belongs to, how it ended, how long it took and, when it failed, why, as one word of
`REASONS`; the app words that word in the language of the window.

Never an argument, a result or an error message. A service's message can quote what it was given (a
name, an address, a sentence the user said), so even a short one could carry what the user said
into a table that is not cleared like the history. The kind of a failure is all that is kept: the
plugins' (`integrations.client.KINDS`), the lookups' (`tools.ToolResult.error`) and the model's
(`failure.KINDS`) are folded into the few reasons below.
"""

from dataclasses import dataclass

# bad_arguments: the tool was given something it does not take. auth: it needs a new sign-in.
# missing_secret: a key or token it needs is not set. denied: the sign-in was refused. timeout:
# the service did not answer in time. network: it could not be reached. not_found: what was asked
# for is not there (a city, a program). blocked: the tool may not run now (switched off, no yes, not
# a tool of the task). gone: the tool is no longer there. tool_error: the service said it failed.
# protocol: it answered with something that is not an answer. cancelled: the user stopped it.
# not_made: the turn could ask about one call only, so this one was not made. credits, budget,
# no_key: the model could not be paid for. On the owner's desktop (`raphael_core.computer`):
# password: a password field, which only the owner fills; handoff: a step that is the owner's (a
# sign-in, a payment, an administrator's prompt); refused_app: an app Raphael never operates;
# interrupted: the owner's own mouse or keys stopped the run. error: anything else.
REASONS = frozenset(
    {
        "bad_arguments",
        "auth",
        "missing_secret",
        "denied",
        "timeout",
        "network",
        "not_found",
        "blocked",
        "gone",
        "tool_error",
        "protocol",
        "cancelled",
        "not_made",
        "credits",
        "budget",
        "no_key",
        "password",
        "handoff",
        "refused_app",
        "interrupted",
        "error",
    }
)

# Kinds of other parts that mean one of the reasons above.
_ALIASES = {
    "offline": "network",
    "http_error": "network",
    "server": "network",
    "rate_limit": "network",
    "bad_response": "protocol",
    "changed": "blocked",
    "stale": "not_found",
    "elevated": "handoff",
    "own_window": "blocked",
    "keys_not_allowed": "blocked",
    "unavailable": "network",
    "failed": "error",
}

# The reason of a step that failed with no kind, by how it ended.
_BY_OUTCOME = {"timeout": "timeout", "blocked": "blocked", "cancelled": "cancelled"}


def reason_of(outcome: str, kind: str | None) -> str | None:
    """The reason to keep for a step that ended with `outcome` ("ok", "error", "timeout", "blocked",
    "cancelled") because of `kind`; None for a step that went well."""
    if outcome == "ok":
        return None
    if kind:
        kind = _ALIASES.get(kind, kind)
        if kind in REASONS:
            return kind
    return _BY_OUTCOME.get(outcome, "error")


@dataclass(frozen=True)
class Step:
    """One step of a turn. `kind` is "tool" (a lookup or a plugin's tool), "command" (something
    done to this computer), "computer" (a step of a run on the owner's desktop: `tool` is the
    action, `server` the name of the app) or "model" (a model call of the worker); `server` is the
    plugin's name, "Raphael" for Raphael's own lookups, None for a command; `at_ms` is when it
    ended."""

    kind: str
    tool: str
    server: str | None
    outcome: str
    ms: int
    reason: str | None = None
    at_ms: int = 0
