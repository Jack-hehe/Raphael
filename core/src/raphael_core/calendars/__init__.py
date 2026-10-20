"""The calendars on Home's Today card: iCalendar subscription addresses, read-only.

`ics` turns the text of a calendar into the events of one day, `fetch` downloads it, `vault` keeps
the addresses in the Windows Credential Manager (an address is a secret: whoever has it can read the
calendar) and `service` puts it all behind the `calendar.*` methods.
"""
