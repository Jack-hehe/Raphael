// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
import { mountRunAnalytics } from "./run-analytics";
import type { RunAnalyticsApi, RunReport } from "./run-analytics-types";
import { setLocale } from "./i18n";

afterEach(() => document.body.replaceChildren());
it("shows catalog model names, localized members and durations, and expands the member top five", async () => {
  const summary = { duration_ms: 185200, cost_usd: .01, tokens: 120, ai_steps: 1, tools: 0, errors: 0 };
  const report: RunReport = { range: "month", start_day: null, end_day: "2026-10-24", summary, total: 1, groups: { member: Array.from({length:7}, (_, i) => ({label: i ? `Member ${i}` : "Raphael", member_id: i, tokens: 7-i, cost_usd: 0, runs: 1})), model: [{label:"cli:codex:gpt-6.1",tokens:120,cost_usd:.01,runs:1}], day: [], provider: [], routine: [] } };
  const api: RunAnalyticsApi = { usageRuns: vi.fn().mockResolvedValue(report), usageExport: vi.fn(), usageAllowances: vi.fn().mockResolvedValue({brains:[]}), saveUsageCsv:vi.fn(), brainsCli:vi.fn().mockResolvedValue({clis:[{id:"codex",models:[{id:"gpt-6.1",name:"GPT-6.1",provider:"OpenAI"}]}]}) };
  setLocale("zh-TW");
  const host = document.createElement("div"); document.body.append(host);
  const view = mountRunAnalytics(host,api); await view.ready;
  expect(host.textContent).toContain("3 分 5 秒");
  expect(host.textContent).toContain("拉斐爾");
  expect(host.textContent).toContain("GPT-6.1");
  expect(host.textContent).not.toContain("cli:codex:");
  expect(host.querySelectorAll('.run-members .run-bars li')).toHaveLength(5);
  host.querySelector<HTMLButtonElement>('[data-run-act="members"]')!.click();
  expect(host.querySelectorAll('.run-members .run-bars li')).toHaveLength(7);
  expect(host.querySelector('.tabs.switch [aria-selected="true"]')?.textContent).toBe("模型");
  setLocale("en"); expect(host.textContent).toContain("3m 5s");
  setLocale("ja"); expect(host.textContent).toContain("3分5秒");
  view.destroy(); setLocale("zh-TW");
});
it("keeps totals, grouping and CSV export without any per-run navigation", async () => {
  const summary = { duration_ms: 2000, cost_usd: .01, tokens: 120, ai_steps: 1, tools: 2, errors: 0 };
  const report: RunReport = { range: "month", start_day: null, end_day: "2026-10-24", summary, total: 0, groups: { member: [], model: [], day: [], provider: [], routine: [] } };
  const api: RunAnalyticsApi = { usageRuns: vi.fn().mockResolvedValue(report), usageExport: vi.fn().mockResolvedValue({ csv: "all runs" }), usageAllowances: vi.fn().mockResolvedValue({ brains: [] }), saveUsageCsv: vi.fn().mockResolvedValue(true) };
  const host = document.createElement("div"); document.body.append(host);
  const view = mountRunAnalytics(host, api); await view.ready;
  expect(host.querySelectorAll(".run-tile")).toHaveLength(6);
  expect(host.querySelector(".run-list, .run-timeline, [data-run-id]")).toBeNull();
  host.querySelector<HTMLButtonElement>('[data-run-group="day"]')!.click();
  expect(host.querySelector('[data-run-group="day"]')?.getAttribute("aria-selected")).toBe("true");
  host.querySelector<HTMLButtonElement>('[data-run-act="export"]')!.click();
  await vi.waitFor(() => expect(api.saveUsageCsv).toHaveBeenCalledWith("all runs"));
  expect(api.usageExport).toHaveBeenCalledWith(expect.objectContaining({ range: "month" }));
  host.querySelector<HTMLButtonElement>('[data-run-act="period"]')!.click();
  document.querySelector<HTMLButtonElement>('[data-value="week"]')!.click();
  await vi.waitFor(() => expect(api.usageRuns).toHaveBeenLastCalledWith(expect.objectContaining({ range: "week" })));
  view.destroy(); expect(host.childElementCount).toBe(0);
});

it("retries a failed load and does not open a save dialog after the view is destroyed", async () => {
  const summary = { duration_ms: 0, cost_usd: 0, tokens: 0, ai_steps: 0, tools: 0, errors: 0 };
  const report: RunReport = { range: "month", start_day: null, end_day: "2026-10-24", summary, total: 0, groups: { member: [], model: [], day: [], provider: [], routine: [] } };
  let finish!: (result: { csv: string }) => void;
  const api: RunAnalyticsApi = { usageRuns: vi.fn().mockRejectedValueOnce("offline").mockResolvedValue(report), usageExport: vi.fn().mockImplementation(() => new Promise(resolve => { finish = resolve; })), usageAllowances: vi.fn().mockResolvedValue({ brains: [] }), saveUsageCsv: vi.fn() };
  const host = document.createElement("div"); document.body.append(host);
  const view = mountRunAnalytics(host, api); await view.ready;
  expect(host.querySelector('[role="alert"]')!.textContent).toContain("offline");
  host.querySelector<HTMLButtonElement>('[data-run-act="retry"]')!.click();
  await vi.waitFor(() => expect(host.querySelector('[role="alert"]')).toBeNull());
  host.querySelector<HTMLButtonElement>('[data-run-act="export"]')!.click();
  view.destroy(); finish({ csv: "every run" });
  await Promise.resolve(); await Promise.resolve();
  expect(api.saveUsageCsv).not.toHaveBeenCalled();
});


it("shows recorded subscription exhaustion and reset beside live Codex allowances", async () => {
  const summary = { duration_ms:0, cost_usd:0, tokens:0, ai_steps:0, tools:0, errors:0 };
  const report: RunReport = { range:"month", start_day:null, end_day:"2026-10-24", summary, total:0, groups:{member:[],model:[],day:[],provider:[],routine:[]} };
  const api: RunAnalyticsApi = { usageRuns:vi.fn().mockResolvedValue(report), usageExport:vi.fn(), saveUsageCsv:vi.fn(), usageAllowances:vi.fn().mockResolvedValue({brains:[{id:"codex",name:"Codex CLI",windows:[{period:"fiveHour",remaining_percent:76,resets_at:1792800000}]},{id:"claude",name:"Claude",windows:[]},{id:"gemini",name:"Antigravity",windows:[]}],quotas:[{provider:"claude",reset_at_ms:1792800000000},{provider:"gemini",reset_at_ms:null}]}) };
  const host = document.createElement("div"); document.body.append(host);
  const view = mountRunAnalytics(host,api); await view.ready;
  const text = host.querySelector(".run-allowance")!.textContent;
  expect(text).toContain("76%"); expect(text).toContain("Claude"); expect(text).toContain("Antigravity");
  expect(text).toContain("額度已用完"); expect(text).toContain("重設時間未提供");
  view.destroy();
});

it("renders and refreshes all allowance sources with maker marks and honest unknown percentages", async () => {
  const summary = { duration_ms:0, cost_usd:0, tokens:0, ai_steps:0, tools:0, errors:0 };
  const report: RunReport = { range:"month", start_day:null, end_day:"2026-10-24", summary, total:0, groups:{member:[],model:[],day:[],provider:[],routine:[]} };
  const api: RunAnalyticsApi = { usageRuns:vi.fn().mockResolvedValue(report), usageExport:vi.fn(), saveUsageCsv:vi.fn(), usageAllowances:vi.fn().mockResolvedValue({brains:[
    {id:"codex",name:"Codex",source:"live",windows:[{period:"fiveHour",remaining_percent:76,resets_at:1792900000}]},
    {id:"claude",name:"Claude",source:"reported",windows:[
      {period:"fiveHour",remaining_percent:17,resets_at:1,status:"allowed_warning",updated_at_ms:1792800000000},
      {period:"weekly",remaining_percent:null,resets_at:null,status:"allowed",updated_at_ms:1792800000000},
    ]},
    {id:"gemini",name:"Antigravity",source:"unavailable",windows:[]},
  ],quotas:[{provider:"gemini",reset_at_ms:null}]}) };
  setLocale("zh-TW");
  const host = document.createElement("div"); document.body.append(host);
  const view = mountRunAnalytics(host,api); await view.ready;
  const section = host.querySelector(".run-allowance")!;
  expect(section.querySelectorAll(".run-subscription")).toHaveLength(3);
  expect(section.querySelectorAll("h4 .mono")).toHaveLength(3);
  expect(section.textContent).toContain("更新於");
  expect(section.textContent).toContain("這個 CLI 不提供剩餘額度");
  expect(section.textContent).toContain("接近額度上限");
  expect(section.textContent).not.toContain("null%");
  expect(section.querySelectorAll('[role="progressbar"]')).toHaveLength(2);
  expect(section.querySelectorAll("h4")).toHaveLength(3);
  expect(section.textContent).toContain("此回報的重設時間已過");
  vi.mocked(api.usageAllowances).mockResolvedValueOnce({ brains:[{
    id:"claude",name:"Claude",source:"reported",windows:[{
      period:"fiveHour",remaining_percent:12,resets_at:null,status:"allowed_warning",updated_at_ms:1792800060000,
    }],
  }] });
  host.querySelector<HTMLButtonElement>('[data-run-act="allowance"]')!.click();
  await vi.waitFor(() => expect(host.querySelector(".run-allowance")!.textContent).toContain("12%"));
  expect(host.querySelectorAll(".run-subscription")).toHaveLength(1);
  expect(host.querySelector(".run-allowance")!.textContent).not.toContain("額度已用完");
  setLocale("en"); expect(host.querySelector(".run-allowance")!.textContent).toContain("Last run report");
  setLocale("ja"); expect(host.querySelector(".run-allowance")!.textContent).toContain("更新日時");
  setLocale("zh-TW");
  view.destroy();
});
