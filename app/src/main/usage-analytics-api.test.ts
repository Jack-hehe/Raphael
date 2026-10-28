import { expect, test, vi } from "vitest";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn(async () => ({ days: [] })) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke, Channel: class {} }));
import { usageAnalytics, usageRuns, usageExport, usageAllowances, saveUsageCsv } from "./api";

test("usage requests cross the shell using only the named analytics RPC", async () => {
  const request = { range: "30d", end_day: "2026-10-23", tz: "Asia/Taipei" } as const;
  expect(await usageAnalytics(request)).toEqual({ days: [] });
  expect(invoke).toHaveBeenCalledWith("core_call", { method: "usage.analytics", params: request });
});

test("aggregate member bars use the member's current picture, while export stays a native save", async () => {
  const call = invoke as ReturnType<typeof vi.fn>;
  call.mockImplementation(async (command: string, args: { method?: string }) => {
    if (args.method === "members.list") return { members: [{ id: 7, name: "Research", avatar_url: "data:image/png;base64,AAAA" }] };
    if (args.method === "usage.runs") return { groups:{member:[{label:"Research",tokens:1,cost_usd:0,runs:1}]} };
    if (args.method === "usage.export") return { csv: "every run" };
    if (args.method === "usage.allowances") return { brains: [] };
    return command === "save_usage_csv";
  });
  expect((await usageRuns({ range: "all" })).groups.member[0]).toMatchObject({member_id:7,avatar_url:"data:image/png;base64,AAAA"});
  expect(await usageExport({ range: "all" })).toEqual({ csv: "every run" });
  expect(await usageAllowances()).toEqual({ brains: [] });
  expect(await saveUsageCsv("every run")).toBe(true);
  expect(call).toHaveBeenCalledWith("save_usage_csv", { csv: "every run" });
});
