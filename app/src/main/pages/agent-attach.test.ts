// @vitest-environment happy-dom
/// <reference types="node" />
// The composer's 「＋」 with images and PDFs, in a page of its own (happy-dom), with the app and the
// core faked: what it takes, what it refuses, the chips it shows, and what it sends to `agent.ask`.
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { AgentTurn, AppContext, MainEvent } from "../types";

const api = vi.hoisted(() => ({
  agentHistoryList: vi.fn(),
  agentHistoryClear: vi.fn(),
  agentHistorySearch: vi.fn(),
  agentAskText: vi.fn(),
  agentQuestionOpen: vi.fn(),
  agentQuestionAnswer: vi.fn(),
  agentVoice: vi.fn(),
  workerCancel: vi.fn(),
  workerRerun: vi.fn(),
}));
vi.mock("../api", () => api);
vi.mock("../ui", async (original) => ({ ...(await original<typeof import("../ui")>()), toaster: { info: vi.fn() } }));

import { Cleared } from "../cleared";
import { emptyTimelineState, pendingTurnHTML, turnHTML } from "../timeline";
import { toaster } from "../ui";
import {
  ATTACH_MAX,
  IMAGE_MAX_BYTES,
  PDF_MAX_BYTES,
  PICKER_ACCEPT,
  attachRefusal,
  attachmentType,
  base64Of,
  createAgent,
  typedQuestion,
  wireAttachments,
} from "./agent";

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);
const PDF = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37]);
const png = (name = "photo.png"): File => new File([PNG], name, { type: "image/png" });
const pdf = (name = "report.pdf"): File => new File([PDF], name, { type: "application/pdf" });
/** A file whose size says more than it holds: the limits are read from the size, before the bytes. */
const sized = (file: File, size: number): File => Object.defineProperty(file, "size", { value: size });

const flush = async (): Promise<void> => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

function mount() {
  const app = {
    go: vi.fn(),
    device: () => ({ dictation_mode: "hold" }),
    onEvent(_handler: (event: MainEvent) => void) {
      return () => {};
    },
    undo: { begin: vi.fn() },
    cleared: new Cleared(),
  } as unknown as AppContext;
  const agent = createAgent(app);
  document.body.append(agent.el);
  agent.show();
  // The composer is put on the page once the conversation is loaded: it is looked for each time.
  const picker = (): HTMLInputElement => agent.el.querySelector<HTMLInputElement>(".cmp-picker")!;
  const field = (): HTMLTextAreaElement => agent.el.querySelector<HTMLTextAreaElement>(".cmp-input")!;
  /** Picks `files` with 「＋」's file dialog. */
  const pick = async (...files: File[]): Promise<void> => {
    Object.defineProperty(picker(), "files", { value: files, configurable: true });
    picker().dispatchEvent(new Event("change"));
    await flush();
  };
  const chips = (): string[] => [...agent.el.querySelectorAll(".cmp-file .cmp-chip .cmp-chip-t")].map((c) => c.textContent ?? "");
  return { agent, picker, field, pick, chips };
}

beforeEach(() => {
  document.body.innerHTML = "";
  for (const fn of Object.values(api)) fn.mockReset();
  vi.mocked(toaster.info).mockReset();
  api.agentQuestionOpen.mockResolvedValue(null);
  api.agentHistoryList.mockResolvedValue({ turns: [] });
  api.agentAskText.mockResolvedValue(undefined);
});

afterEach(() => {
  document.body.innerHTML = "";
});

describe("what 「＋」 takes", () => {
  test("text files as before, and PNG, JPG, WebP, GIF and PDF; nothing else", () => {
    expect(PICKER_ACCEPT).toContain(".txt");
    for (const kind of [".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"]) expect(PICKER_ACCEPT).toContain(kind);
    expect(attachmentType({ name: "a.png", type: "image/png" })).toBe("image/png");
    expect(attachmentType({ name: "a.jpg", type: "image/jpg" })).toBe("image/jpeg");
    expect(attachmentType({ name: "a.webp", type: "" })).toBe("image/webp");
    expect(attachmentType({ name: "scan.PDF", type: "application/octet-stream" })).toBe("application/pdf");
    expect(attachmentType({ name: "notes.md", type: "text/markdown" })).toBe("text");
    expect(attachmentType({ name: "a.bmp", type: "image/bmp" })).toBeNull();
    expect(attachmentType({ name: "setup.exe", type: "application/x-msdownload" })).toBeNull();
    expect(attachmentType({ name: "fake.png", type: "application/x-msdownload" })).toBeNull();
  });

  test("at most four, an image up to 10 MB and a PDF up to 20 MB (the core's limits)", () => {
    expect(ATTACH_MAX).toBe(4);
    // the app's own limit is the same (commands.rs)
    const commands = readFileSync(path.resolve(import.meta.dirname, "..", "..", "..", "src-tauri", "src", "commands.rs"), "utf8");
    expect(commands).toContain(`pub const TYPED_MAX_ATTACHMENTS: usize = ${ATTACH_MAX};`);
    expect(attachRefusal(3, "image/png", IMAGE_MAX_BYTES)).toBeNull();
    expect(attachRefusal(4, "image/png", 10)).toBe("count");
    expect(attachRefusal(0, "image/png", IMAGE_MAX_BYTES + 1)).toBe("size");
    expect(attachRefusal(0, "application/pdf", PDF_MAX_BYTES)).toBeNull();
    expect(attachRefusal(0, "application/pdf", PDF_MAX_BYTES + 1)).toBe("size");
    expect(attachRefusal(0, "image/gif", 0)).toBe("empty");
  });

  test("the bytes go in base64, whole", () => {
    expect(base64Of(PNG)).toBe(btoa(String.fromCharCode(...PNG)));
    const big = new Uint8Array(100_000).map((_, i) => i % 251);
    expect(atob(base64Of(big)).length).toBe(100_000);
    expect(atob(base64Of(big)).charCodeAt(99_999)).toBe(99_999 % 251);
  });

  test("with files, no words is a question too; without, it is none", () => {
    expect(typedQuestion("  ", null, true)).toBe("");
    expect(typedQuestion("  ", null)).toBeNull();
    expect(typedQuestion("這張圖是什麼？", null, true)).toBe("這張圖是什麼？");
  });
});

describe("the composer with images and PDFs", () => {
  test("an image and a PDF show as chips with ×; the image with a small picture of itself", async () => {
    const { agent, pick, chips } = mount();
    await flush();
    await pick(png(), pdf());
    expect(chips()).toEqual(["photo.png", "report.pdf"]);
    const [image, file] = [...agent.el.querySelectorAll<HTMLElement>(".cmp-file .cmp-chip")];
    expect(image.querySelector<HTMLImageElement>("img.cmp-thumb")!.src).toBe(`data:image/png;base64,${base64Of(PNG)}`);
    expect(file.querySelector("img")).toBeNull();
    for (const chip of [image, file]) {
      const x = chip.querySelector<HTMLButtonElement>("button.chip-x")!;
      expect(x.getAttribute("aria-label")).toBe("移除檔案");
    }
    expect(agent.el.querySelector<HTMLElement>(".cmp-file")!.hidden).toBe(false);
  });

  test("× takes one off; the others stay", async () => {
    const { agent, pick, chips } = mount();
    await flush();
    await pick(png("a.png"), png("b.png"), pdf());
    agent.el.querySelectorAll<HTMLElement>(".cmp-file .chip-x")[1].click();
    expect(chips()).toEqual(["a.png", "report.pdf"]);
    agent.el.querySelectorAll<HTMLElement>(".cmp-file .chip-x")[0].click();
    agent.el.querySelectorAll<HTMLElement>(".cmp-file .chip-x")[0].click();
    expect(chips()).toEqual([]);
    expect(agent.el.querySelector<HTMLElement>(".cmp-file")!.hidden).toBe(true);
  });

  test("a fifth is refused with a toast; too big is refused with a toast; the rest are taken", async () => {
    const { pick, chips } = mount();
    await flush();
    await pick(png("1.png"), sized(png("huge.png"), IMAGE_MAX_BYTES + 1), png("2.png"));
    expect(chips()).toEqual(["1.png", "2.png"]);
    expect(toaster.info).toHaveBeenCalledWith("「huge.png」太大了（圖片最多 10 MB，PDF 最多 20 MB）");
    await pick(png("3.png"), pdf("4.pdf"), png("5.png"), png("6.png"));
    expect(chips()).toEqual(["1.png", "2.png", "3.png", "4.pdf"]);
    expect(toaster.info).toHaveBeenLastCalledWith("最多只能附加 4 個檔案");
    expect(toaster.info).toHaveBeenCalledTimes(2); // the sixth is not tried after the fifth
  });

  test("another kind of file is refused, and says what may be attached", async () => {
    const { pick, chips } = mount();
    await flush();
    await pick(new File(["MZ"], "setup.exe", { type: "application/x-msdownload" }));
    expect(chips()).toEqual([]);
    expect(toaster.info).toHaveBeenCalledWith("只能附加圖片（PNG、JPG、WebP、GIF）、PDF 或 .txt、.md 文字檔");
  });

  test("a pasted image is attached; a paste of words alone stays the field's", async () => {
    const { field, chips } = mount();
    await flush();
    const paste = (files: File[], text = ""): Event => {
      const event = new Event("paste", { bubbles: true, cancelable: true });
      Object.defineProperty(event, "clipboardData", { value: { files, getData: () => text } });
      field().dispatchEvent(event);
      return event;
    };
    expect(paste([], "只是文字").defaultPrevented).toBe(false);
    expect(paste([new File([PNG], "", { type: "image/png" })]).defaultPrevented).toBe(true);
    await flush();
    expect(chips()).toEqual(["image.png"]);
  });

  test("sending gives agent.ask the files as {name, mime, data}, and the composer is empty after", async () => {
    const { agent, pick, field, chips } = mount();
    await flush();
    await pick(png(), pdf());
    field().value = "  這是什麼？ ";
    agent.el.querySelector<HTMLFormElement>(".composer")!.requestSubmit();
    await flush();
    expect(api.agentAskText).toHaveBeenCalledTimes(1);
    expect(api.agentAskText).toHaveBeenCalledWith("這是什麼？", [
      { name: "photo.png", mime: "image/png", data: base64Of(PNG) },
      { name: "report.pdf", mime: "application/pdf", data: base64Of(PDF) },
    ]);
    expect(chips()).toEqual([]);
    expect(field().value).toBe("");
  });

  test("files with no words are sent, as a question of no words", async () => {
    const { agent, pick } = mount();
    await flush();
    await pick(png());
    agent.el.querySelector<HTMLFormElement>(".composer")!.requestSubmit();
    await flush();
    expect(api.agentAskText).toHaveBeenCalledWith("", [{ name: "photo.png", mime: "image/png", data: base64Of(PNG) }]);
  });

  test("a question without files sends none", async () => {
    const { agent, field } = mount();
    await flush();
    field().value = "你好";
    agent.el.querySelector<HTMLFormElement>(".composer")!.requestSubmit();
    await flush();
    expect(api.agentAskText).toHaveBeenCalledWith("你好", []);
  });

  test("a question that fails gives its files back to the composer", async () => {
    api.agentAskText.mockRejectedValue("拉斐爾正在忙，請稍後再問");
    const { agent, pick, chips } = mount();
    await flush();
    await pick(pdf());
    agent.el.querySelector<HTMLFormElement>(".composer")!.requestSubmit();
    await flush();
    expect(chips()).toEqual(["report.pdf"]);
  });

  test("the wire form holds the name, the type and the bytes, nothing of the page's own", () => {
    expect(wireAttachments([{ id: 7, name: "a.png", mime: "image/png", data: "AA==" }])).toEqual([{ name: "a.png", mime: "image/png", data: "AA==" }]);
  });
});

describe("a turn with files in the conversation", () => {
  const look = { face: () => "" };
  const turn = (over: Partial<AgentTurn>): AgentTurn => ({
    id: 3, created_at_ms: Date.parse("2026-10-23T10:00:00+08:00"), question: "這是什麼？", route: "chat", prefix: "答",
    text: "是一張收據", detail: null, status: "done", source: "app", ...over,
  });

  test("shows their names as small chips under the words, from the history's attachments", () => {
    const html = turnHTML(turn({ attachments: [{ name: "receipt.jpg", mime: "image/jpeg" }, { name: "a.pdf", mime: "application/pdf" }] }), emptyTimelineState(), false, look);
    const bubble = /<div class="bubble">(.*?)<\/div><\/div><div class="ra">/.exec(html)![1];
    expect(bubble.indexOf("「這是什麼？」")).toBeLessThan(bubble.indexOf("me-files"));
    expect([...bubble.matchAll(/<span class="me-file-t">(.*?)<\/span>/g)].map((m) => m[1])).toEqual(["receipt.jpg", "a.pdf"]);
  });

  test("files with no words still show the bubble, with the chips alone; a turn without files has none", () => {
    const only = turnHTML(turn({ question: "", attachments: [{ name: "x.png", mime: "image/png" }] }), emptyTimelineState(), false, look);
    expect(only).toContain('<div class="me">');
    expect(only).not.toContain("「");
    expect(only).toContain("x.png");
    expect(turnHTML(turn({}), emptyTimelineState(), false, look)).not.toContain("me-files");
  });

  test("a question on its way shows its files too", () => {
    const html = pendingTurnHTML("", "app", look, [{ name: "scan.pdf", mime: "application/pdf" }]);
    expect(html).toContain("scan.pdf");
    expect(html).not.toContain("「」");
  });
});
