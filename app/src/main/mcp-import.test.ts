// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({ integrationsPreview: vi.fn(), integrationsDiscover: vi.fn(), integrationsImport: vi.fn() }));
vi.mock("./api", () => api);
import { openMcpImport } from "./mcp-import";
const flush = () => vi.waitFor(() => expect(document.querySelector("[data-mcp-candidate]")).not.toBeNull());
afterEach(() => { document.querySelector<HTMLButtonElement>('[data-dlg="close"]')?.click(); document.body.replaceChildren(); });
it("previews JSON metadata, imports checked ids and dismisses the shared dialog", async () => {
  api.integrationsPreview.mockResolvedValue({ candidates: [{ id: "opaque", name: "Notes", source: "JSON", transport: "stdio", command: "npx", args: ["notes"], env: ["TOKEN"], headers: [] }], rejected: 0 });
  api.integrationsImport.mockResolvedValue({ servers: [] });
  const changed = vi.fn(); openMcpImport(false, changed);
  const input = document.querySelector<HTMLTextAreaElement>("[data-mcp-json]")!;
  input.value = '{"mcpServers":{"Notes":{"command":"npx","env":{"TOKEN":"private-value"}}}}';
  document.querySelector<HTMLButtonElement>('[data-dlg="preview"]')!.click(); await flush();
  expect(document.querySelector("[data-mcp-preview]")!.textContent).not.toContain("private-value");
  const selected = document.querySelector<HTMLInputElement>("[data-mcp-candidate]")!;
  selected.checked = false; selected.dispatchEvent(new Event("change", { bubbles: true }));
  expect(document.querySelector<HTMLButtonElement>('[data-dlg="import"]')!.disabled).toBe(true);
  selected.checked = true; selected.dispatchEvent(new Event("change", { bubbles: true }));
  document.querySelector<HTMLButtonElement>('[data-dlg="import"]')!.click();
  await vi.waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(api.integrationsImport).toHaveBeenCalledWith(["opaque"]);
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});
it("auto-discovers and shows an empty list with import disabled", async () => {
  api.integrationsDiscover.mockResolvedValue({ candidates: [], rejected: 2 });
  openMcpImport(true, vi.fn());
  await vi.waitFor(() => expect(api.integrationsDiscover).toHaveBeenCalled());
  await vi.waitFor(() => expect(document.querySelector("[data-mcp-preview]")!.textContent).toContain("2"));
  expect(document.querySelector<HTMLButtonElement>('[data-dlg="import"]')!.disabled).toBe(true);
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});
