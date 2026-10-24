"""Run-level analytics over retained conversations and tasks, with measured timing metadata.

The spend ledger remains the source for the monthly bill. Run totals describe retained work;
legacy rows without metrics remain untimed. CSV includes all matching runs, not just a UI page.
"""

import csv
import io
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..protocol import invalid_params


def save_metrics(conn, kind, run_id, started, finished, calls):
    conn.execute("INSERT OR REPLACE INTO run_metrics VALUES(?,?,?,?,?,?,?)", (
        kind, run_id, started, finished, sum(c.get("tokens", 0) for c in calls), len(calls),
        json.dumps(calls, separators=(",", ":")),
    ))
    conn.commit()


class RunAnalytics:
    def __init__(self, conn, now_ms, providers=None):
        self.conn, self.now_ms, self.providers = conn, now_ms, providers

    def _period(self, params):
        period = params.get("range", "month")
        if period not in ("week", "month", "last_month", "all"):
            raise invalid_params("Unknown run period")
        try:
            name = params.get("tz", "UTC")
            if not isinstance(name, str) or len(name) > 100:
                raise ValueError
            zone = ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            raise invalid_params("tz must be an IANA time zone") from None
        today = datetime.fromtimestamp(self.now_ms() / 1000, zone).date()
        start, end = None, today + timedelta(days=1)
        if period == "week": start = today - timedelta(days=today.weekday())
        elif period == "month": start = today.replace(day=1)
        elif period == "last_month":
            end = today.replace(day=1)
            start = (end - timedelta(days=1)).replace(day=1)
        def ms(day): return int(datetime(day.year, day.month, day.day, tzinfo=zone).timestamp() * 1000)
        return period, zone, start, end, ms(start) if start else 0, ms(end)

    def _provider(self, model):
        if not model: return "unknown"
        if model.startswith("cli:"): return model.split(":")[1]
        if not model.startswith("@"): return "openrouter"
        ident = model[1:].partition(":")[0]
        if self.providers:
            try: return self.providers.config(ident)["name"]
            except ValueError: pass
        return ident

    def _routine_source(self, run):
        if run["turn_id"] is not None:
            task = self.conn.execute("SELECT * FROM worker_tasks WHERE turn_id=? ORDER BY id DESC LIMIT 1", (run["turn_id"],)).fetchone()
            if task:
                return "task", dict(task)
            turn = self.conn.execute("SELECT * FROM agent_turns WHERE id=?", (run["turn_id"],)).fetchone()
            if turn:
                return "turn", dict(turn)
        return None, None

    def _rows(self, lower, upper, only=None):
        members = {row[0]: (row[1], row[2]) for row in self.conn.execute("SELECT id,name,avatar FROM members")}
        members[0] = ("Raphael", "")
        rows = []
        for kind, table in (("turn", "agent_turns"), ("task", "worker_tasks")):
            if only and kind != only[0]: continue
            query = f"SELECT * FROM {table} WHERE created_at_ms>=? AND created_at_ms<?"
            args = (lower, upper)
            if only:
                query += " AND id=?"
                args += (only[1],)
            for raw in self.conn.execute(query, args):
                item = dict(raw)
                # A routine's report is also copied into its member's conversation. The task is
                # the measured run; its copied report is not another run that spent the same cost.
                if kind == "turn" and (item["route"] == "routine" or self.conn.execute("SELECT 1 FROM routine_runs WHERE turn_id=? LIMIT 1", (item["id"],)).fetchone() or self.conn.execute("SELECT 1 FROM worker_tasks WHERE turn_id=? LIMIT 1", (item["id"],)).fetchone()): continue
                if kind == "task" and not only and self.conn.execute("SELECT 1 FROM routine_runs WHERE turn_id=?", (item["turn_id"],)).fetchone():
                    continue
                mid = item["member_id"]
                member, avatar = members.get(mid, (str(mid), ""))
                metric = self.conn.execute("SELECT * FROM run_metrics WHERE kind=? AND run_id=?", (kind, item["id"])).fetchone()
                metric = dict(metric) if metric else None
                model = item.get("model") or ""
                if metric and not model:
                    spans = json.loads(metric["model_spans"])
                    model = next((s.get("model", "") for s in reversed(spans) if s.get("model")), "")
                started = metric["started_at_ms"] if metric else item.get("started_at_ms")
                finished = metric["finished_at_ms"] if metric else item.get("finished_at_ms")
                duration = max(0, finished - started) if started is not None and finished is not None else None
                steps_table, field = ("agent_steps", "turn_id") if kind == "turn" else ("worker_steps", "task_id")
                tools, errors, models = self.conn.execute(f"SELECT COUNT(CASE WHEN kind!='model' THEN 1 END),COUNT(CASE WHEN outcome NOT IN ('ok','running') THEN 1 END),COUNT(CASE WHEN kind='model' THEN 1 END) FROM {steps_table} WHERE {field}=?", (item["id"],)).fetchone()
                routine_id = item.get("routine_id")
                routine = self.conn.execute("SELECT title FROM routines WHERE id=?", (routine_id,)).fetchone() if routine_id else None
                rows.append({"kind": kind, "id": item["id"], "at_ms": item["created_at_ms"], "started_at_ms": started,
                             "duration_ms": duration, "member_id": mid, "member_name": member, "avatar": avatar,
                             "title": item["question"] if kind == "turn" else (routine[0] if routine else item["text"]),
                             "model": model, "provider": self._provider(model), "routine": routine[0] if routine else "",
                             "cost_usd": item["cost_usd"], "tokens": metric["tokens"] if metric else None,
                             "ai_steps": max(metric["ai_steps"], models) if metric else models, "tools": tools,
                             "errors": errors + (int(item.get("status", item.get("state")) in ("error", "failed")) if not errors else 0)})
        if not only or only[0] == "routine":
            query = "SELECT rr.*,r.title,r.member_id FROM routine_runs rr JOIN routines r ON r.id=rr.routine_id WHERE rr.started>=? AND rr.started<?"
            args = (lower, upper)
            if only:
                query += " AND rr.id=?"
                args += (only[1],)
            for raw in self.conn.execute(query, args):
                record = dict(raw)
                source_kind, source = self._routine_source(record)
                backing = self._rows(0, 2**53, (source_kind, source["id"])) if source and source_kind == "task" else []
                # A routine turn is excluded from regular runs; collect its metadata directly.
                if source and source_kind == "turn":
                    metric = self.conn.execute("SELECT * FROM run_metrics WHERE kind='turn' AND run_id=?", (source["id"],)).fetchone()
                    steps = self.conn.execute("SELECT COUNT(CASE WHEN kind!='model' THEN 1 END), COUNT(CASE WHEN outcome NOT IN ('ok','running') THEN 1 END), COUNT(CASE WHEN kind='model' THEN 1 END) FROM agent_steps WHERE turn_id=?", (source["id"],)).fetchone()
                    backing = [{"tokens": metric["tokens"] if metric else None, "ai_steps": max(metric["ai_steps"],steps[2]) if metric else steps[2], "tools": steps[0], "errors": steps[1], "model": source.get("model") or ""}]
                base = backing[0] if backing else {}
                started, finished = record["started"], record["finished"]
                member, avatar = members.get(record["member_id"], (str(record["member_id"]), ""))
                model = base.get("model", "")
                rows.append({**base, "kind": "routine", "id": record["id"], "at_ms": started, "started_at_ms": started,
                             "duration_ms": max(0, finished-started) if finished is not None else base.get("duration_ms"),
                             "member_id": record["member_id"], "member_name": member, "avatar": avatar,
                             "title": record["title"], "routine": record["title"], "status": record["status"],
                             "model": model, "provider": self._provider(model),
                             "cost_usd": record["cost"] if finished is not None else (source["cost_usd"] if source else record["cost"]),
                             "tokens": base.get("tokens"), "ai_steps": base.get("ai_steps",0), "tools": base.get("tools",0),
                             "errors": base.get("errors",0) or int(record["status"] in ("error","failed"))})
        return sorted(rows, key=lambda r: (-r["at_ms"], r["kind"], -r["id"]))

    @staticmethod
    def summary(rows):
        return {"duration_ms": sum(r["duration_ms"] or 0 for r in rows),
                "cost_usd": sum(r["cost_usd"] for r in rows), "tokens": sum(r["tokens"] or 0 for r in rows),
                **{key: sum(r[key] for r in rows) for key in ("ai_steps", "tools", "errors")}}

    async def read(self, params):
        period, zone, start, end, lower, upper = self._period(params)
        offset, limit = params.get("offset", 0), params.get("limit", 50)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise invalid_params("Invalid run page")
        rows = self._rows(lower, upper)
        groups = {}
        for grouping in ("member", "model", "day", "provider", "routine"):
            values = defaultdict(lambda: {"tokens": 0, "cost_usd": 0, "runs": 0})
            for row in rows:
                label = (row["member_name"] if grouping == "member" else
                         datetime.fromtimestamp(row["at_ms"] / 1000, zone).date().isoformat() if grouping == "day" else row[grouping]) or "unknown"
                entry = values[label]
                entry["tokens"] += row["tokens"] or 0
                entry["cost_usd"] += row["cost_usd"]
                entry["runs"] += 1
            groups[grouping] = sorted([{"label": label, **value} for label, value in values.items()], key=lambda v: (-v["tokens"], -v["cost_usd"], v["label"]))
        return {"range": period, "start_day": start.isoformat() if start else None, "end_day": (end-timedelta(days=1)).isoformat(),
                "summary": self.summary(rows), "groups": groups, "runs": rows[offset:offset+limit], "total": len(rows)}

    async def trace(self, params):
        kind, ident = params.get("kind"), params.get("id")
        if kind not in ("turn", "task", "routine") or type(ident) is not int or ident <= 0:
            raise invalid_params("Expected a run kind and id")
        run = next((r for r in self._rows(0, 2**53, (kind, ident)) if r["id"] == ident and r["kind"] == kind), None)
        if run is None: raise invalid_params("Run no longer exists")
        if kind == "routine":
            record = self.conn.execute("SELECT * FROM routine_runs WHERE id=?", (ident,)).fetchone()
            source_kind, source = self._routine_source(record)
            if not source:
                return {"run": run, "summary": self.summary([run]), "spans": []}
            kind, ident = source_kind, source["id"]
        metric = self.conn.execute("SELECT model_spans FROM run_metrics WHERE kind=? AND run_id=?", (kind, ident)).fetchone()
        spans = [{**s, "kind": "model", "tool": "", "server": s.get("model")} for s in json.loads(metric[0])] if metric else []
        table, field = ("agent_steps", "turn_id") if kind == "turn" else ("worker_steps", "task_id")
        for step in self.conn.execute(f"SELECT at_ms,kind,tool,server,outcome,ms FROM {table} WHERE {field}=? ORDER BY n", (ident,)):
            data = dict(step)
            if data["kind"] == "model" and metric and data["outcome"] == "ok": continue
            spans.append(data)
        spans.sort(key=lambda s: (s["at_ms"] - s["ms"], s["at_ms"]))
        return {"run": run, "summary": self.summary([run]), "spans": spans}

    async def export(self, params):
        _, _, _, _, lower, upper = self._period(params)
        columns = ["time", "member", "title", "model", "provider", "duration_ms", "tokens", "cost_usd", "ai_steps", "tools", "errors"]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, columns)
        writer.writeheader()
        for row in self._rows(lower, upper):
            values = {**row, "time": datetime.fromtimestamp(row["at_ms"]/1000, UTC).isoformat(), "member": row["member_name"]}
            safe = {key: values.get(key) for key in columns}
            # Quoting alone does not stop formula evaluation when a spreadsheet opens a CSV.
            safe = {k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else v) for k,v in safe.items()}
            writer.writerow(safe)
        return {"csv": output.getvalue()}
