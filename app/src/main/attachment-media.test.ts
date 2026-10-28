// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createAttachmentMedia } from "./attachment-media";
import { sentFileHTML } from "./attachments";

beforeEach(() => vi.stubGlobal("IntersectionObserver", undefined));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = ""; });

test("stored copies populate inline media and are released when the thread changes", async () => {
  const id = "a".repeat(32) + ".png";
  const root = document.createElement("div");
  root.innerHTML = `<button data-attachment="${id}"><img data-attachment="${id}"></button>`;
  document.body.append(root);
  const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:stored-image");
  const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  const read = vi.fn().mockResolvedValue({ mime: "image/png", data: btoa("image") });
  const media = createAttachmentMedia(root, read);
  media.sync();
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(root.querySelector("img")!.src).toBe("blob:stored-image");
  expect(media.source(root.querySelector("button")!)).toBe("blob:stored-image");
  media.sync(); await Promise.resolve();
  expect(read).toHaveBeenCalledTimes(1);
  expect(create).toHaveBeenCalledTimes(1);
  media.reset();
  expect(revoke).toHaveBeenCalledWith("blob:stored-image");
});

test("a response from an old thread cannot populate or retain media in the new one", async () => {
  const root = document.createElement("div");
  root.innerHTML = `<img data-attachment="${"b".repeat(32)}.png">`;
  const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:unused");
  let finish!: (file: { mime: string; data: string }) => void;
  const media = createAttachmentMedia(root, () => new Promise(resolve => { finish = resolve; }));
  media.sync(); media.reset();
  finish({ mime: "image/png", data: btoa("old") });
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(create).not.toHaveBeenCalled();
  expect(root.querySelector("img")!.hasAttribute("src")).toBe(false);
});

test("the audio pill plays, pauses, seeks and follows duration and playback events", async () => {
  const root = document.createElement("div");
  root.innerHTML = sentFileHTML({ name: "voice.wav", mime: "audio/wav", preview: "data:audio/wav;base64,AA==" });
  const audio = root.querySelector("audio")!;
  let paused = true;
  Object.defineProperty(audio, "duration", { configurable: true, value: 90 });
  Object.defineProperty(audio, "paused", { configurable: true, get: () => paused });
  const play = vi.spyOn(audio, "play").mockImplementation(async () => { paused = false; audio.dispatchEvent(new Event("play")); });
  const pause = vi.spyOn(audio, "pause").mockImplementation(() => { paused = true; audio.dispatchEvent(new Event("pause")); });
  const media = createAttachmentMedia(root, vi.fn()); media.sync(); media.sync();
  const button = root.querySelector<HTMLButtonElement>("[data-audio-toggle]")!;
  const range = root.querySelector<HTMLInputElement>("[data-audio-progress]")!;
  expect(range.disabled).toBe(false);
  button.click(); await Promise.resolve();
  expect(play).toHaveBeenCalledTimes(1);
  expect(button.getAttribute("aria-pressed")).toBe("true");
  audio.currentTime = 30; audio.dispatchEvent(new Event("timeupdate"));
  expect(root.querySelector("[data-audio-time]")!.textContent).toBe("0:30 / 1:30");
  range.value = "50"; range.dispatchEvent(new Event("input"));
  expect(audio.currentTime).toBe(45);
  button.click(); expect(pause).toHaveBeenCalledTimes(1);
  expect(button.getAttribute("aria-pressed")).toBe("false");
  media.reset(); expect(pause).toHaveBeenCalledTimes(2);
});

test("removed media are stopped when the timeline redraws", () => {
  const root = document.createElement("div"); root.innerHTML = "<video></video>";
  const pause = vi.spyOn(root.querySelector("video")!, "pause").mockImplementation(() => {});
  const media = createAttachmentMedia(root, vi.fn()); media.sync();
  root.replaceChildren(); media.sync();
  expect(pause).toHaveBeenCalledTimes(1);
});

test("lazy loading watches the visible audio pill rather than its hidden media element", async () => {
  let intersect!: IntersectionObserverCallback;
  const observe = vi.fn();
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: IntersectionObserverCallback) { intersect = callback; }
    observe = observe;
    disconnect() {}
  });
  const root = document.createElement("div");
  root.innerHTML = sentFileHTML({ id: `${"c".repeat(32)}.wav`, name: "voice.wav", mime: "audio/wav" });
  const read = vi.fn().mockResolvedValue({ mime: "audio/wav", data: btoa("sound") });
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:voice");
  const media = createAttachmentMedia(root, read); media.sync();
  const pill = root.querySelector(".attachment-audio")!;
  expect(observe).toHaveBeenCalledWith(pill);
  intersect([{ isIntersecting: true, target: pill } as IntersectionObserverEntry], {} as IntersectionObserver);
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(root.querySelector("audio")!.src).toBe("blob:voice");
});
