// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { mountModelPicker, modelPickerHTML, type ModelPicker, type PickerRole } from "./model-picker";
import type { CliBrain } from "./types";
const clis: CliBrain[] = [
  { id: "claude", name: "Claude Code", installed: true, logged_in: true, login_command: "claude auth login", models: [
    { id: "cli:claude:sonnet", name: "Claude Sonnet 5.5", is_default: true, reasoning_efforts: ["low", "high"], default_effort: "high" },
    { id: "cli:claude:opus", name: "Claude Opus 5.5", reasoning_efforts: ["low", "high"] },
  ] },
  { id: "codex", name: "Codex", installed: true, logged_in: true, login_command: "codex login", models: [{ id: "cli:codex:luna", name: "GPT Luna", is_default: true }] },
  { id: "gemini", name: "Antigravity", installed: false, logged_in: false, login_command: "agy", models: [] },
];
let picker: ModelPicker;
afterEach(() => { picker?.close(); document.body.innerHTML = ""; vi.restoreAllMocks(); });
function mount(role: PickerRole = "answer", current = "cli:claude") {
  const slot = document.createElement("div"); document.body.append(slot);
  const choose = vi.fn(), sources = vi.fn(), login = vi.fn();
  picker = mountModelPicker(slot, { role, catalog: null, clis, current, label: "Answer", onChoose: choose, onSources: sources, onCliAction: login });
  picker.button.focus(); picker.open(); return { choose, sources, login };
}
const click = (selector: string) => document.querySelector<HTMLButtonElement>(selector)!.click();
test("subscriptions are separate; bare refs show the concrete default and source", () => {
  const html = modelPickerHTML({ role: "answer", current: "cli:claude", catalog: null, clis, label: "Answer" });
  expect(html).toContain("Claude Sonnet 5.5"); expect(html).toContain("Claude Code");
  mount(); expect(document.querySelectorAll('[data-source^="cli:"]')).toHaveLength(3);
  expect(document.querySelector('.mc-row[aria-checked="true"]')?.textContent).toContain("Claude Sonnet 5.5");
  expect(document.querySelector('.mc-default')).not.toBeNull(); expect(document.querySelector(".mc-tools")).toBeNull();
});
test("selection is staged and effort is saved with the concrete model only on confirmation", () => {
  const { choose } = mount(); click('[data-model="cli:claude:opus"]'); click('[data-effort="low"]');
  expect(choose).not.toHaveBeenCalled(); expect(document.querySelector(".mc-selection")?.textContent).toContain("Claude Opus 5.5");
  click('[data-dlg="use"]'); expect(choose).toHaveBeenCalledWith("cli:claude:opus?effort=low");
  expect(document.querySelector('[role="dialog"]')).toBeNull(); expect(document.activeElement).toBe(picker.button);
});
test("cancel and Escape discard drafts; install and connection buttons perform real actions", () => {
  const { choose, sources, login } = mount(); click('[data-model="cli:claude:opus"]'); click('[data-dlg="close"]');
  expect(choose).not.toHaveBeenCalled(); picker.open(); click('[data-source="cli:gemini"]');
  expect(document.querySelector(".mc-row")).toBeNull(); click('[data-dlg="login"]'); expect(login).toHaveBeenCalledWith("gemini");
  click('[data-dlg="sources"]'); expect(sources).toHaveBeenCalledOnce(); picker.open();
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); expect(picker.isOpen).toBe(false);
});
test.each(["cleanup", "stt", "vision"] as const)("%s excludes subscription brains", (role) => {
  mount(role); expect(document.querySelector('[data-source^="cli:"]')).toBeNull();
});
test("logout removes rows and disables confirmation without saving", () => {
  const { choose } = mount(); picker.update({ clis: clis.map((c) => ({ ...c, logged_in: false })) });
  expect(picker.button.textContent).toContain("Claude Sonnet 5.5");
  expect(document.querySelector(".mc-row")).toBeNull(); expect(document.querySelector<HTMLButtonElement>('[data-dlg="use"]')!.disabled).toBe(true);
  expect(choose).not.toHaveBeenCalled();
});

test("Antigravity shows mixed makers and saves the full effort-suffixed model ref", () => {
  const slot = document.createElement("div"); document.body.append(slot);
  const choose = vi.fn();
  picker = mountModelPicker(slot, { role: "answer", catalog: null, label: "Answer", current: "cli:gemini:gemini-4-argon-low", onChoose: choose,
    clis: [{ id: "gemini", name: "Antigravity", installed: true, logged_in: true, login_command: "agy", models: [
      { id: "cli:gemini:gemini-4-argon", name: "Gemini Argon", provider: "Google", is_default: true, reasoning_efforts: ["low", "high"], default_effort: "high",
        effort_models: { low: "cli:gemini:gemini-4-argon-low", high: "cli:gemini:gemini-4-argon-high" } },
      { id: "cli:gemini:claude-opus-5-5", name: "Claude Opus 5.5", provider: "Anthropic", reasoning_efforts: ["high"], effort_models: { high: "cli:gemini:claude-opus-5-5-high" } },
    ] }] });
  expect(picker.button.textContent).toContain("Gemini Argon"); expect(picker.button.textContent).toContain("Antigravity");
  picker.open(); expect(document.querySelector('[data-effort="low"]')?.getAttribute("aria-pressed")).toBe("true");
  expect(document.querySelectorAll(".mc-row")).toHaveLength(2);
  click('[data-effort="high"]'); expect(choose).not.toHaveBeenCalled();
  click('[data-dlg="use"]'); expect(choose).toHaveBeenCalledWith("cli:gemini:gemini-4-argon-high");
  picker.open(); click('[data-model="cli:gemini:claude-opus-5-5"]');
  click('[data-dlg="use"]'); expect(choose).toHaveBeenLastCalledWith("cli:gemini:claude-opus-5-5-high");
});

test("switching sources preserves the staged effort and never changes the saved-source dot", () => {
  const { choose } = mount(); click('[data-model="cli:claude:opus"]'); click('[data-effort="low"]');
  click('[data-source="cli:codex"]'); click('[data-source="cli:claude"]');
  expect(document.querySelector('[data-effort="low"]')?.getAttribute("aria-pressed")).toBe("true");
  expect(document.querySelector(".mc-current")?.closest("[data-source]")?.getAttribute("data-source")).toBe("cli:claude");
  click('[data-dlg="use"]'); expect(choose).toHaveBeenCalledWith("cli:claude:opus?effort=low");
});

test("a dialog handed to a replacement settings row keeps its draft and returns focus to the new trigger", () => {
  const { choose } = mount(); click('[data-model="cli:claude:opus"]'); click('[data-effort="low"]');
  picker.button.parentElement!.remove();
  const slot = document.createElement("div"); document.body.append(slot);
  picker = mountModelPicker(slot, { role: "answer", catalog: null, clis, current: "cli:claude", label: "Answer", onChoose: choose });
  expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
  click('[data-dlg="use"]'); expect(choose).toHaveBeenCalledWith("cli:claude:opus?effort=low");
  expect(document.activeElement).toBe(picker.button);
});

test("local speech selection waits for confirmation, including downloads; delete still asks first", () => {
  const slot = document.createElement("div"); document.body.append(slot);
  const use = vi.fn(), remove = vi.fn(), cancel = vi.fn();
  picker = mountModelPicker(slot, { role: "stt", current: "cloud/speech", catalog: null, label: "Speech", onChoose: vi.fn(),
    local: { active: null, download: null, models: [
      { name: "qwen3-asr", title: "Qwen3-ASR", ready: false, languages: ["en"], size: 1000, note: "dictionary" },
      { name: "sensevoice", title: "SenseVoice", ready: true, languages: ["en"], size: 1000, note: "fast" },
    ] }, onUseLocal: use, onDeleteLocal: remove, onCancelLocal: cancel });
  picker.open(); click('[data-act="use"][data-name="qwen3-asr"]');
  expect(use).not.toHaveBeenCalled(); expect(document.querySelector('[data-act="download"]')).toBeNull();
  click('[data-dlg="use"]'); expect(use).toHaveBeenCalledWith("qwen3-asr");
  click('[data-act="delete"][data-name="sensevoice"]'); expect(remove).not.toHaveBeenCalled();
  click('[data-act="confirm"][data-name="sensevoice"]'); expect(remove).toHaveBeenCalledWith("sensevoice");
});

test("an inherited member model remains selectable even when every subscription is signed out", () => {
  const slot = document.createElement("div"); document.body.append(slot);
  const choose = vi.fn();
  picker = mountModelPicker(slot, { role: "answer", current: "cli:codex:luna", catalog: null,
    clis: clis.map((cli) => ({ ...cli, logged_in: false })), label: "Member model", lead: { id: "", name: "Shared model" }, onChoose: choose });
  picker.open(); click('.mc-inherit'); expect(choose).not.toHaveBeenCalled();
  click('[data-dlg="use"]'); expect(choose).toHaveBeenCalledWith("");
});
