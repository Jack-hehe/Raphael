// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { createLinkPreviews, previewCardsHTML } from "./link-previews";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); document.body.innerHTML = ""; });
test("cards load on intersection, share cached requests on redraw, reserve space and reject remote thumbnails", async () => {
  let observe!: IntersectionObserverCallback;
  const watched: Element[] = [];
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: IntersectionObserverCallback) { observe = callback; }
    observe(el: Element) { watched.push(el); }
    unobserve() {} disconnect() {}
  });
  const root = document.createElement("div"); document.body.append(root);
  root.innerHTML = previewCardsHTML("https://example.com/a https://example.com/b https://example.com/a https://example.com/c https://example.com/d");
  const fetch = vi.fn().mockResolvedValue({ url: "https://example.com/a", title: "<img onerror=alert(1)>", site: "Example", description: "A story", thumbnail: "https://private.example/image" });
  const previews = createLinkPreviews(root, fetch);
  previews.sync(); expect(fetch).not.toHaveBeenCalled(); expect(watched).toHaveLength(3);
  const first = root.querySelector<HTMLElement>(".preview-card")!;
  const html = first.outerHTML;
  observe([{ target: first, isIntersecting: true } as unknown as IntersectionObserverEntry], {} as IntersectionObserver);
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(fetch).toHaveBeenCalledExactlyOnceWith("https://example.com/a");
  expect(first.textContent).toContain("A story"); expect(first.querySelector("img")).toBeNull();
  expect(first.querySelector("script,[onerror]")).toBeNull();
  root.innerHTML = html; previews.sync();
  const replacement = root.firstElementChild!;
  observe([{ target: replacement, isIntersecting: true } as unknown as IntersectionObserverEntry], {} as IntersectionObserver);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(replacement.textContent).toContain("A story");
  previews.pause();
});

test("a pending result cannot mutate a hidden or replaced thread, and can be reused when shown", async () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  let resolve!: (value: null) => void;
  const fetch = vi.fn(() => new Promise<null>(r => { resolve = r; }));
  const root = document.createElement("div"); document.body.append(root);
  root.innerHTML = previewCardsHTML("https://example.com");
  const previews = createLinkPreviews(root, fetch); previews.sync(); await Promise.resolve();
  previews.pause(); resolve(null);
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(root.querySelector("[data-preview-loaded]")).toBeNull();
  previews.sync(); expect(fetch).toHaveBeenCalledTimes(1);
  expect(root.querySelector("[data-preview-state='unavailable']")).not.toBeNull();
});

test("preview cache expires after one day and keeps bounded raster thumbnails", async () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.useFakeTimers(); vi.setSystemTime(0);
  const root = document.createElement("div"); document.body.append(root);
  const fetch = vi.fn().mockResolvedValue({ url: "https://example.com/", site: "Site", title: "Article", description: "Summary", thumbnail: "data:image/png;base64,cG5n" });
  const previews = createLinkPreviews(root, fetch);
  const draw = async () => { root.innerHTML = previewCardsHTML("https://example.com"); previews.sync(); for (let i = 0; i < 8; i++) await Promise.resolve(); };
  await draw(); expect(root.querySelector("img")?.getAttribute("src")).toBe("data:image/png;base64,cG5n");
  vi.setSystemTime(86399_000); await draw(); expect(fetch).toHaveBeenCalledTimes(1);
  vi.setSystemTime(86401_000); await draw(); expect(fetch).toHaveBeenCalledTimes(2);
  previews.pause();
});
