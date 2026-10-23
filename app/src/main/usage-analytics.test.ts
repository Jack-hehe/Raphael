// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";
import { mountUsageAnalytics } from "./usage-analytics";
import type { UsageAnalytics, UsageGroup, UsageRequest } from "./usage-analytics-types";

function group(key = "agent", total = 2): UsageGroup {
  return { total, series: [{ key, label: key, total, share: 1, values: [0, 0, 0, 0, 0, 0, total] }] };
}
function data(): UsageAnalytics {
  return {
    range: "7d", start_day: "2026-10-17", end_day: "2026-10-23", today_day: "2026-10-23",
    days: Array.from({ length: 7 }, (_, i) => `2026-10-${17 + i}`), tz: "Asia/Taipei",
    cost_by: { purpose: group(), model: group("vendor/model"), provider: group("openrouter") },
    tools: group("web_search", 3), messages_by: { model: group("vendor/model", 4), source: group("app", 4) },
    members: Array.from({ length: 7 }, (_, id) => ({
      id, name: id ? `Member ${id}` : "Raphael", role: "Writer", deleted: false,
      turns: 2, cost_usd: 0.125, share: 1 / 7,
      models: [{ model: "vendor/model", turns: 2, cost_usd: 0.125 }],
    })),
    dictation: { dictations: [0, 0, 0, 0, 0, 0, 2], words: [0, 0, 0, 0, 0, 0, 40],
      estimated: [false, false, false, false, false, false, true],
      estimated_words: 10, estimated_dictations: 1, day_basis: "capture_local" },
  };
}
const disposers: (() => void)[] = [];
afterEach(() => { disposers.splice(0).forEach(stop => stop()); document.body.replaceChildren(); setLocale("zh-TW"); });

async function setup(answer: (p: UsageRequest) => Promise<UsageAnalytics> = async () => data()) {
  const host = document.createElement("div");
  document.body.append(host);
  const api = { usageAnalytics: vi.fn(answer) };
  const mounted = mountUsageAnalytics(host, api);
  disposers.push(() => mounted.destroy());
  await mounted.ready;
  return { host, api, mounted };
}

test("renders all five analytics sections, exact values and estimated dictation notice", async () => {
  const { host, api } = await setup();
  expect(api.usageAnalytics).toHaveBeenCalledWith({ range: "7d", tz: "Asia/Taipei" });
  for (const label of ["用量走勢", "各角色用量", "工具使用", "訊息", "聽寫"]) expect(host.textContent).toContain(label);
  expect(host.querySelectorAll("svg")).toHaveLength(4);
  expect(host.textContent).toContain("100.0%");
  expect(host.textContent).toContain("US$2.00");
  expect(host.textContent).toContain("估算");
  const mark = host.querySelector<SVGElement>("[data-tooltip]")!;
  mark.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
  expect(host.querySelector('[role="tooltip"]')?.textContent).toContain("2026-10-23");
  expect(host.querySelector('[role="tooltip"]')?.textContent).toContain("US$2.00");
});

function click(host: HTMLElement, control: string) {
  host.querySelector<HTMLButtonElement>(`[data-control="${control}"]`)!.click();
}
function select(host: HTMLElement, control: string, value: string) {
  const input = host.querySelector<HTMLSelectElement>(`[data-control="${control}"]`)!;
  input.value = value;
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

test("changes cost, message and dictation grouping without another RPC", async () => {
  const { host, api } = await setup();
  select(host, "cost", "provider");
  expect(host.querySelector(".ua-cost-head select")?.getAttribute("data-control")).toBe("cost");
  expect(host.querySelector(".ua-legend")?.textContent).toContain("openrouter");
  select(host, "messages", "source");
  expect(host.querySelectorAll(".ua-legend")[2]?.textContent).toContain("應用程式");
  select(host, "dictation", "words");
  expect(host.querySelectorAll(".ua-total")[3]?.textContent).toBe("40");
  expect(api.usageAnalytics).toHaveBeenCalledTimes(1);
});

test("expands models and shows more than five members, preserving focus", async () => {
  const { host } = await setup();
  expect(host.querySelector('[data-member="6"]')).toBeNull();
  const member = host.querySelector<HTMLButtonElement>('[data-member="0"]')!;
  member.focus();
  member.click();
  expect(host.querySelector('[data-member="0"]')?.getAttribute("aria-expanded")).toBe("true");
  expect((document.activeElement as HTMLElement).dataset.member).toBe("0");
  const details = host.querySelector(`#${host.querySelector('[data-member="0"]')!.getAttribute("aria-controls")}`)!;
  expect(details.hasAttribute("hidden")).toBe(false);
  expect(details.textContent).toContain("vendor/model");
  click(host, "more");
  expect(host.querySelector('[data-member="6"]')).not.toBeNull();
  click(host, "member-0");
  expect(host.querySelector('[data-member="0"]')?.getAttribute("aria-expanded")).toBe("false");
  click(host, "more");
  expect(host.querySelector('[data-member="6"]')).toBeNull();
});

test("navigates complete periods, clamps forward at today and switches range", async () => {
  const { host, api } = await setup(async p => {
    const result = data();
    result.end_day = p.end_day ?? result.end_day;
    result.range = p.range;
    return result;
  });
  expect(host.querySelector<HTMLButtonElement>('[data-control="next"]')!.disabled).toBe(true);
  click(host, "previous");
  await vi.waitFor(() => expect(api.usageAnalytics).toHaveBeenLastCalledWith({ range: "7d", end_day: "2026-10-16", tz: "Asia/Taipei" }));
  await vi.waitFor(() => expect(host.getAttribute("aria-busy")).toBeNull());
  await vi.waitFor(() => expect(host.querySelector<HTMLButtonElement>('[data-control="next"]')!.disabled).toBe(false));
  click(host, "next");
  await vi.waitFor(() => expect(api.usageAnalytics).toHaveBeenLastCalledWith({ range: "7d", end_day: "2026-10-23", tz: "Asia/Taipei" }));
  click(host, "30d");
  await vi.waitFor(() => expect(api.usageAnalytics.mock.lastCall?.[0].range).toBe("30d"));
  await vi.waitFor(() => expect(host.querySelector('[data-range="30d"]')?.getAttribute("aria-pressed")).toBe("true"));
  click(host, "previous");
  await vi.waitFor(() => expect(api.usageAnalytics.mock.lastCall?.[0].end_day).toBe("2026-09-23"));
});

test("renders useful empty states and translates the mounted view without refetching", async () => {
  const { host, api } = await setup(async () => {
    const result = data();
    const empty = { total: 0, series: [] };
    result.cost_by = { purpose: empty, model: empty, provider: empty };
    result.messages_by = { model: empty, source: empty };
    result.tools = empty;
    result.members = [];
    result.dictation.dictations.fill(0);
    result.dictation.words.fill(0);
    result.dictation.estimated.fill(false);
    return result;
  });
  expect(host.querySelectorAll(".ua-empty")).toHaveLength(5);
  expect(host.textContent).not.toMatch(/NaN|Infinity|undefined/);
  setLocale("en");
  expect(host.textContent).toContain("Usage history");
  expect(host.textContent).toContain("No usage in this period");
  setLocale("ja");
  expect(host.textContent).toContain("利用履歴");
  expect(api.usageAnalytics).toHaveBeenCalledTimes(1);
});

test("recovers from an RPC error using the retry button", async () => {
  let calls = 0;
  const { host } = await setup(async () => {
    if (++calls === 1) throw new Error("private backend detail");
    return data();
  });
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("無法載入");
  expect(host.textContent).not.toContain("private backend detail");
  click(host, "retry");
  await vi.waitFor(() => expect(host.querySelectorAll("svg")).toHaveLength(4));
});

test("ignores stale responses and pending work after destroy", async () => {
  const resolvers: ((value: UsageAnalytics) => void)[] = [];
  const host = document.createElement("div");
  document.body.append(host);
  const api = { usageAnalytics: vi.fn(() => new Promise<UsageAnalytics>(resolve => resolvers.push(resolve))) };
  const mounted = mountUsageAnalytics(host, api);
  click(host, "30d");
  expect(resolvers).toHaveLength(2);
  resolvers[1]!(data());
  await vi.waitFor(() => expect(host.querySelectorAll("svg")).toHaveLength(4));
  const stale = data(); stale.cost_by.purpose = group("stale", 999);
  resolvers[0]!(stale);
  await mounted.ready;
  expect(host.textContent).not.toContain("999");
  const pending = mounted.refresh();
  mounted.destroy();
  resolvers[2]!(data());
  await pending;
  setLocale("en");
  expect(host.childElementCount).toBe(0);
});

test("escapes names and exposes exact tooltips on hover, focus and Escape", async () => {
  const { host } = await setup(async () => {
    const result = data();
    result.members[0]!.name = '<img src="bad" onerror="alert(1)">';
    result.cost_by.purpose.series[0]!.label = "Safe";
    return result;
  });
  expect(host.querySelector("img")).toBeNull();
  expect(host.textContent).toContain('<img src="bad"');
  const mark = host.querySelector<SVGElement>("[data-tooltip]")!;
  mark.dispatchEvent(new Event("pointerover", { bubbles: true }));
  const tooltip = host.querySelector<HTMLElement>('[role="tooltip"]')!;
  expect(tooltip.hidden).toBe(false);
  expect(mark.getAttribute("aria-describedby")).toBe(tooltip.id);
  mark.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  expect(tooltip.hidden).toBe(true);
});

test("tooltips retain sub-cent precision while the summary uses the standard USD format", async () => {
  const { host } = await setup(async () => {
    const result = data();
    result.cost_by.purpose = group("agent", 0.012345);
    return result;
  });
  expect(host.querySelector(".ua-total")?.textContent).toBe("US$0.01");
  expect(host.querySelector("[data-tooltip]")?.getAttribute("aria-label")).toContain("US$0.012345");
});

test("remounting one host disposes its previous locale listeners and pending requests", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  let resolve!: (v: UsageAnalytics) => void;
  const old = mountUsageAnalytics(host, { usageAnalytics: () => new Promise(r => { resolve = r; }) });
  const current = mountUsageAnalytics(host, { usageAnalytics: async () => data() });
  disposers.push(() => current.destroy());
  await current.ready;
  resolve(data());
  await old.ready;
  setLocale("en");
  expect(host.querySelectorAll(".usage-analytics")).toHaveLength(1);
  expect(host.textContent).toContain("Usage history");
  await old.refresh();
  expect(host.querySelectorAll("svg")).toHaveLength(4);
});
