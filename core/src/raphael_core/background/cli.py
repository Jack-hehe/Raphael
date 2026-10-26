"""Adapt the existing CLI member loop to the background worker's tool/approval contract."""
from types import SimpleNamespace

from ..agent.cli.loop import CliLoop
from ..protocol import RpcError
from ..worker.loop import Stop, ToolOutcome


class RoomCliSessions:
    """Room turns receive their full transcript and never resume a private member conversation."""
    def __init__(self):
        self.locks = {}
        self.conversations = SimpleNamespace(resolve=lambda member, conversation: conversation)

    def get(self, *args):
        return None

    def put(self, *args):
        pass


def member_loop(brains, quota, task, run, spec, *, wait_s, progress):
    async def execute(call_id, name, arguments):
        if name == "cli_permission":
            # Native shell/file permission is not an alternative to Raphael's tool gate.
            return ToolOutcome(False, "use_mcp_tools")
        try:
            result = await run.tool({"call_id":call_id, "name":name, "arguments":arguments})
        except RpcError as error:
            data = error.data if isinstance(error.data, dict) else {}
            if data.get("stop"):
                raise Stop(data["stop"], data.get("detail")) from None
            raise
        return tool_outcome(result)

    return CliLoop(brains=brains, sessions=RoomCliSessions(), member_id=task.member_id,
        conversation_id=f"room-task-{task.id}", model=run.model, ledger=run.ledger,
        language=task.language, plain=True, system=spec["system"], user=spec["user"],
        tools=spec["tools"], attachments=run.attachments, progress=progress,
        thinking=lambda text: None, event=progress, execute=execute, quota=quota, wait_timeout_s=wait_s)


def tool_outcome(result):
    if isinstance(result, str):
        return ToolOutcome('status="ok"' in result, content=result)
    return ToolOutcome(bool(result.get("ok")), result.get("kind"), result.get("content", ""),
                       instruction=result.get("instruction") or "")
