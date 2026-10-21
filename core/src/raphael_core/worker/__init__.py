"""The background worker's side: what runs in the worker process, and nothing else.

The worker is a process of its own (`raphael-core.exe --worker`; `python -m raphael_core --worker`
in development) that the core starts when the first background task comes. It carries a task out
in a loop (`loop`): ask the model what to do, make the calls it asks for, look at the results, ask
again, until the model reports. It has no keys, no database and no connection to any plugin: the
only thing it can do is ask the core, over its stdin and stdout (`channel`), to make a model call
(`host.chat`) or a tool call (`host.tool`). The core is the one gate: it holds the key, writes the
spend, checks the tier of every tool at the moment of the call, counts the limits and writes the
audit (`raphael_core.background.host`). So a worker that goes wrong, or is led astray by what a tool
returned, can ask for things and be refused, and can do nothing else.

That is also why this package imports nothing of the core's other side. A test keeps it so: no
module here, and nothing they import, reaches `keyring`, `raphael_core.brain.secrets`,
`raphael_core.integrations.vault` or `sqlite3`.

Modules: `channel` (JSON-RPC both ways over lines), `guards` (what a tool result is wrapped in, how
it is cut, the guard against repeated calls), `loop` (the loop itself), `runner` (the process).

Nothing here logs the task, a prompt, an argument or a result.
"""
