// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { textPositionRect } from "./text-position";

afterEach(() => { vi.restoreAllMocks(); document.body.replaceChildren(); });

test("text coordinates retain the field's typography, wrap width and scroll offsets without leaking the mirror", () => {
  const input = document.createElement("textarea");
  input.value = "First line\nHello @Alice";
  input.style.cssText = "font: 16px/24px monospace; padding: 8px 12px; border: 1px solid; box-sizing: border-box; letter-spacing: 1px";
  document.body.append(input);
  input.scrollTop = 24; input.scrollLeft = 5;
  vi.spyOn(input, "getBoundingClientRect").mockReturnValue(new DOMRect(100, 200, 300, 80));
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
    if (this.tagName === "DIV") {
      expect(this.style.width).toBe("300px");
      expect(this.style.whiteSpace).toBe("pre-wrap");
      expect(this.style.lineHeight).toBe("24px");
      expect(this.style.paddingLeft).toBe("12px");
      expect(this.style.letterSpacing).toBe("1px");
    }
    return new DOMRect(0, 0, 300, 80);
  });
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockImplementation(function(this: HTMLElement) {
    expect(this.textContent).toBe("@Alice");
    return [new DOMRect(70, 40, 90, 16)] as unknown as DOMRectList;
  });
  const result = textPositionRect(input, input.value.indexOf("@"));
  expect([result.left, result.top, result.height]).toEqual([165, 216, 24]);
  expect(document.body.children).toHaveLength(1);
});
