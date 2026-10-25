# Raphael architecture and checked boundaries

The Windows Tauri shell owns windows, overlay, global hotkeys, audio/paste and the
Python sidecar lifecycle. The TypeScript main window uses `app/src/main/api.ts`
to call the shell's allowlisted commands and `core_call` bridge. The Python core
registers JSON-RPC methods on `Router`; notifications return through the shell's
main/overlay event channels. Shell-only window metadata and hotkey forwarding are
explicit native exceptions, not alternate JSON-RPC clients.

`AgentService` is the shared conversational implementation for Raphael (member 0)
and `Speaker` members. Members store identity/resources/routines; rooms select
recipients and use that same agent. Background services persist tasks and supply
the worker's guarded `run_task` loop. Connectors own account state and scoped MCP
tool availability. Tool snapshots share production definitions and tool gates
remain authoritative at execution. API providers implement `ModelProvider` and
use `NarratedLoop`; subscriptions use `CliLoop` and the single `RaphaelMcp` server.
The evaluator replaces only execution with a recorder, not the loops or schemas.

SQLite (`brain/db.py`, versioned migrations) owns settings, history, members,
rooms, routines, tasks and spend. Attachment and memory stores own bounded file
content. OS credential storage owns secrets; hosted Actions alone may supply an
ephemeral `OPENROUTER_API_KEY` through the evaluator credential adapter. UI fixture
state is synthetic and never proves native persistence or provider acceptance.

Every mechanical row names an executable test. Human rows require architecture
review and are not presented as automatically proven. Settings semantic duplication
and arbitrary hard-coded capability lists require review as well as the narrow
mechanical boundaries below.

| ID | Invariant | Check | Evidence |
| --- | --- | --- | --- |
| A-01 | Member turns use AgentService rather than a second model loop in members or rooms. | pytest | core/tests/architecture/test_boundaries.py::test_members_and_rooms_do_not_create_brain_loops |
| A-02 | Team administration definitions are offered only to member 0. | pytest | core/tests/architecture/test_boundaries.py::test_administration_is_raphael_only |
| A-03 | API and CLI conversation paths consume the shared production snapshot; live evaluation uses those loops and definitions. | pytest | core/tests/architecture/test_boundaries.py::test_shared_loop_and_definition_sources |
| A-04 | CLI conversations use one authenticated RaphaelMcp implementation; no alternate MCP server in agent CLI or evaluation. | pytest | core/tests/architecture/test_boundaries.py::test_one_mcp_server |
| A-05 | Secrets use the OS credential store; only hosted evaluator CI can read its ephemeral Actions key, and failures never report credential values. | pytest | core/tests/architecture/test_boundaries.py::test_evaluator_credentials_are_ci_scoped; core/tests/brain/test_openrouter.py::test_a_key_provider_failure_is_logged_by_exception_type_only; core/tests/evaluate/test_guardian.py::test_provider_error_is_classified_without_secret_text |
| A-06 | Main-window core RPC crosses api.ts only; window-hotkeys.ts may forward the shell-only window_hotkey command. | vitest | app/src/architecture.test.ts::core IPC has one frontend boundary |
| A-07 | Main-window source contains no native selects; rendered checks also reject native time inputs. | vitest | app/src/architecture.test.ts::main templates never introduce native selects |
| A-08 | Settings keys extend the single DEFAULTS registry without duplicate literal keys. | pytest | core/tests/architecture/test_boundaries.py::test_settings_have_one_unique_registry |
| A-09 | Open is a generic target_kind/target action rather than a per-site enum. | pytest | core/tests/architecture/test_boundaries.py::test_open_has_no_site_allowlist |
| A-10 | Literal frontend RPC calls are shell-allowlisted, and every named shell RPC method has a registered production core handler. | pytest, vitest | core/tests/architecture/test_boundaries.py::test_shell_named_rpc_methods_have_registered_core_handlers; app/src/architecture.test.ts::literal frontend RPC calls are allowed by the shell |
| A-11 | State belongs to the existing stores and migrations; no duplicate settings UI or persistent state registry. | human | Architecture and diff review |
| A-12 | Core execution remains independently gated for members, rooms, background work and connectors. | human | Native journeys and service/gate tests |
| A-13 | Every mechanical architecture and product rule links an existing executable test. | vitest | app/src/architecture.test.ts::mechanical rules link existing named tests |
| A-14 | Native notifications retain their main/overlay channel contracts and behavior. | human | Native notification journeys |
