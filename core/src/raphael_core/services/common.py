"""What the services share: the kind a failure is told by, and the budget of a text a tool returns.

A service never raises anything but `ServiceError` for a failure of the world, and the error says
which way it failed in one word (`kind`), never in a message: a message can quote what the service
was given (an address, a search, a page), and those must not reach a log or a step.

A text a tool returns (a page, the text of a PDF) is held to TEXT_BUDGET characters. A longer one
keeps its first three quarters of the budget and its last quarter, with a line where the middle was
and a footer that says it was cut and by how much: the start of a page says what it is, and the end
often holds its conclusion or its date.
"""

TEXT_BUDGET = 15_000
_HEAD_SHARE = 0.75


class ServiceError(Exception):
    """A service that could not do what it was asked. `kind` says why, in one word:

    - "bad_url" (not an http or https address with a host, or one with a user name in it),
      "blocked_address" (the host is this computer, the local network or an address of no one),
      "redirects" (more than five), "too_large", "unsupported_type" (neither a page nor a PDF);
    - "missing_secret" (the service needs a key and none is stored), "not_configured" (SearXNG with
      no address), "auth", "credits", "rate_limit", "http_error", "timeout", "network",
      "bad_response";
    - "vision_unsupported" (the model chosen to read images cannot), "bad_pdf", "encrypted",
      "no_text" (a PDF with no text that could be read).
    """

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def fit(text: str, budget: int = TEXT_BUDGET) -> tuple[str, bool]:
    """`text` held to about `budget` characters, and whether it had to be cut: its head (three
    quarters of the budget) and its tail (the rest), a mark where the middle was, and a footer."""
    if len(text) <= budget:
        return text, False
    head = int(budget * _HEAD_SHARE)
    tail = budget - head
    left_out = len(text) - head - tail
    footer = (
        f"[Cut short: the text had {len(text)} characters; {left_out} in the middle were left out.]"
    )
    return f"{text[:head].rstrip()}\n\n[…]\n\n{text[-tail:].lstrip()}\n\n{footer}", True
