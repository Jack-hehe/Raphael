// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { mountModelPicker, type ModelPicker, type PickerRole } from "./model-picker";
import { closeMenu } from "./ui";
import type { CliBrain } from "./types";
const clis: CliBrain[] = [
  { id: "claude", name: "Claude Code", installed: true, logged_in: true, login_command: "claude auth login", models: [{ id: "cli:claude", name: "CLI default" }, { id: "cli:claude:sonnet", name: "sonnet" }] },
  { id: "codex", name: "Codex", installed: true, logged_in: true, login_command: "codex login", models: [] },
  { id: "gemini", name: "Gemini CLI", installed: true, logged_in: null, login_command: "gemini", models: [] },
];
let picker: ModelPicker;
afterEach(() => { picker?.close(); closeMenu(); document.body.innerHTML = ""; localStorage.clear(); vi.restoreAllMocks(); });
function mount(role: PickerRole = "answer") {
  vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => {});
  const slot = document.createElement("div"); document.body.append(slot);
  const choose = vi.fn();
  picker = mountModelPicker(slot, { role, catalog: null, clis, current: "cli:codex", label: "Model", onChoose: choose });
  picker.open(); return choose;
}
test("answer picker offers a subscription chip with three official marks and exact default/model refs without an API catalog", () => {
  const choose = mount();
  const chip = document.querySelector<HTMLButtonElement>('[data-connection="subscription"]')!;
  expect(chip).not.toBeNull(); expect(chip.textContent).toContain("訂閱");
  expect(chip.querySelectorAll(".mono")).toHaveLength(3); chip.click();
  expect([...document.querySelectorAll<HTMLElement>(".mp-opt")].map((el) => el.dataset.id).sort()).toEqual(["cli:claude", "cli:claude:sonnet", "cli:codex"]);
  document.querySelector<HTMLButtonElement>('.mp-opt[data-id="cli:claude:sonnet"]')!.click();
  expect(choose).toHaveBeenCalledWith("cli:claude:sonnet");
});
test.each(["cleanup", "stt", "vision"] as const)("%s never offers CLI brains", (role) => {
  mount(role);
  expect(document.querySelector('[data-connection="subscription"]')).toBeNull();
  expect(document.querySelector('.mp-opt[data-id^="cli:"]')).toBeNull();
});
test("live logout removes CLI options while preserving search", () => {
  mount(); const search = document.querySelector<HTMLInputElement>("#mp-search")!;
  search.value = "sonnet"; search.dispatchEvent(new Event("input", { bubbles: true }));
  picker.update({ clis: clis.map((c) => ({ ...c, logged_in: false })) });
  expect(document.querySelector<HTMLInputElement>("#mp-search")!.value).toBe("sonnet");
  expect(document.querySelector('.mp-opt[data-id^="cli:"]')).toBeNull();
});

test("the subscription view has a simple count and no API token pricing or OpenRouter notice", () => {
  mount();
  picker.update({ catalog: { llm: [], stt: [], source: "builtin", fetched_at_ms: null, replaced: [] } });
  document.querySelector<HTMLButtonElement>('[data-connection="subscription"]')!.click();
  expect(document.querySelector(".mp-foot")!.textContent).toBe("3 個模型");
  expect(document.querySelector(".mp-cols")!.textContent).toBe("模型價格");
});
