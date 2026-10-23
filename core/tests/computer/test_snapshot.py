"""What the shell says is on screen, read with suspicion and written out for the model."""

from raphael_core.computer import snapshot as snap
from raphael_core.computer.snapshot import (
    describe,
    describe_windows,
    parse_snapshot,
    parse_windows,
)

from .conftest import calculator, element, windows


def test_a_look_is_read_into_elements_by_id():
    shot = parse_snapshot(calculator(), 7)
    assert shot is not None
    assert shot.app.exe == "calculatorapp.exe" and shot.app.name == "Calculator"
    assert list(shot.elements) == [1, 2, 3, 4, 5, 6]
    seven = shot.elements[3]
    assert (seven.role, seven.name, seven.actions, seven.rect) == ("Button", "七", {"invoke"}, (30, 20, 40, 30))
    assert shot.focused is shot.elements[6]


def test_a_password_field_never_keeps_a_value_even_if_the_shell_sent_one():
    shot = parse_snapshot(calculator(), 7)
    password = shot.elements[5]
    assert password.password and password.value is None
    text = describe(shot)
    assert "hunter2" not in text
    assert "PASSWORD-FIELD" in text


def test_a_look_at_another_window_or_of_another_shape_is_no_look():
    assert parse_snapshot(calculator(window=8), 7) is None
    assert parse_snapshot({"window": 7, "app": {"exe": "x"}, "elements": []}, 7) is None
    assert parse_snapshot({"window": 7, "app": {"exe": "calc.exe"}, "elements": "nope"}, 7) is None
    assert parse_snapshot("nope", 7) is None


def test_elements_of_the_wrong_shape_are_left_out():
    rows = [
        element(1, "Button", "ok"),
        {"id": "2", "depth": 1, "role": "Button"},
        {"id": 3, "depth": 99, "role": "Button"},
        {"id": 4, "depth": 1, "role": "<script>"},
        {"id": -5, "depth": 1, "role": "Button"},
        element(1, "Button", "duplicate id"),
        element(6, "Button", "rect", rect=[1, 2, -3, 4], state="weird", actions=["invoke", "rm -rf"]),
    ]
    shot = parse_snapshot(calculator(elements=rows), 7)
    assert list(shot.elements) == [1, 6]
    assert shot.elements[1].name == "ok"
    six = shot.elements[6]
    assert six.rect is None and six.state is None and six.actions == {"invoke"}


def test_the_elements_are_capped_and_the_cut_is_said():
    rows = [element(i, "Button", f"b{i}") for i in range(1, snap.MAX_ELEMENTS + 50)]
    shot = parse_snapshot(calculator(elements=rows), 7)
    assert len(shot.elements) == snap.MAX_ELEMENTS
    assert shot.truncated
    assert "cut short" in describe(shot)


def test_a_name_cannot_break_the_lines_or_run_on():
    rows = [element(1, "Button", "line one\nline two " + "x" * 500)]
    shot = parse_snapshot(calculator(elements=rows), 7)
    name = shot.elements[1].name
    assert "\n" not in name and " " not in name
    assert len(name) == snap.MAX_NAME_CHARS and name.endswith("…")
    assert len(describe(shot).splitlines()) == 2


def test_the_model_reads_one_line_per_element_indented_by_depth():
    text = describe(parse_snapshot(calculator(), 7))
    lines = text.splitlines()
    assert lines[0] == 'window 7 · Calculator (calculatorapp.exe) · title "計算機 - 私人帳本"'
    assert lines[1] == '[1] Window "計算機" @ 10,20 40x30'
    assert lines[3] == '  [3] Button "七" (invoke) @ 30,20 40x30'
    assert '  [6] Edit "訊息" focused (value) @ 60,20 40x30' in lines


def test_the_windows_are_listed_with_their_ids():
    listed = parse_windows(windows())
    assert [(w.id, w.app.exe, w.title, w.foreground) for w in listed] == [
        (7, "calculatorapp.exe", "計算機 - 私人帳本", True)
    ]
    assert describe_windows(listed) == '[7] Calculator (calculatorapp.exe) title "計算機 - 私人帳本" (in front)'
    assert describe_windows([]) == "(no app windows are open)"


def test_windows_of_the_wrong_shape_are_left_out():
    rows = {"windows": [
        {"id": 1, "exe": "NOT LOWER.EXE", "name": "x"},
        {"id": 0, "exe": "a.exe"},
        {"id": 2, "exe": "notepad.exe"},
        "nope",
    ]}  # fmt: skip
    listed = parse_windows(rows)
    assert [(w.id, w.app.name) for w in listed] == [(2, "notepad")]
    assert parse_windows({"windows": "x"}) == [] and parse_windows(None) == []
