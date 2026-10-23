import { expect, test, vi } from "vitest";
const { invoke } = vi.hoisted(() => ({ invoke: vi.fn(async () => ({})) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke, Channel: class {} }));
import { brainsCli, brainsCliLogin } from "./api";

test("CLI discovery crosses only the named main-window RPC", async () => {
  invoke.mockResolvedValueOnce({ clis: [] });
  expect(await brainsCli()).toEqual({ clis: [] });
  expect(invoke).toHaveBeenLastCalledWith("core_call", { method: "brains.cli", params: {} });
});
test("login sends only the selected CLI id", async () => {
  invoke.mockResolvedValueOnce({ started: true });
  expect(await brainsCliLogin("claude")).toEqual({ started: true });
  expect(invoke).toHaveBeenLastCalledWith("core_call", { method: "brains.cli_login", params: { id: "claude" } });
});
