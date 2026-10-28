// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { selectionQuote } from "./message-quote";

afterEach(() => { document.getSelection()?.removeAllRanges(); document.body.innerHTML = ""; vi.restoreAllMocks(); });

function mount() {
  document.body.innerHTML = '<section><div class="thread"><div class="message-text">しかねません</div><div class="bubble">Owner words</div></div><textarea></textarea></section>';
  const host = document.querySelector("section")!, thread = host.querySelector<HTMLElement>(".thread")!, input = host.querySelector("textarea")!;
  const quote = vi.fn();
  const hide = selectionQuote({ host, thread, active: () => host.isConnected, author: element => element.matches(".bubble") ? "You" : "Teacher", onQuote: quote });
  const select = (element: Element) => {
    const range = document.createRange(); range.selectNodeContents(element);
    range.getClientRects = () => [{ left: 100, right: 210, top: 100, bottom: 120, width: 110, height: 20 }] as unknown as DOMRectList;
    const selection = document.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
  };
  return { host, thread, input, quote, hide, select, pill: () => host.querySelector<HTMLButtonElement>(".quote-pill")! };
}

test("only message selections offer a quote, preserving full text and author", () => {
  const m = mount(); m.select(m.thread.querySelector(".message-text")!);
  expect(m.pill().hidden).toBe(false);
  m.pill().click();
  expect(m.quote).toHaveBeenCalledExactlyOnceWith({ text: "しかねません", author: "Teacher" });
  expect(m.pill().hidden).toBe(true);
  m.select(m.thread.querySelector(".bubble")!); m.pill().click();
  expect(m.quote).toHaveBeenLastCalledWith({ text: "Owner words", author: "You" });
  m.select(m.input); expect(m.pill().hidden).toBe(true);
});

test("scroll, Escape, outside click and cleared selections dismiss the pill", () => {
  const m = mount(), message = m.thread.querySelector(".message-text")!;
  for (const dismiss of [
    () => m.thread.dispatchEvent(new Event("scroll")),
    () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })),
    () => document.body.dispatchEvent(new Event("pointerdown", { bubbles: true })),
    () => { document.getSelection()!.removeAllRanges(); document.dispatchEvent(new Event("selectionchange")); },
  ]) { m.select(message); expect(m.pill().hidden).toBe(false); dismiss(); expect(m.pill().hidden).toBe(true); }
});
