import { describe, expect, test } from "vitest";
import { ModelDownload, type ModelPhase, type ModelsApi, downloadPhase, formatProgress, resumeModels } from "./models";
import type { MainEvent, ModelsStatus } from "./types";

/** The sizes the plan gives (Task 9): the bar does not jump when the second model starts. */
const SILERO = 2_313_101;
const QWEN = 878_702_423;

function progress(name: string, done: number, total: number): MainEvent {
  return { type: "ModelProgress", name, done, total };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** A core that lists `models` and lets the test decide when `ensure` ends. */
function fakeApi(models: ModelsStatus["models"]) {
  const ensures: { names: string[]; end: ReturnType<typeof deferred<{ ok: boolean }>> }[] = [];
  let statusCalls = 0;
  const api: ModelsApi = {
    status: async () => {
      statusCalls++;
      return { models };
    },
    ensure: (names) => {
      const end = deferred<{ ok: boolean }>();
      ensures.push({ names, end });
      return end.promise;
    },
  };
  return { api, ensures, statusCalls: () => statusCalls };
}

const missing = [
  { name: "silero-vad", ready: false },
  { name: "qwen3-asr", ready: false },
];

/** Lets the pending promises of the download run. */
const settle = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

describe("downloadPhase", () => {
  const names = ["silero-vad", "qwen3-asr"];

  test("before the first report the bar is at zero of the expected size", () => {
    expect(downloadPhase(names, new Map())).toEqual({ kind: "downloading", done: 0, total: SILERO + QWEN });
  });

  test("adds up what the models have done", () => {
    const seen = new Map([
      ["silero-vad", { done: SILERO, total: SILERO }],
      ["qwen3-asr", { done: 100, total: QWEN }],
    ]);
    expect(downloadPhase(names, seen)).toEqual({ kind: "downloading", done: SILERO + 100, total: SILERO + QWEN });
  });

  test("a total the core reports wins over the expected one", () => {
    const seen = new Map([["qwen3-asr", { done: 50, total: 1000 }]]);
    expect(downloadPhase(["qwen3-asr"], seen)).toEqual({ kind: "downloading", done: 50, total: 1000 });
  });

  test("a model nothing is known about has no size, so the bar cannot be drawn", () => {
    expect(downloadPhase(["mystery"], new Map())).toEqual({ kind: "downloading", done: 0, total: 0 });
  });

  test("done never goes over total", () => {
    const seen = new Map([["qwen3-asr", { done: 2000, total: 1000 }]]);
    expect(downloadPhase(["qwen3-asr"], seen)).toEqual({ kind: "installing" });
  });

  test("all models fully downloaded means they are being unpacked", () => {
    const seen = new Map([
      ["silero-vad", { done: SILERO, total: SILERO }],
      ["qwen3-asr", { done: QWEN, total: QWEN }],
    ]);
    expect(downloadPhase(names, seen)).toEqual({ kind: "installing" });
  });

  test("one model done while the other has not started is still downloading", () => {
    const seen = new Map([["silero-vad", { done: SILERO, total: SILERO }]]);
    expect(downloadPhase(names, seen).kind).toBe("downloading");
  });
});

describe("formatProgress", () => {
  test("big downloads are in GB, shared unit", () => {
    expect(formatProgress(380_000_000, 880_000_000)).toBe("0.38 / 0.88 GB");
  });

  test("small ones are in MB", () => {
    expect(formatProgress(12_400_000, 40_000_000)).toBe("12 / 40 MB");
  });
});

describe("ModelDownload", () => {
  test("models that are all there need no download", async () => {
    const { api, ensures } = fakeApi([
      { name: "silero-vad", ready: true },
      { name: "qwen3-asr", ready: true },
    ]);
    const download = new ModelDownload(api);
    await download.start();
    expect(download.phase).toEqual({ kind: "ready" });
    expect(ensures).toHaveLength(0);
  });

  test("only the models that are missing are asked for", async () => {
    const { api, ensures } = fakeApi([
      { name: "silero-vad", ready: true },
      { name: "qwen3-asr", ready: false },
    ]);
    const download = new ModelDownload(api);
    const done = download.start();
    await settle();
    expect(ensures.map((e) => e.names)).toEqual([["qwen3-asr"]]);
    ensures[0].end.resolve({ ok: true });
    await done;
    expect(download.phase).toEqual({ kind: "ready" });
  });

  test("goes from checking through downloading and installing to ready", async () => {
    const { api, ensures } = fakeApi(missing);
    const seen: ModelPhase["kind"][] = [];
    const download = new ModelDownload(api, (phase) => seen.push(phase.kind));
    const done = download.start();
    await settle();
    download.handle(progress("silero-vad", 1000, SILERO));
    download.handle(progress("silero-vad", SILERO, SILERO));
    download.handle(progress("qwen3-asr", 5000, QWEN));
    download.handle(progress("qwen3-asr", QWEN, QWEN));
    expect(download.phase).toEqual({ kind: "installing" });
    ensures[0].end.resolve({ ok: true });
    await done;
    expect(download.phase).toEqual({ kind: "ready" });

    // changes are reported once each, and installing is a state of its own
    expect(seen[0]).toBe("checking");
    expect(seen).toContain("downloading");
    expect(seen.indexOf("installing")).toBeGreaterThan(seen.lastIndexOf("downloading"));
    expect(seen[seen.length - 1]).toBe("ready");
  });

  test("the bar follows the bytes", async () => {
    const { api } = fakeApi(missing);
    const download = new ModelDownload(api);
    void download.start();
    await settle();
    download.handle(progress("silero-vad", SILERO, SILERO));
    download.handle(progress("qwen3-asr", 100, QWEN));
    expect(download.phase).toEqual({ kind: "downloading", done: SILERO + 100, total: SILERO + QWEN });
  });

  test("reports for models that are not being fetched are ignored", async () => {
    const { api } = fakeApi([{ name: "qwen3-asr", ready: false }]);
    const download = new ModelDownload(api);
    void download.start();
    await settle();
    const before = download.phase;
    download.handle(progress("sensevoice", 5, 10));
    download.handle({ type: "Level", value: 0.5 });
    expect(download.phase).toEqual(before);
  });

  test("reports that come after the end are ignored", async () => {
    const { api, ensures } = fakeApi([{ name: "qwen3-asr", ready: false }]);
    const download = new ModelDownload(api);
    const done = download.start();
    await settle();
    ensures[0].end.resolve({ ok: true });
    await done;
    download.handle(progress("qwen3-asr", 1, 10));
    expect(download.phase).toEqual({ kind: "ready" });
  });

  test("a download that fails says why, and can be tried again", async () => {
    const { api, ensures, statusCalls } = fakeApi([{ name: "qwen3-asr", ready: false }]);
    const download = new ModelDownload(api);
    const first = download.start();
    await settle();
    ensures[0].end.reject("[-32603] 下載失敗：連線中斷");
    await first;
    expect(download.phase).toEqual({ kind: "failed", reason: "下載失敗：連線中斷" });

    const second = download.start();
    await settle();
    expect(statusCalls()).toBe(2);
    expect(ensures).toHaveLength(2);
    expect(download.phase.kind).toBe("downloading");
    ensures[1].end.resolve({ ok: true });
    await second;
    expect(download.phase).toEqual({ kind: "ready" });
  });

  test("an answer of not ok is a failure too", async () => {
    const { api, ensures } = fakeApi([{ name: "qwen3-asr", ready: false }]);
    const download = new ModelDownload(api);
    const done = download.start();
    await settle();
    ensures[0].end.resolve({ ok: false });
    await done;
    expect(download.phase.kind).toBe("failed");
  });

  test("a core that does not tell which models are there is a failure", async () => {
    const api: ModelsApi = {
      status: () => Promise.reject("Raphael's core is not running"),
      ensure: () => Promise.reject(new Error("must not be called")),
    };
    const download = new ModelDownload(api);
    await download.start();
    expect(download.phase).toEqual({ kind: "failed", reason: "Raphael 的核心沒有在執行" });
  });

  test("starting twice while it runs asks the core once", async () => {
    const { api, ensures } = fakeApi(missing);
    const download = new ModelDownload(api);
    const a = download.start();
    const b = download.start();
    await settle();
    expect(ensures).toHaveLength(1);
    expect(ensures[0].names).toEqual(["silero-vad", "qwen3-asr"]);
    ensures[0].end.resolve({ ok: true });
    await Promise.all([a, b]);
  });

  test("starting again when everything is ready does nothing", async () => {
    const { api, statusCalls } = fakeApi([{ name: "qwen3-asr", ready: true }]);
    const download = new ModelDownload(api);
    await download.start();
    await download.start();
    expect(statusCalls()).toBe(1);
  });
});

describe("resumeModels", () => {
  test("fetches what is missing, and says nothing when that works", async () => {
    const { api, ensures } = fakeApi([{ name: "qwen3-asr", ready: false }]);
    const warnings: string[] = [];
    const done = resumeModels(api, (message) => warnings.push(message));
    await settle();
    expect(ensures.map((e) => e.names)).toEqual([["qwen3-asr"]]);
    ensures[0].end.resolve({ ok: true });
    await done;
    expect(warnings).toEqual([]);
  });

  test("asks nothing more when the models are there", async () => {
    const { api, ensures } = fakeApi([{ name: "qwen3-asr", ready: true }]);
    const warnings: string[] = [];
    await resumeModels(api, (message) => warnings.push(message));
    expect(ensures).toHaveLength(0);
    expect(warnings).toEqual([]);
  });

  test("warns, with the reason, when the models cannot be fetched", async () => {
    const { api, ensures } = fakeApi([{ name: "qwen3-asr", ready: false }]);
    const warnings: string[] = [];
    const done = resumeModels(api, (message) => warnings.push(message));
    await settle();
    ensures[0].end.reject("[-32603] download failed: offline");
    await done;
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("download failed: offline");
  });

  test("warns when the core cannot even say which models are there", async () => {
    const api: ModelsApi = {
      status: () => Promise.reject("Raphael's core is not running"),
      ensure: () => Promise.reject(new Error("must not be called")),
    };
    const warnings: string[] = [];
    await resumeModels(api, (message) => warnings.push(message));
    expect(warnings).toHaveLength(1);
  });
});
