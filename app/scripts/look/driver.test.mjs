// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from "vitest";
import { findVisibleElement } from "./driver.mjs";
import { requestDisposition } from "./requests.mjs";

describe("look browser interaction contracts", () => {
  afterEach(() => { document.body.innerHTML = ""; });
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
