"""What the shell says is on screen, read with suspicion, and written out for the model.

The shell answers `apps` with the top-level windows of the apps on the desktop and `look` with a
compact accessibility snapshot of one window (`Snapshot`): each element with the id the shell gave
it for this look, its depth, its control type, its name, its value (never a password field's), its
state, whether it is enabled, focused or a password field, what it can be done to, and where it is
on the screen. The shell caps the snapshot (elements, depth, characters); this side caps it again
and drops whatever is not of the expected shape, since a reply that is wrong must not become a
prompt that is wrong.

The model reads the snapshot as text (`describe`), inside the untrusted wrapper of every tool result
(`worker.guards.wrap_untrusted`): what an app shows is data, never an instruction, whatever it says.
The elements stay in memory in `Element`s for the length of the run, so that the safety rules can
look at the element a step names (`safety.judge`). None of it is logged or stored.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# The caps of this side: the shell's are the same or tighter.
MAX_ELEMENTS = 400
MAX_DEPTH = 16
MAX_NAME_CHARS = 120
MAX_VALUE_CHARS = 300
MAX_TITLE_CHARS = 160
MAX_WINDOWS = 40
# The executable of an app, as the shell names it: a file name, in lower case.
_EXE = re.compile(r"[a-z0-9][a-z0-9 ._()\-]{0,126}\.exe")
_ROLE = re.compile(r"[A-Za-z]{1,32}")
_ACTIONS = frozenset({"invoke", "toggle", "select", "expand", "value", "scroll", "text"})
_STATES = frozenset({"on", "off", "mixed", "expanded", "collapsed", "selected"})
# Characters that would let a name break the lines of the snapshot or the tags around it.
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]+")


def clean_text(value: Any, limit: int) -> str:
    """A text of the screen, safe to put on one line: control characters and line breaks are
    spaces, and it is cut to `limit` characters (with an ellipsis). Anything else is no text."""
    if not isinstance(value, str):
        return ""
    text = " ".join(_CONTROL.sub(" ", value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


@dataclass(frozen=True)
class App:
    """An app as Raphael asks for it: its executable (`calculatorapp.exe`), which the owner's
    always-allowed list keeps, and the name it goes by (`Calculator`), which questions and steps
    show."""

    exe: str
    name: str


@dataclass(frozen=True)
class Window:
    """A top-level window of an app, as `apps` listed it. `id` is the shell's handle for it; the
    title is the window's own words, in memory only."""

    id: int
    app: App
    title: str
    foreground: bool
    always_allowed: bool


@dataclass(frozen=True)
class Element:
    """One element of a snapshot."""

    id: int
    depth: int
    role: str
    name: str
    value: str | None
    enabled: bool
    focused: bool
    password: bool
    state: str | None
    actions: frozenset[str]
    rect: tuple[int, int, int, int] | None


@dataclass
class Snapshot:
    """One look at one window: its elements by id, and whether the shell had to leave some out."""

    window: int
    app: App
    title: str
    elements: dict[int, Element] = field(default_factory=dict)
    truncated: bool = False
    always_allowed: bool = False

    @property
    def focused(self) -> Element | None:
        """The element that has the keyboard focus, if the snapshot holds it."""
        return next((element for element in self.elements.values() if element.focused), None)


def parse_app(data: Any) -> App | None:
    """An app of a reply: `{"exe", "name"}`. The name falls back to the executable's stem."""
    if not isinstance(data, Mapping):
        return None
    exe = data.get("exe")
    if not isinstance(exe, str) or not _EXE.fullmatch(exe):
        return None
    name = clean_text(data.get("name"), 60) or exe[:-4]
    return App(exe=exe, name=name)


def parse_windows(result: Any) -> list[Window]:
    """The windows of an `apps` reply: `{"windows": [{"id", "exe", "name", "title", "foreground",
    "always_allowed"}]}`. A window that is not of that shape is left out."""
    rows = result.get("windows") if isinstance(result, Mapping) else None
    windows: list[Window] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or len(windows) >= MAX_WINDOWS:
            continue
        window_id, app = _int(row.get("id")), parse_app(row)
        if window_id is None or window_id <= 0 or app is None:
            continue
        windows.append(
            Window(
                id=window_id,
                app=app,
                title=clean_text(row.get("title"), MAX_TITLE_CHARS),
                foreground=row.get("foreground") is True,
                always_allowed=row.get("always_allowed") is True,
            )
        )
    return windows


def _rect(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, list) or len(value) != 4 or any(type(n) is not int for n in value):
        return None
    x, y, w, h = value
    return (x, y, w, h) if w >= 0 and h >= 0 else None


def parse_element(row: Any) -> Element | None:
    """One element of a `look` reply, or None when it is not of the expected shape. A password
    field never has a value here, whatever the reply held."""
    if not isinstance(row, Mapping):
        return None
    element_id, depth = _int(row.get("id")), _int(row.get("depth"))
    role = row.get("role")
    if element_id is None or element_id <= 0 or depth is None or not 0 <= depth <= MAX_DEPTH:
        return None
    if not isinstance(role, str) or not _ROLE.fullmatch(role):
        return None
    password = row.get("password") is True
    value = None if password else clean_text(row.get("value"), MAX_VALUE_CHARS) or None
    actions = row.get("actions")
    state = row.get("state")
    return Element(
        id=element_id,
        depth=depth,
        role=role,
        name=clean_text(row.get("name"), MAX_NAME_CHARS),
        value=value,
        enabled=row.get("enabled") is not False,
        focused=row.get("focused") is True,
        password=password,
        state=state if state in _STATES else None,
        actions=frozenset(a for a in actions if a in _ACTIONS) if isinstance(actions, list) else frozenset(),
        rect=_rect(row.get("rect")),
    )


def parse_snapshot(result: Any, window: int) -> Snapshot | None:
    """A `look` reply as a snapshot of `window`: `{"window", "app", "title", "elements",
    "truncated", "always_allowed"}`. None when it is not one, or is one of another window."""
    if not isinstance(result, Mapping) or _int(result.get("window")) != window:
        return None
    app = parse_app(result.get("app"))
    rows = result.get("elements")
    if app is None or not isinstance(rows, list):
        return None
    snapshot = Snapshot(
        window=window,
        app=app,
        title=clean_text(result.get("title"), MAX_TITLE_CHARS),
        truncated=result.get("truncated") is True or len(rows) > MAX_ELEMENTS,
        always_allowed=result.get("always_allowed") is True,
    )
    for row in rows[:MAX_ELEMENTS]:
        element = parse_element(row)
        if element is not None and element.id not in snapshot.elements:
            snapshot.elements[element.id] = element
    return snapshot


def _quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def describe_element(element: Element) -> str:
    """One line of the snapshot: `[7] Button "Send" (invoke) @ 120,40 80x24`, indented by depth."""
    parts = [f"[{element.id}]", element.role]
    if element.name:
        parts.append(_quoted(element.name))
    if element.password:
        parts.append("PASSWORD-FIELD (never type here: hand over)")
    elif element.value is not None:
        parts.append(f"value={_quoted(element.value)}")
    if element.state:
        parts.append(element.state)
    if element.focused:
        parts.append("focused")
    if not element.enabled:
        parts.append("disabled")
    if element.actions:
        parts.append("(" + ",".join(sorted(element.actions)) + ")")
    if element.rect is not None:
        x, y, w, h = element.rect
        parts.append(f"@ {x},{y} {w}x{h}")
    return "  " * min(element.depth, 8) + " ".join(parts)


def describe(snapshot: Snapshot) -> str:
    """The snapshot as the model reads it: a heading for the window, then one line per element."""
    head = f"window {snapshot.window} · {snapshot.app.name} ({snapshot.app.exe})"
    if snapshot.title:
        head += f" · title {_quoted(snapshot.title)}"
    lines = [head]
    lines += [describe_element(element) for element in snapshot.elements.values()]
    if not snapshot.elements:
        lines.append("(no elements could be read: the app may draw its own controls)")
    if snapshot.truncated:
        lines.append(f"(cut short: only the first {len(snapshot.elements)} elements are shown)")
    return "\n".join(lines)


def describe_windows(windows: list[Window]) -> str:
    """The `apps` listing as the model reads it: one line per window."""
    if not windows:
        return "(no app windows are open)"
    lines = []
    for window in windows:
        line = f"[{window.id}] {window.app.name} ({window.app.exe})"
        if window.title:
            line += f" title {_quoted(window.title)}"
        if window.foreground:
            line += " (in front)"
        lines.append(line)
    return "\n".join(lines)
