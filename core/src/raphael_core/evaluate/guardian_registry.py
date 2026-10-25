"""Source inventory and fail-closed joins for live tool coverage.

An offer is not an inventory: enumerate registration sources before shortlisting,
then compare the real API and Raphael MCP projections. No discovery call counts
as execution evidence. Reports contain digests, never tool arguments or results.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from ..agent.cli.loop import APPROVE, MEMORY_TOOLS
from ..agent.harness_tools import definitions_for
from ..agent.mcp_server import RaphaelMcp
from ..agent.plugins import make_offer, tool_key
from ..agent.policy import Tier
from ..agent.speaker import Speaker
from ..agent.team import TEAM_TOOLS, worker_definition
from ..background.snapshot import _DEFINITIONS, take_snapshot
from ..computer.tools import definitions as computer_definitions

MANIFEST = Path(__file__).with_name("guardian_manifest.json")
BRAINS = ("C", "A")


def external_identity(provider, server, operation):
    return "mcp/" + "/".join(quote(value, safe="") for value in (provider, server, operation))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass
class RegisteredTool:
    tool_key: str
    origin: str
    provider: str
    app_or_server_id: str
    operation: str
    schemas: dict = field(default_factory=dict)
    api_names: list = field(default_factory=list)
    mcp_names: list = field(default_factory=list)
    roles: list = field(default_factory=list)
    auth_modes: list = field(default_factory=lambda: ["none"])
    enabled_state: str = "enabled"
    unavailable_reason: str = ""
    execution_keys: list = field(default_factory=list)

    @property
    def schema_hash(self):
        return digest(self.schemas)

    def report(self, mapping=None):
        return {**asdict(self), "schema_hash": self.schema_hash,
                "scenario_ids": (mapping or {}).get("scenario_ids", []),
                "expected_assertions": (mapping or {}).get("expected_assertions", [])}


async def inventory(servers=()):
    """Union registration sources, role/context offers and the production MCP listing.

    `servers` is the full ConfigStore snapshot, including disabled/lazy tools.
    Remote catalogs must supply their complete persisted server snapshots; an
    unavailable catalog is reported by the suite rather than silently omitted.
    """
    from ..agent.plugins import PluginTool

    records = {}

    def register(key, name, schema, role, *, origin="builtin", provider="raphael",
                 server="core", auth="none", enabled=True):
        item = records.setdefault(key, RegisteredTool(key, origin, provider, server, name, auth_modes=[auth]))
        item.schemas[role] = schema
        if role not in item.roles:
            item.roles.append(role)
        item.auth_modes = sorted(set([*item.auth_modes, auth])) if origin != "builtin" else [auth]
        if not enabled:
            item.enabled_state = "disabled"
            item.unavailable_reason = "registered but disabled"
        return item

    def builtin(name):
        return "raphael/core/" + name

    for role, member in (("chief", 0), ("member", 1)):
        for name, item in {**_DEFINITIONS, **dict(definitions_for(member))}.items():
            register(builtin(name), name, item["parameters"], role)
        for name in TEAM_TOOLS:
            if name == "create_member" and member:
                continue
            definition = worker_definition(name)["function"]
            register(builtin(name), name, definition["parameters"], role)
        for definition in MEMORY_TOOLS + [APPROVE]:
            function = definition["function"]
            register(builtin(function["name"]), function["name"], function["parameters"], role)
        for definition in computer_definitions():
            function = definition["function"]
            register(builtin(function["name"]), function["name"], function["parameters"], role)

    plugins = []
    external_keys = {}
    for server in servers:
        for tool in server.tools:
            source_key = tool_key(server.id, tool.name)
            provider = server.managed_by or "mcp"
            key = external_identity(provider, server.id, tool.name)
            external_keys[source_key] = key
            item = register(key, tool.name, tool.input_schema, "chief", origin="plugin", provider=provider,
                            server=server.id, auth=server.auth, enabled=server.enabled and server.tool_on(tool))
            item.execution_keys = [source_key]
            register(key, tool.name, tool.input_schema, "member", origin="plugin", provider=provider,
                     server=server.id, auth=server.auth, enabled=server.enabled and server.tool_on(tool))
            plugins.append(PluginTool(source_key, server.name, tool.name, tool.description, tool.input_schema, Tier.T1))

    # Full registration offer deliberately bypasses enabled/top-K filters for the
    # denominator. The actual execution gate still uses the live grants.
    class AllRegistered:
        def offered(self):
            return plugins

    alias_keys = {alias: external_keys[tool.key] for alias, tool in make_offer(plugins).by_alias.items()}
    for role, member in (("chief", 0), ("member", 1)):
        snapshot = take_snapshot(AllRegistered(), memory=True, attachments=True,
                                 speaker=Speaker(id=member, name="Fixture", role="Assistant"),
                                 team=TEAM_TOOLS if not member else ("ask_member",),
                                 interactive=True, max_tier=Tier.T3)
        if snapshot.pool:
            snapshot.show(snapshot.pool.by_alias)
        api = snapshot.definitions
        # find_tools is registered even when a small fixture has no hidden tools.
        conditional = [] if any(d["function"]["name"] == "find_tools" for d in api) else [
            {"type": "function", "function": {"name": "find_tools", **_DEFINITIONS["find_tools"]}}]
        api = [*api, *conditional]
        api = [*api, *computer_definitions()]
        mcp = RaphaelMcp(lambda: [*snapshot.definitions, *conditional, *MEMORY_TOOLS, APPROVE], None)
        listing = (await mcp._dispatch({"method": "tools/list"}))["result"]["tools"]
        for surface, definitions in (("api", [d["function"] for d in api]), ("mcp", listing)):
            for definition in definitions:
                name = definition["name"]
                key = alias_keys.get(name, builtin(name))
                item = records[key]
                names = item.api_names if surface == "api" else item.mcp_names
                if name not in names:
                    names.append(name)
                schema = definition.get("parameters", definition.get("inputSchema"))
                registered = item.schemas.get(role, item.schemas.get("chief"))
                if schema != registered:
                    item.unavailable_reason = "surface schema drift"
    return sorted(records.values(), key=lambda item: item.tool_key)


def load_manifest(path=MANIFEST):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("version") != 1 or not isinstance(value.get("tools"), dict):
        raise ValueError("invalid_tool_manifest")
    return value["tools"]


def coverage_guard(registry, mapping, calls=(), *, scenario_ids=(), run_id="", head="", models=None):
    """Return every missing mapping, surface, schema and verified brain/cell call.

    Evidence is admitted only for this run/HEAD/model and a mapped cell that
    passed its observable assertions. A denied call or receipt alone never counts.
    """
    errors, rows = [], []
    if not registry:
        errors.append({"tool_key": None, "reason": "empty_registry"})
    known = {item.tool_key for item in registry}
    for stale in sorted(set(mapping) - known):
        errors.append({"tool_key": stale, "reason": "mapping_without_registered_tool"})
    for item in registry:
        entry = mapping.get(item.tool_key, {})
        required = entry.get("scenario_ids", [])
        assertions = entry.get("expected_assertions", [])
        reasons = []
        if not isinstance(required, list) or not isinstance(assertions, list):
            required, assertions = [], []
            reasons.append("invalid_mapping")
        if not required or not assertions:
            reasons.append("missing_mapping")
        if scenario_ids and any(cell not in scenario_ids for cell in required):
            reasons.append("unknown_scenario_cell")
        if not item.api_names or not item.mcp_names:
            reasons.append("missing_surface")
        if item.unavailable_reason == "surface schema drift":
            reasons.append("schema_drift")
        for reason in reasons:
            errors.append({"tool_key": item.tool_key, "reason": reason})
        for brain in BRAINS:
            for scenario in required or [None]:
                verified = any(
                    call.get("tool_key") == item.tool_key and call.get("brain") == brain
                    and call.get("scenario_id") == scenario and call.get("schema_hash") == item.schema_hash
                    and call.get("run_id") == run_id and call.get("head") == head
                    and (models is None or call.get("requested_model") == models[brain])
                    and call.get("source") == "production_loop" and call.get("gate_passed") is True
                    and call.get("outcome") == "ok" and call.get("cell_status") == "PASS"
                    and set(assertions) <= set(call.get("assertions", []))
                    for call in calls
                )
                row = {"tool_key": item.tool_key, "schema_hash": item.schema_hash, "brain": brain,
                       "scenario_id": scenario, "status": "PASS" if verified and not reasons else "FAIL",
                       "reason": "; ".join(reasons or ([] if verified else ["missing_verified_call"]))}
                rows.append(row)
    return {"status": "FAIL" if errors or any(row["status"] != "PASS" for row in rows) else "PASS",
            "errors": errors, "results": rows,
            "missing": [row for row in rows if row["status"] != "PASS"]}
