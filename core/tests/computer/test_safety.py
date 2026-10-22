"""The rules a step on the owner's desktop is judged by: hand-off, a yes first, and the keys."""

import re
from pathlib import Path

import pytest

from raphael_core.computer import safety
from raphael_core.computer.safety import CONFIRM, HANDOFF, KeysRefused, judge, parse_chord


@pytest.mark.parametrize(
    "text, chord",
    [
        ("ctrl+s", ("ctrl", "s")),
        ("Ctrl+Shift+Tab", ("ctrl", "shift", "tab")),
        ("shift+ctrl+tab", ("ctrl", "shift", "tab")),
        ("enter", ("enter",)),
        ("Esc", ("escape",)),
        ("alt+f", ("alt", "f")),
        ("ctrl+1", ("ctrl", "1")),
        ("PgDn", ("pagedown",)),
        ("F5", ("f5",)),
        ("shift+tab", ("shift", "tab")),
        ("control+z", ("ctrl", "z")),
    ],
)
def test_a_chord_of_the_allowlist_is_read_in_one_spelling(text, chord):
    assert parse_chord(text) == chord


@pytest.mark.parametrize(
    "text, why",
    [
        ("win+l", "windows"),
        ("Win+R", "windows"),
        ("meta+d", "windows"),
        ("ctrl+win+right", "windows"),
        ("alt+tab", "system"),
        ("alt+shift+tab", "system"),
        ("alt+f4", "system"),
        ("ctrl+alt+delete", "system"),
        ("ctrl+shift+esc", "system"),
        ("ctrl+esc", "system"),
        ("alt+space", "system"),
        ("ctrl+alt+t", "system"),
        ("a", "typing"),
        ("shift+a", "typing"),
        ("7", "typing"),
        ("", "bad"),
        ("ctrl+", "bad"),
        ("ctrl", "bad"),
        ("ctrl+ctrl+s", "bad"),
        ("ctrl+f13", "bad"),
        ("hyper+s", "bad"),
        ("printscreen", "bad"),
        (None, "bad"),
        (42, "bad"),
        ("ctrl+" * 20 + "s", "bad"),
    ],
)
def test_a_chord_outside_the_allowlist_is_refused_and_says_why(text, why):
    with pytest.raises(KeysRefused) as refused:
        parse_chord(text)
    assert refused.value.why == why


def test_a_chord_is_written_the_way_the_owner_reads_it():
    assert safety.chord_text(("ctrl", "shift", "tab")) == "Ctrl+Shift+Tab"
    assert safety.chord_text(("ctrl", "s")) == "Ctrl+S"
    assert safety.chord_text(("enter",)) == "Enter"


@pytest.mark.parametrize(
    "name",
    ["Send", "傳送", "送出", "刪除", "削除", "Buy now", "購買", "注文する", "Publish", "回覆全部",
     "Delete permanently", "ＳＥＮＤ", "Place order", "Uninstall", "Submit form"],
)  # fmt: skip
def test_a_button_that_sends_deletes_or_buys_asks_first(name):
    assert judge(action="click", declared_final=False, name=name, role="Button") == CONFIRM


@pytest.mark.parametrize(
    "name",
    ["Sign in", "登入", "ログイン", "Pay now", "付款", "結帳", "お支払い", "Card number", "卡號",
     "CVV", "安全碼", "Verification code", "驗證碼", "Forgot password", "密碼", "One-time code",
     "one_time code"],
)  # fmt: skip
def test_a_sign_in_a_payment_or_a_code_is_the_owners(name):
    assert judge(action="click", declared_final=False, name=name, role="Button") == HANDOFF
    assert judge(action="type", declared_final=False, name=name, role="Edit") == HANDOFF


def test_the_shell_checks_the_live_focus_against_the_same_hand_off_words():
    policy = Path(__file__).resolve().parents[3] / "app" / "src-tauri" / "src" / "computer" / "policy.rs"
    source = policy.read_text(encoding="utf-8")

    def rust_list(name):
        body = re.search(rf"const {name}: &\[&str\] = &\[(.*?)\];", source, re.S)
        assert body, name
        return tuple(re.findall(r'"([^"]*)"', body.group(1)))

    assert rust_list("HANDOFF_EN") == safety._HANDOFF_EN
    assert rust_list("HANDOFF_CJK") == safety._HANDOFF_CJK


@pytest.mark.parametrize("name", ["七", "Seven", "Sender list", "Paypal", "Postcode", "清除", "Open"])
def test_an_ordinary_button_runs(name):
    assert judge(action="click", declared_final=False, name=name, role="Button") is None


def test_a_password_field_is_the_owners_whatever_it_is_called():
    assert judge(action="type", declared_final=False, name="Box", role="Edit", password=True) == HANDOFF
    assert judge(action="click", declared_final=True, name="x", role="Edit", password=True) == HANDOFF
    # typing or keys with no element reach the focused field
    assert judge(action="type", declared_final=False, focused_password=True) == HANDOFF
    assert judge(action="keys", declared_final=False, chord=("ctrl", "v"), focused_password=True) == HANDOFF


def test_what_the_model_declares_final_asks_first():
    assert judge(action="click", declared_final=True, name="七", role="Button") == CONFIRM
    assert judge(action="keys", declared_final=True, chord=("tab",)) == CONFIRM


def test_keys_that_send_or_delete_for_good_ask_first():
    assert judge(action="keys", declared_final=False, chord=("ctrl", "enter")) == CONFIRM
    assert judge(action="keys", declared_final=False, chord=("shift", "delete")) == CONFIRM
    assert judge(action="keys", declared_final=False, chord=("ctrl", "s")) is None


def test_enter_in_a_message_box_sends_and_asks_first():
    assert (
        judge(action="keys", declared_final=False, chord=("enter",), focused_name="輸入訊息",
              focused_role="Edit")  # fmt: skip
        == CONFIRM
    )
    assert judge(action="keys", declared_final=False, chord=("enter",), name="Message", role="Edit") == CONFIRM
    # Enter in a box that is no message box, or on a button, runs
    assert judge(action="keys", declared_final=False, chord=("enter",), focused_name="檔名", focused_role="Edit") is None
    assert judge(action="keys", declared_final=False, chord=("enter",), focused_name="Message", focused_role="Button") is None


@pytest.mark.parametrize("text", ["hi\nthere", "hi\r\n", "line\rline", "a b", "a b", "end\x0b", "a\x85b"])
def test_typing_a_line_break_may_press_enter_and_asks_first(text):
    # the shell types a line break as Enter, which sends a chat message or a form
    assert judge(action="type", declared_final=False, text=text) == CONFIRM
    assert judge(action="type", declared_final=False, name="檔名", role="Edit", text=text) == CONFIRM
    assert judge(action="type", declared_final=False, focused_name="Body", focused_role="Document", text=text) == CONFIRM


def test_typing_on_one_line_runs():
    assert judge(action="type", declared_final=False, name="Search", role="Edit", text="12*34\tok") is None


@pytest.mark.parametrize("name", ["Sign in", "Pay now", "Card number", "驗證碼", "ログイン"])
@pytest.mark.parametrize("chord", [("enter",), ("space",), ("ctrl", "v"), ("backspace",)])
def test_keys_on_the_focused_sign_in_payment_or_code_are_the_owners(name, chord):
    assert judge(action="keys", declared_final=False, chord=chord, focused_name=name, focused_role="Button") == HANDOFF
    # the element named is the one the keys reach (the shell focuses it first)
    assert judge(action="keys", declared_final=False, chord=chord, name=name, role="Edit") == HANDOFF


@pytest.mark.parametrize("name", ["Card number", "Verification code", "安全碼", "Sign in"])
def test_typing_into_the_focused_payment_field_or_code_is_the_owners(name):
    assert judge(action="type", declared_final=False, focused_name=name, focused_role="Edit", text="4111") == HANDOFF


@pytest.mark.parametrize("chord", [("tab",), ("shift", "tab"), ("escape",)])
def test_keys_that_only_move_away_from_a_sign_in_run(chord):
    assert judge(action="keys", declared_final=False, chord=chord, focused_name="Sign in", focused_role="Button") is None


@pytest.mark.parametrize("chord", [("enter",), ("space",)])
@pytest.mark.parametrize("name", ["Send", "傳送", "Delete", "Buy now", "購入"])
def test_enter_or_space_on_the_focused_button_that_sends_deletes_or_buys_asks_first(chord, name):
    assert judge(action="keys", declared_final=False, chord=chord, focused_name=name, focused_role="Button") == CONFIRM
    assert judge(action="keys", declared_final=False, chord=chord, name=name, role="Button") == CONFIRM
    # other keys on it change nothing
    assert judge(action="keys", declared_final=False, chord=("tab",), focused_name=name, focused_role="Button") is None


# --- keys that delete what is selected -------------------------------------------------------------

DELETING = [("delete",), ("ctrl", "delete"), ("shift", "delete"), ("ctrl", "shift", "delete"), ("backspace",),
            ("ctrl", "backspace"), ("shift", "backspace"), ("ctrl", "d")]  # fmt: skip
SELECTED = frozenset({"select", "invoke"})
EDITABLE = frozenset({"value"})


@pytest.mark.parametrize("chord", DELETING)
def test_keys_that_delete_what_is_selected_ask_first_away_from_a_text_field(chord):
    # a file selected in File Explorer, a mail in a list, a row of a table: Delete deletes it
    for role in ("ListItem", "DataItem", "TreeItem", "List", "Pane", "Button"):
        assert judge(action="keys", declared_final=False, chord=chord, focused_name="report.docx",
                     focused_role=role, focused_actions=SELECTED) == CONFIRM, role  # fmt: skip
        assert judge(action="keys", declared_final=False, chord=chord, name="report.docx", role=role,
                     actions=SELECTED) == CONFIRM, role  # fmt: skip
    # nothing known to have the focus: the keys may reach anything
    assert judge(action="keys", declared_final=False, chord=chord) == CONFIRM
    # a document that cannot be edited (a mail's reading pane, a web page): Delete deletes the mail
    assert judge(action="keys", declared_final=False, chord=chord, focused_name="Message body",
                 focused_role="Document", focused_actions=frozenset({"scroll", "text"})) == CONFIRM  # fmt: skip
    # a field that is read only
    assert judge(action="keys", declared_final=False, chord=chord, name="Subject", role="Edit") == CONFIRM


@pytest.mark.parametrize("chord", [c for c in DELETING if c != ("shift", "delete")])
def test_keys_that_delete_inside_a_text_field_only_edit_and_run(chord):
    assert judge(action="keys", declared_final=False, chord=chord, focused_name="Search",
                 focused_role="Edit", focused_actions=EDITABLE) is None  # fmt: skip
    assert judge(action="keys", declared_final=False, chord=chord, name="檔名", role="Edit", actions=EDITABLE) is None
    assert judge(action="keys", declared_final=False, chord=chord, focused_name="Body",
                 focused_role="Document", focused_actions=frozenset({"value", "scroll"})) is None  # fmt: skip
    # the element named is the one the keys reach, whatever has the focus
    assert judge(action="keys", declared_final=False, chord=chord, name="report.docx", role="ListItem",
                 actions=SELECTED, focused_name="Search", focused_role="Edit",
                 focused_actions=EDITABLE) == CONFIRM  # fmt: skip


def test_shift_delete_deletes_for_good_and_asks_even_in_a_text_field():
    assert judge(action="keys", declared_final=False, chord=("shift", "delete"), focused_name="Search",
                 focused_role="Edit", focused_actions=EDITABLE) == CONFIRM  # fmt: skip


@pytest.mark.parametrize("chord", [("left",), ("ctrl", "a"), ("ctrl", "c"), ("f2",), ("alt", "backspace")])
def test_other_keys_on_a_selected_file_run(chord):
    assert judge(action="keys", declared_final=False, chord=chord, focused_name="report.docx",
                 focused_role="ListItem", focused_actions=SELECTED) is None  # fmt: skip


def test_the_shell_refuses_the_same_deleting_keys_away_from_a_text_field():
    keys_rs = Path(__file__).resolve().parents[3] / "app" / "src-tauri" / "src" / "computer" / "keys.rs"
    source = keys_rs.read_text(encoding="utf-8")
    assert "pub fn deletes_items(self) -> bool" in source
    # the rule both sides keep: Delete or Backspace without Alt, and Ctrl+D alone
    assert all(safety.deletes_items(chord) for chord in DELETING)
    for chord in [("alt", "delete"), ("alt", "backspace"), ("ctrl", "shift", "d"), ("alt", "d"), ("enter",)]:
        assert not safety.deletes_items(chord), chord


# --- what the keys reach when the look knows no focus, and an element with no name ----------------


@pytest.mark.parametrize("chord", [("enter",), ("space",)])
def test_enter_or_space_with_no_element_and_no_known_focus_asks_first(chord):
    # a window looked at in the background: once it comes to the front, the focus may be on Send
    assert judge(action="keys", declared_final=False, chord=chord) == CONFIRM
    # with a focus the look knows, it is judged by that
    assert judge(action="keys", declared_final=False, chord=chord, focused_name="七", focused_role="Button") is None
    # other keys with no known focus run as before
    assert judge(action="keys", declared_final=False, chord=("ctrl", "s")) is None
    assert judge(action="keys", declared_final=False, chord=("tab",)) is None


def test_an_element_named_without_a_name_is_still_the_target():
    # a plain search box with no accessible name, while the look's focus is on a password field
    unnamed = {"name": "", "role": "Edit", "actions": EDITABLE}
    elsewhere = {"focused_name": "Password", "focused_role": "Edit", "focused_password": True}
    assert judge(action="type", declared_final=False, text="cats", **unnamed, **elsewhere) is None
    assert judge(action="keys", declared_final=False, chord=("ctrl", "a"), **unnamed, **elsewhere) is None
    assert judge(action="keys", declared_final=False, chord=("delete",), **unnamed, **elsewhere) is None
    # and is judged as itself: an unnamed list item takes Delete only with a yes
    assert judge(action="keys", declared_final=False, chord=("delete",), name="", role="ListItem",
                 focused_name="Search", focused_role="Edit", focused_actions=EDITABLE) == CONFIRM  # fmt: skip


def test_the_shell_judges_the_live_focus_by_the_same_send_words():
    policy = Path(__file__).resolve().parents[3] / "app" / "src-tauri" / "src" / "computer" / "policy.rs"
    source = policy.read_text(encoding="utf-8")

    def rust_list(name):
        body = re.search(rf"const {name}: &\[&str\] = &\[(.*?)\];", source, re.S)
        assert body, name
        return tuple(re.findall(r'"([^"]*)"', body.group(1)))

    assert rust_list("CONFIRM_EN") == safety._CONFIRM_EN
    assert rust_list("CONFIRM_CJK") == safety._CONFIRM_CJK
    assert rust_list("MESSAGE_EN") == safety._MESSAGE_EN
    assert rust_list("MESSAGE_CJK") == safety._MESSAGE_CJK
