"""What stands between a tool's result and the model: marking it as data, cutting it down, and
refusing a call that only repeats one.

A tool's result is text that nobody vetted: a web page, a note, a mail. Whatever it says, it is data
for the task and never an instruction, and the model is told so twice: in the system prompt, and by
the tags every result is wrapped in. A result that carries the closing tag of the wrapper, or the
opening tag of the task, would end the data early and speak as the user: those tags are
neutralized inside it first. A result is cut to a size the model can use, keeping its beginning and
its end. A result of an earlier step is dropped once the model has gone on, so that the
conversation does not grow without bound.

Nothing here logs a tool name, an argument or a result.
"""

import json
import re
from collections.abc import Mapping, MutableSequence
from typing import Any

# The most of a result the model is shown, in characters. The beginning of a page says what it is
# and the end often says how it ended, so both are kept.
MAX_RESULT_CHARS = 6000
# A skill import is already bounded to 128 KiB. Keep its complete Markdown (including JSON
# escaping) when the dedicated reader returns it, while retaining the untrusted-data wrapper.
MAX_SKILL_RESULT_CHARS = 2 * 128 * 1024 + 1000
HEAD_SHARE = 0.65
CUT_MARK = "\n[... {skipped} characters left out ...]\n"
# How many of the latest rounds of calls keep their results in full.
KEEP_ROUNDS = 3
OMITTED = "[result left out: it is from an earlier step]"
# The most the arguments of one call may come to, as JSON, in characters. (The core refuses more
# too: `raphael_core.agent.plugins.MAX_ARGUMENT_CHARS`, which a test keeps the same.)
MAX_ARGUMENT_CHARS = 16000
# The same call, with the same arguments, is refused the third time.
REPEAT_LIMIT = 3

# The tags the prompts are built from. A text that holds one is changed so that it does not close,
# or open, what it is inside of.
_TAGS = re.compile(r"<(/?)\s*(tool_result|task|question|conversation|previous_attempt|background_tasks)\b", re.IGNORECASE)


def neutralize(text: str) -> str:
    """`text` with the tags of the prompts made harmless: the `<` of each becomes `＜`."""
    return _TAGS.sub(lambda found: f"＜{found.group(1)}{found.group(2)}", text)


def head_tail_split(text: str, limit: int = MAX_RESULT_CHARS) -> tuple[str, bool]:
    """`text` cut to about `limit` characters, keeping its head and its tail with a mark between,
    and whether anything was left out."""
    if len(text) <= limit:
        return text, False
    skipped = len(text) - limit
    mark = CUT_MARK.format(skipped=skipped)
    room = max(limit - len(mark), 0)
    head = int(room * HEAD_SHARE)
    tail = room - head
    return text[:head] + mark + (text[len(text) - tail :] if tail else ""), True


def wrap_untrusted(tool: str, text: str, *, ok: bool) -> str:
    """A tool's result as the model reads it: cut, neutralized and inside tags that say it is data
    from outside, never to be obeyed."""
    limit = MAX_SKILL_RESULT_CHARS if tool == "skill_read" else MAX_RESULT_CHARS
    shown, cut = head_tail_split(neutralize(text), limit)
    status = "ok" if ok else "error"
    note = " cut_short=true" if cut else ""
    return (
        f'<tool_result untrusted="true" tool="{_attribute(tool)}" status="{status}"{note}>\n'
        f"{shown}\n</tool_result>"
    )


def _attribute(value: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.:-]', "_", value)[:80]


def elide_old(messages: MutableSequence[dict[str, Any]], keep: int = KEEP_ROUNDS) -> None:
    """Replace the results of all but the last `keep` rounds of calls with a short mark. A round is
    a reply of the model that asked for calls, with the results that followed it."""
    rounds = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    for start in rounds[: max(len(rounds) - keep, 0)]:
        for message in messages[start + 1 :]:
            if message.get("role") != "tool":
                break
            message["content"] = OMITTED


def canonical(arguments: Mapping[str, Any]) -> str:
    """The arguments of a call as one text, the same for the same arguments."""
    return json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)


class RepeatGuard:
    """Refuses a call that is the same as two before it: the same tool with the same arguments for
    the third time. Such a call cannot tell the model anything it does not know, and a model that
    keeps asking it is going round in a circle."""

    def __init__(self, limit: int = REPEAT_LIMIT) -> None:
        self._limit = limit
        self._seen: dict[tuple[str, str], int] = {}

    def allows(self, name: str, arguments: Mapping[str, Any]) -> bool:
        """Count the call, and say whether it may be made."""
        key = (name, canonical(arguments))
        count = self._seen.get(key, 0) + 1
        self._seen[key] = count
        return count < self._limit
