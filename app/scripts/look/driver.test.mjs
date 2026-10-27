// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from "vitest";
import { findVisibleElement, perform } from "./driver.mjs";
import { requestDisposition } from "./requests.mjs";

describe("look browser interaction contracts", () => {
  afterEach(() => { document.body.innerHTML = ""; });
  it("requeries a replaced click target without replaying application errors", async () => {
    const stale = { click: vi.fn().mockRejectedValue(new Error("Node is either not clickable or not an Element")), dispose: vi.fn() };
    const current = { click: vi.fn().mockResolvedValue(undefined), dispose: vi.fn() };
    const page = { waitForFunction: vi.fn().mockResolvedValueOnce({ asElement: () => stale }).mockResolvedValue({ asElement: () => current }) };
    await perform(page, { action: "click", selector: ".target" });
    expect(page.waitForFunction).toHaveBeenCalledTimes(2);
    expect(stale.dispose).toHaveBeenCalledOnce();
    expect(current.click).toHaveBeenCalledOnce();
    current.click.mockRejectedValue(new Error("application failure"));
    await expect(perform(page, { action: "click", selector: ".target" })).rejects.toThrow("application failure");
    expect(page.waitForFunction).toHaveBeenCalledTimes(3);
  });
  it.each([true, false])("opens the wide member picker through visible column controls (collapsed=%s)", async collapsed => {
    const clicked = [];
    const page = {
      evaluate: vi.fn().mockResolvedValue(false),
      $eval: vi.fn().mockResolvedValue(collapsed),
      waitForFunction: vi.fn().mockImplementation(async (_fn, _options, selector) => ({ asElement: () => ({ click: async () => { clicked.push(selector); }, dispose: vi.fn() }) })),
    };
    await perform(page, { action: "member-picker" });
    expect(clicked).toEqual([...(collapsed ? ['[data-col="collapse"]'] : []), '[data-col="add"]', '#app-popover [data-pick="conversation"]']);
  });
  it("fails after three pre-dispatch stale handles", async () => {
    const target = { click: vi.fn().mockRejectedValue(new Error("Node is detached from document")), dispose: vi.fn() };
    const page = { waitForFunction: vi.fn().mockResolvedValue({ asElement: () => target }) };
    await expect(perform(page, { action: "click", selector: ".target" })).rejects.toThrow("Node is detached");
    expect(target.click).toHaveBeenCalledTimes(3);
    expect(target.dispose).toHaveBeenCalledTimes(3);
  });
  it("waits for an absence by its selector and says what is still there when it does not come", async () => {
    const page = { waitForFunction: vi.fn().mockResolvedValue(undefined) };
    await perform(page, { action: "wait-gone", selector: ".row" });
    expect(page.waitForFunction).toHaveBeenCalledWith(expect.any(Function), { timeout: 6000 }, ".row");
    const gone = page.waitForFunction.mock.calls[0][0];
    document.body.innerHTML = '<div class="row"></div>';
    expect(gone(".row")).toBe(false);
    document.body.innerHTML = "";
    expect(gone(".row")).toBe(true);
    page.waitForFunction.mockRejectedValue(new Error("timeout"));
    await expect(perform(page, { action: "wait-gone", selector: ".row" })).rejects.toThrow("Still present: .row");
  });
  it("finds the visible result when a collapsed work section matches first", () => {
    document.body.innerHTML = '<div hidden><button data-action="reply">Work</button></div><button data-action="reply">Answer</button>';
    for (const element of document.querySelectorAll("button")) element.getBoundingClientRect = () => ({ width: 28, height: 28 });
    expect(findVisibleElement('[data-action="reply"]').textContent).toBe("Answer");
    expect(findVisibleElement(".missing")).toBeNull();
  });
  it("ignores an empty first match", () => {
    document.body.innerHTML = '<div class="target"></div><div class="target">Ready</div>';
    document.querySelectorAll(".target")[1].getBoundingClientRect = () => ({ width: 100, height: 30 });
    expect(findVisibleElement(".target").textContent).toBe("Ready");
  });
  it("serves no favicon and preserves failures for other resources", () => {
    const origin = "http://127.0.0.1:1234";
    expect(requestDisposition(`${origin}/favicon.ico?v=1`, origin)).toBe("empty");
    expect(requestDisposition(`${origin}/missing.js`, origin)).toBe("continue");
    expect(requestDisposition("https://outside.example/image.png", origin)).toBe("block");
    expect(requestDisposition("data:image/png;base64,AA==", origin)).toBe("continue");
  });
});
