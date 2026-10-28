"""Speaker labels for model-facing conversation data, including quoted source evidence."""

from collections.abc import Sequence

from .history import Turn


def turn_speakers(turn: Turn) -> tuple[str, str]:
    """Stable role labels for old turns as well as newly attributed exchanges."""
    if turn.route == "exchange":
        return "requesting assistant", "responding assistant"
    speaker = f"assistant (member {turn.member_id})" if turn.member_id else "Raphael"
    if turn.route == "routine" or turn.source == "routine":
        return "routine", speaker
    if turn.source == "member":
        requester = "requesting assistant" if turn.peer_id is None else (
            f"assistant (member {turn.peer_id})" if turn.peer_id else "Raphael"
        )
        return requester, speaker
    return "owner", speaker


def labelled_lines(speaker: str, text: str) -> str:
    """Label every line without changing an existing quotation's inner speaker marking."""
    label = " ".join(speaker.splitlines())
    return "\n".join(f"{label}: {line}" for line in text.splitlines())


def owner_lines(turns: Sequence[Turn]) -> tuple[str, ...]:
    """Verbatim owner lines from answered turns; assistant requests and routines are excluded."""
    return tuple(dict.fromkeys(
        line
        for turn in turns
        if turn.status == "done" and turn.route not in ("exchange", "routine")
        and turn.source not in ("member", "routine")
        for line in turn.question.splitlines() if line.strip()
    ))


def relayed_answer(speaker: str, text: str, owner_sources: Sequence[str] = ()) -> str:
    """Keep the respondent's words and exact matching owner-source lines distinct.

    Matching is evidence of what the owner said, not an attempt to infer quotation semantics.
    Only lines present verbatim in the reply cross the thread boundary.
    """
    quoted = [line for line in owner_sources if line in text]
    evidence = ""
    if quoted:
        evidence = (
            "source: The owner also said these exact lines; preserve their original attribution.\n"
            + labelled_lines("owner", "\n".join(quoted)) + "\n"
        )
    return evidence + labelled_lines(speaker, text)
