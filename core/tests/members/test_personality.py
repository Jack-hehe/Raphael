"""A member's character is persistent, editable and separate from its work instructions."""

import pytest

from raphael_core.brain import db
from raphael_core.members.store import MemberStore
from .test_service import refused


async def test_personality_round_trips_through_create_update_list_and_speaker(rig, caplog):
    made = (await rig.call("members.create", {
        "name": "Teacher", "role": "Japanese", "personality": "  PRIVATE-STYLE\nStrict but patient.  ",
        "instructions": "Teach N1 grammar.",
    }))["member"]
    assert made["personality"] == "PRIVATE-STYLE\nStrict but patient."
    changed = (await rig.call("members.update", {"id": made["id"], "personality": "Warm and direct."}))["member"]
    assert changed["personality"] == "Warm and direct."
    assert changed["instructions"] == "Teach N1 grammar." and changed["role"] == "Japanese"
    listed = (await rig.call("members.list"))["members"]
    assert listed[1]["personality"] == "Warm and direct."
    assert rig.service.speaker(made["id"]).personality == "Warm and direct."
    cleared = (await rig.call("members.update", {"id": made["id"], "personality": ""}))["member"]
    assert cleared["personality"] == ""
    assert "PRIVATE-STYLE" not in caplog.text


def test_v18_members_migrate_with_empty_personality_and_keep_existing_data(tmp_path, monkeypatch):
    path = tmp_path / "v18.db"
    with monkeypatch.context() as old:
        old.setattr(db, "LATEST_VERSION", 18)
        conn = db.connect(path)
        cursor = conn.execute(
            "INSERT INTO members (name, role, instructions, position, created_at_ms) VALUES (?, ?, ?, 0, 1)",
            ("Teacher", "Japanese", "Teach N1."),
        )
        original_id = cursor.lastrowid
        conn.commit()
        conn.close()
    conn = db.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19
        store = MemberStore(conn)
        migrated = store.member(original_id)
        assert migrated.personality == "" and migrated.instructions == "Teach N1."
        store.update_member(original_id, personality="Strict and concise.")
    finally:
        conn.close()
    conn = db.connect(path)
    try:
        assert MemberStore(conn).speaker(original_id).personality == "Strict and concise."
    finally:
        conn.close()


@pytest.mark.parametrize("value", [None, 12, "x" * 2001, "private\0style"], ids=["null", "number", "long", "control"])
async def test_invalid_personality_is_rejected_without_changing_other_fields(rig, value):
    assert await refused(rig, "members.create", {"name": "Invalid", "personality": value}) == "personality"
    made = (await rig.call("members.create", {"name": "Teacher"}))["member"]
    assert made["personality"] == ""
    assert await refused(rig, "members.update", {"id": made["id"], "personality": value, "role": "Changed"}) == "personality"
    [_, saved] = (await rig.call("members.list"))["members"]
    assert saved["personality"] == "" and saved["role"] == ""
