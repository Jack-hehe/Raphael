"""A tool the owner switched off, or an app they uninstalled, is never offered to a brain and never called by one
again, through the production loops of both brains (the API loop and the authenticated MCP of a CLI), and
whatever the owner said before: their 一律允許 on that tool waives its questions and nothing more. The brains here
are stubborn: they remember the tool from earlier in the conversation and call it by name, offered or not."""
import pytest

from raphael_core.brain.settings import SettingsStore

from card_brains import question_of
from p15a_brains import Stage

PROMPT = "請用 Guardian 寄出測試信。"
SENT = "測試信已經寄出了。"
WRITE = "Guardian__write"


def act(user, tools, results):
    """An honest brain that remembers the tool: it calls it by name whether or not it was offered this turn, and
    says only what came back."""
    if "寄出測試信" not in question_of(user):
        return "我不確定要怎麼做。", []
    if not results:
        return "", [(WRITE, {})]
    return (SENT if "test_mode" in results[-1] else "這封信沒有寄出。"), []


class Owner(Stage):
    """What the owner does in the window and in the chat, on one production App with the brain of the test."""

    def __init__(self, tmp_path, monkeypatch, brain):
        super().__init__(tmp_path, monkeypatch, brain, act)

    async def setup(self):
        await self.driver.install_permission_fixture()
        SettingsStore(self.app._conn).set("action_mode", "ask")
        self.app.safety_rules.ensure_seeded("zh-TW")
        for rule in self.app.safety_rules.rules():
            self.app.safety_rules.delete(rule.id)

    def sends(self):
        return [receipt["operation"] for receipt in self.driver.recorder.receipts if receipt["operation"] == "write"]

    def blocked(self):
        return [trace for trace in self.driver.traces if trace["name"].endswith("/write") and trace["outcome"] == "blocked"]

    async def always(self):
        """The first send is asked about, and the owner says 一律允許."""
        asked = await self.driver.ask(PROMPT, session="first")
        card = ((await self.driver.turn_of(0, asked.get("turn_id"))).get("permissions") or [None])[0]
        assert asked.get("status") == "confirm" and card and card["always"] and not self.sends()
        await self.driver.confirm(asked, True, always=True, card_digest=card["digest"])
        assert self.sends() == ["write"]
        assert [rule.scope for rule in self.app.safety_rules.rules() if rule.scope] == ["tool:mcp:guardian-mail/write"]
        # After which that tool needs no card.
        free = await self.driver.ask(PROMPT, session="free")
        assert free.get("status") == "done" and self.sends() == ["write", "write"]

    async def switch_off(self):
        await self.app.call("integrations.set_tool_enabled", {"id": "guardian-mail", "tool": "write", "enabled": False})

    async def uninstall(self):
        await self.app.call("integrations.disconnect", {"id": "guardian-mail"})


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_a_tool_switched_off_while_the_model_thinks_is_refused_after_always(tmp_path, monkeypatch, brain):
    owner = Owner(tmp_path, monkeypatch, brain)
    try:
        await owner.setup()
        await owner.always()
        owner.after_listing(owner.switch_off)
        result = await owner.driver.ask(PROMPT, session="in-flight")
        # The tool was on when the brain was told what it may use, and off when it called it: the gate refused it,
        # with no question to the owner (Always waives questions, never a switch) and no send.
        assert WRITE in owner.offered[-1]
        assert result.get("status") == "done" and SENT not in result["text"]
        assert owner.sends() == ["write", "write"] and len(owner.blocked()) == 1
    finally:
        await owner.aclose()


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_a_tool_switched_off_is_not_offered_to_the_next_turn_and_a_remembered_call_reaches_nothing(
    tmp_path, monkeypatch, brain
):
    owner = Owner(tmp_path, monkeypatch, brain)
    try:
        await owner.setup()
        await owner.always()
        await owner.switch_off()
        result = await owner.driver.ask(PROMPT, session="next")
        # Offered: the other tool of the connection, not this one. The brain called this one anyway, by name.
        assert owner.offered[-1] and not any(name.endswith("__write") for name in owner.offered[-1])
        assert any(name.endswith("__send_copy") for name in owner.offered[-1])
        assert result.get("status") == "done" and SENT not in result["text"] and owner.sends() == ["write", "write"]
        # Switched on again, it is offered again (the owner's switch is the only thing that decides).
        await owner.app.call("integrations.set_tool_enabled", {"id": "guardian-mail", "tool": "write", "enabled": True})
        await owner.driver.ask(PROMPT, session="again")
        assert WRITE in owner.offered[-1]
    finally:
        await owner.aclose()


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_an_uninstalled_app_is_not_offered_not_called_not_counted_and_not_in_the_roster(tmp_path, monkeypatch, brain):
    owner = Owner(tmp_path, monkeypatch, brain)
    try:
        await owner.setup()
        await owner.always()
        before = owner.app.members.team_context(0, "zh-TW")
        assert (before["connected"], [app["name"] for app in before["apps"]]) == (1, ["Guardian"])
        await owner.uninstall()
        result = await owner.driver.ask(PROMPT, session="gone")
        assert not any(name.startswith("Guardian__") for name in owner.offered[-1])
        assert result.get("status") == "done" and SENT not in result["text"] and owner.sends() == ["write", "write"]
        # Every place that shows the apps says the same: the roster of a model, the page and the sidebar's count.
        after = owner.app.members.team_context(0, "zh-TW")
        inventory = await owner.app.call("integrations.inventory")
        assert (after["connected"], after["apps"], after["inventory_state"]) == (0, [], "complete")
        assert (inventory["count"], inventory["apps"]) == (0, []) and after["revision"] != before["revision"]
        # And one that is in flight when it goes (the brain was told of it, then the owner uninstalled it) is refused too.
        await owner.driver.install_permission_fixture()
        owner.after_listing(owner.uninstall)
        in_flight = await owner.driver.ask(PROMPT, session="gone-in-flight")
        assert WRITE in owner.offered[-1] and SENT not in in_flight["text"] and owner.sends() == ["write", "write"]
        assert len(owner.blocked()) == 1
    finally:
        await owner.aclose()
