import { expect, test, vi } from "vitest";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn(async () => ({ days: [] })) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke, Channel: class {} }));
import { usageAnalytics } from "./api";

test("usage requests cross the shell using only the named analytics RPC", async () => {
  const request = { range: "30d", end_day: "2026-10-23", tz: "Asia/Taipei" } as const;
  expect(await usageAnalytics(request)).toEqual({ days: [] });
  expect(invoke).toHaveBeenCalledWith("core_call", { method: "usage.analytics", params: request });
});
