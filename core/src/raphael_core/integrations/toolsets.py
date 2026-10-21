"""How the 「連接」 tab groups a plugin's tools: the ones that change things and the ones that only read.

This is for the screen only. Whether a tool may run is whether it is on (`config.ServerConfig.tool_on`)
and nothing else, so a wrong guess here puts a tool in the other list and does no harm.

A tool's own word comes first: its annotations (`readOnlyHint`, `destructiveHint`) as the server
sent them. A server can claim anything, and a tool with no annotations says nothing, so the name is
the fallback: a tool named for a verb that reads (`get_`, `search_`, `list_`, ...) is a read, and
every other, including a name that is none of the known verbs, is counted as one that changes
things, which is the list a person looks at more carefully.
"""

import re

from .config import ToolRecord

# The words (split at `_`, `-`, `.` and a change of case) that mean a tool only looks.
_READ_WORDS = frozenset(
    {
        "get", "list", "search", "find", "read", "fetch", "query", "lookup", "describe", "show",
        "view", "check", "count", "retrieve", "browse", "inspect", "export", "download", "preview",
        "summarize", "who", "whoami", "status", "info", "history", "resolve", "explain", "analyze",
    }
)
_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")


def tool_kind(tool: ToolRecord) -> str:
    """`"read"` for a tool that only looks, `"write"` for one that changes something (or may)."""
    if tool.destructive_hint is True or tool.read_only_hint is False:
        return "write"
    if tool.read_only_hint is True:
        return "read"
    words = [word.lower() for word in _WORD.findall(tool.name)]
    return "read" if words and words[0] in _READ_WORDS else "write"
