"""Read-only daily analytics. Costs have one source of truth: the spend ledger."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..protocol import invalid_params

from .spend import _day_start_ms


def grouped(rows, days, labels=None):
    """Rank by period total, preserving all daily values and deterministic ties."""
    values = defaultdict(lambda: [0] * len(days))
    indices = {day: i for i, day in enumerate(days)}
    for day, key, value in rows:
        values[key][indices[day]] += value
    ranked = sorted(values, key=lambda key: (-sum(values[key]), key))
    total = sum(sum(v) for v in values.values())
    if len(ranked) > 5:
        values["__other__"] = [sum(values[key][i] for key in ranked[5:]) for i in range(len(days))]
        ranked = [*ranked[:5], "__other__"]
    return {"total": total, "series": [
        {"key": key, "label": (labels or {}).get(key, key), "values": values[key], "total": sum(values[key]),
         "share": sum(values[key]) / total if total else 0}
        for key in ranked
    ]}


def tool_group(kind, tool, server):
    """Only recorded call metadata is read; model calls are not tools."""
    if kind == "model":
        return None
    if kind in ("computer", "command") or tool == "computer":
        return "computer"
    if server and server != "Raphael":
        return f"connection:{server}"
    if tool == "web_search":
        return "web_search"
    if tool == "web_read":
        return "web_read"
    if tool and tool.startswith("routine_"):
        return "routines"
    if tool == "ask_member":
        return "ask_member"
    if tool and (tool.startswith("memory_") or tool == "history_search"):
        return "memory"
    if tool in ("read_attachment", "show", "file_read", "read_file"):
        return "files"
    return "other"


class UsageAnalytics:
    def __init__(self, conn, now_ms, tz=None, providers=None):
        self.conn, self.now_ms, self.tz, self.providers = conn, now_ms, tz, providers

    async def read(self, params):
        zone = self.tz
        if params.get("range") not in ("7d", "30d"):
            raise invalid_params("range must be 7d or 30d")
        if "tz" in params:
            name = params["tz"]
            if not isinstance(name, str) or not name or len(name) > 100:
                raise invalid_params("tz must be an IANA time zone")
            try:
                zone = ZoneInfo(name)
            except (ValueError, ZoneInfoNotFoundError):
                raise invalid_params("tz must be an IANA time zone") from None
        now = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=self.now_ms())
        today = now.astimezone(zone).date()
        end_text = params.get("end_day", today.isoformat())
        try:
            if not isinstance(end_text, str) or len(end_text) != 10:
                raise ValueError
            end = date.fromisoformat(end_text)
            if end.isoformat() != end_text or end.year < 2 or end.year > 9998:
                raise ValueError
        except ValueError:
            raise invalid_params("end_day must be YYYY-MM-DD within years 2 through 9998") from None
        count = 7 if params["range"] == "7d" else 30
        days = [(end - timedelta(days=count - 1 - i)).isoformat() for i in range(count)]
        def midnight(day):
            # None means the system's rules at this midnight, not today's fixed UTC offset.
            try:
                local_zone = zone or datetime(day.year, day.month, day.day).astimezone().tzinfo
            except (OSError, OverflowError, ValueError):
                raise invalid_params("end_day is outside the system time zone's supported range") from None
            return _day_start_ms(day, local_zone)

        bounds = (midnight(date.fromisoformat(days[0])), midnight(end + timedelta(days=1)))
        def day(at):
            instant = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=at)
            return instant.astimezone(zone).date().isoformat()

        costs = list(self.conn.execute(
            "SELECT created_at_ms,purpose,model,cost_usd FROM spend"
            " WHERE created_at_ms >= ? AND created_at_ms < ?", bounds))
        provider_labels = {"openrouter": "OpenRouter", "unknown": "unknown"}
        def provider(model):
            if not model:
                return "unknown"
            pid = model[1:].partition(":")[0] if model.startswith("@") else "openrouter"
            if self.providers:
                try:
                    provider_labels[pid] = self.providers.config(pid)["name"]
                except ValueError:
                    pass  # Deleted providers keep their historical identity.
            return pid

        cost_rows = {key: [] for key in ("purpose", "model", "provider")}
        for at, purpose, model, cost in costs:
            for key, value in (("purpose", purpose), ("model", model), ("provider", provider(model))):
                cost_rows[key].append((day(at), value or "unknown", cost))
        turns = list(self.conn.execute(
            "SELECT member_id,model,source,cost_usd,created_at_ms FROM agent_turns"
            " WHERE created_at_ms >= ? AND created_at_ms < ?", bounds))
        member_info = {row[0]: (row[1], row[2]) for row in self.conn.execute("SELECT id,name,role FROM members")}
        members = {}
        for mid, model, source, cost, at in turns:
            if mid not in members:
                name, role = member_info.get(mid, ("Raphael" if mid == 0 else str(mid), ""))
                members[mid] = {"id": mid, "name": name, "role": role,
                                "deleted": mid != 0 and mid not in member_info,
                                "turns": 0, "cost_usd": 0, "models": {}}
            member = members[mid]
            member["turns"] += 1
            member["cost_usd"] += cost
            entry = member["models"].setdefault(model or "unknown", {"model": model or "unknown", "turns": 0, "cost_usd": 0})
            entry["turns"] += 1
            entry["cost_usd"] += cost
        member_total = sum(m["cost_usd"] for m in members.values())
        for member in members.values():
            member["share"] = member["cost_usd"] / member_total if member_total else 0
            member["models"] = sorted(member["models"].values(), key=lambda m: (-m["cost_usd"], -m["turns"], m["model"]))
        calls = []
        tool_labels = {}
        for table in ("agent_steps", "worker_steps"):
            for at, kind, tool, server in self.conn.execute(
                f"SELECT at_ms,kind,tool,server FROM {table} WHERE at_ms >= ? AND at_ms < ?", bounds,
            ):
                key = tool_group(kind, tool, server)
                if key:
                    calls.append((day(at), key, 1))
                    if key.startswith("connection:"):
                        tool_labels[key] = server
        dictation = {"dictations": [0] * count, "words": [0] * count, "estimated": [False] * count,
                     "estimated_words": 0, "estimated_dictations": 0, "day_basis": "capture_local"}
        for at, n, words, estimated, ew, ed in self.conn.execute(
            "SELECT day,dictations,words,estimated,estimated_words,estimated_dictations FROM dictation_daily"
            " WHERE day >= ? AND day <= ?", (days[0], days[-1]),
        ):
            i = days.index(at)
            dictation["dictations"][i], dictation["words"][i] = n, words
            dictation["estimated"][i] = bool(estimated or ew or ed)
            dictation["estimated_words"] += ew
            dictation["estimated_dictations"] += ed
        return {
            "range": params["range"], "start_day": days[0], "end_day": days[-1],
            "days": days, "tz": str(zone or now.astimezone().tzinfo), "today_day": today.isoformat(),
            "cost_by": {key: grouped(rows, days, provider_labels if key == "provider" else None)
                        for key, rows in cost_rows.items()},
            "members": sorted(members.values(), key=lambda m: (m["id"] != 0, -m["cost_usd"], -m["turns"], m["id"])),
            "tools": grouped(calls, days, tool_labels),
            "messages_by": {key: grouped([(day(at), (model if key == "model" else source) or "unknown", 1)
                                          for _, model, source, _, at in turns], days)
                            for key in ("model", "source")},
            "dictation": dictation,
        }
