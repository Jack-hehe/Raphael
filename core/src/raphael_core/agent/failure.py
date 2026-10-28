"""Why the assistant could not do what it was asked, as a reason Raphael can give.

A failure ends in a short 否 line that says why, in the user's language (`persona.PHRASES`): the
kinds below name those phrases. The detail of what went wrong (the message of an exception, which
could hold a key or what the user said) is never kept; the kind is all that travels.
"""

from httpx import ConnectError, TimeoutException

from ..brain.openrouter import OpenRouterError

# no_key: no OpenRouter key is stored. auth: OpenRouter does not accept it. budget: this month's
# limit is used up. credits: the OpenRouter account has none left. offline: the network cannot
# be reached. timeout: the request did not finish in time. error: anything else.
KINDS = ("no_key", "auth", "budget", "credits", "offline", "timeout", "error")

_KIND_OF_OPENROUTER = {
    "auth": "auth",
    "credits": "credits",
    "network": "offline",
    "offline": "offline",
    "timeout": "timeout",
}


class AgentFailure(Exception):
    """The request cannot be answered by a model; `kind` is one of KINDS."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind if kind in KINDS else "error")
        self.kind = kind if kind in KINDS else "error"


def failure_kind(error: BaseException) -> str:
    """The kind of failure `error` amounts to."""
    if isinstance(error, OpenRouterError):
        return _KIND_OF_OPENROUTER.get(error.kind, "error")
    if isinstance(error, (TimeoutError, TimeoutException)):
        return "timeout"
    if isinstance(error, (ConnectionError, ConnectError)):
        return "offline"
    return "error"
