"""The apps a chat can ask the owner to connect from an inline card, and how each is reached.

Each is one Google product, reached through the owner's own Google authorization (the private
connector's tools whose names start with `prefix`) or through Composio's toolkit of the same app.
An app counts as connected when its read tools can be called now.
"""

from dataclasses import dataclass

from .google import READ_TOOLS, TOOLS


@dataclass(frozen=True)
class App:
    id: str
    name: str
    prefix: str
    composio: str

    @property
    def google_tools(self) -> tuple[str, ...]:
        return tuple(name for name in TOOLS if name.startswith(self.prefix))

    @property
    def reads(self) -> tuple[str, ...]:
        return tuple(name for name in self.google_tools if name in READ_TOOLS)


APPS: dict[str, App] = {app.id: app for app in (
    App("gmail", "Gmail", "gmail_", "gmail"),
    App("googlecalendar", "Google Calendar", "calendar_", "googlecalendar"),
)}
