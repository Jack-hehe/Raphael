"""A private skill's trigger, through the production loops of both brains: with the skill on, the member is told when to
use it and is offered the tool that reads it; once the owner deletes it, the next turn is told nothing of it and is
offered no such tool, a brain that remembers it and reads it by name gets nothing, and one that was already reading
it when the owner deleted it gets nothing either. Editing it changes what the next read returns, at once."""
import pytest

from card_brains import question_of
from p15a_brains import Stage

PROMPT = "請依照技能整理這串信的摘要。"
WHEN = "Use when the owner asks for a summary of a thread"
DOCUMENT = f"---\nname: summary-style\ndescription: {WHEN}\n---\nStart with the verdict. Code: SK-4417."
EDITED = DOCUMENT.replace("SK-4417", "SK-9020")


def act(user, tools, results):
    """It reads the skill by name whenever it is asked for the summary (offered the tool or not, as a brain that remembers
    it would) and says only what the skill said."""
    if "整理這串信的摘要" not in question_of(user):
        return "我不確定要怎麼做。", []
    if not results:
        return "", [("skill_read", {"name": "summary-style"})]
    code = next((word for word in results[-1].replace(".", " ").split() if word.startswith("SK-")), "")
    return (f"依照技能，驗證碼是 {code}。" if code else "沒有可用的技能，我直接整理。"), []


class Owner(Stage):
    def __init__(self, tmp_path, monkeypatch, brain):
        super().__init__(tmp_path, monkeypatch, brain, act)

    async def setup(self):
        self.member = (await self.driver.seed_team())[0]
        self.skill = self.app.members.capabilities.keep_skill(self.member, "owner/repo", DOCUMENT)

    async def ask(self, session):
        return await self.driver.ask(PROMPT, member=self.member, session=session)

    async def delete(self):
        await self.app.call("members.skills.delete", {"member_id": self.member, "id": self.skill["id"]})


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_a_skill_that_is_on_is_told_of_offered_and_read(tmp_path, monkeypatch, brain):
    owner = Owner(tmp_path, monkeypatch, brain)
    try:
        await owner.setup()
        result = await owner.ask("on")
        assert "skill_read" in owner.offered[-1] and WHEN in owner.prompts[-1]
        assert "SK-4417" in result["text"]
        # The owner edits the document: the next turn reads the new one, and its words are the ones the model is told.
        await owner.app.call("members.skills.update", {"member_id": owner.member, "id": owner.skill["id"], "text": EDITED})
        assert "SK-9020" in (await owner.ask("edited"))["text"]
    finally:
        await owner.aclose()


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_a_deleted_skill_is_not_told_of_not_offered_and_not_readable_by_a_brain_that_remembers_it(
    tmp_path, monkeypatch, brain
):
    owner = Owner(tmp_path, monkeypatch, brain)
    try:
        await owner.setup()
        assert "SK-4417" in (await owner.ask("on"))["text"]
        await owner.delete()
        result = await owner.ask("deleted")
        assert "skill_read" not in owner.offered[-1] and WHEN not in owner.prompts[-1]
        assert "SK-" not in result["text"] and "沒有可用的技能" in result["text"]
        assert not owner.app.members.capabilities._path(owner.member, owner.skill["id"]).exists()
    finally:
        await owner.aclose()


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_a_skill_deleted_while_the_model_is_using_it_gives_it_nothing(tmp_path, monkeypatch, brain):
    owner = Owner(tmp_path, monkeypatch, brain)
    try:
        await owner.setup()
        owner.after_listing(owner.delete)
        result = await owner.ask("in-flight")
        # It was told of the skill and offered the tool; by the time it read it, the owner had deleted it.
        assert "skill_read" in owner.offered[-1] and WHEN in owner.prompts[-1]
        assert "SK-" not in result["text"] and "沒有可用的技能" in result["text"]
    finally:
        await owner.aclose()
