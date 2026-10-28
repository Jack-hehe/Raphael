"""Plugins: MCP servers the user connects, and which of their tools the assistant may use.

See `service` for the RPC methods and how the assistant reaches the tools; the other modules are
the parts it uses:

- `config` keeps the servers, the tool lists they reported and the tools the user turned off, in
  a JSON file that never holds a secret.
- `vault` keeps the secrets (environment values and headers) in Windows Credential Manager.
- `client` talks to a server: connects on demand, lists tools, calls one with a timeout and a limit
  on the size of what comes back.
- `audit` writes down every call: when, which tool, at which tier, who approved, how it ended.
"""
