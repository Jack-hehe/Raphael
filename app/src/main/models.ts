// Getting the speech models onto this computer: asks the core which are missing, has it fetch them
// (`models.ensure`) and follows the `ModelProgress` events. The download is of the compressed
// archives; once every byte is there the core unpacks them, which takes a while without any
// report, so that is a state of its own ("installing") and not a bar stuck at 100%.

import { t } from "./i18n";

import { describeError } from "./format";
import type { MainEvent, ModelsStatus } from "./types";

/** What the core is asked. */
export interface ModelsApi {
  status(): Promise<ModelsStatus>;
  ensure(names: string[]): Promise<{ ok: boolean }>;
}

export type ModelPhase =
  /** Nothing asked yet. */
  | { kind: "idle" }
  /** Asking the core which models are there. */
  | { kind: "checking" }
  /** Bytes fetched of bytes to fetch; `total` is 0 when the sizes are not known. */
  | { kind: "downloading"; done: number; total: number }
  /** Everything is fetched and is being unpacked. */
  | { kind: "installing" }
  | { kind: "ready" }
  | { kind: "failed"; reason: string };

/**
 * The sizes in bytes of the downloads, for a bar that starts at the right length
 * before the core has reported the second model. The core's own totals win once they arrive.
 */
const EXPECTED_BYTES: Record<string, number> = {
  "silero-vad": 2_313_101,
  "qwen3-asr": 878_702_423,
};

interface Seen {
  done: number;
  total: number;
}

/** Where the download of `names` stands, given what the core has reported for each. */
export function downloadPhase(names: string[], seen: ReadonlyMap<string, Seen>): ModelPhase {
  let done = 0;
  let total = 0;
  let fetched = 0;
  for (const name of names) {
    const report = seen.get(name);
    const size = report?.total || EXPECTED_BYTES[name] || 0;
    const have = Math.min(report?.done ?? 0, size);
    done += have;
    total += size;
    if (report && report.total > 0 && report.done >= report.total) fetched++;
  }
  if (names.length > 0 && fetched === names.length) return { kind: "installing" };
  return { kind: "downloading", done, total };
}

const MB = 1_000_000;
const GB = 1_000_000_000;

/** `380 MB of 880 MB` as the row shows it: one unit for both numbers. */
export function formatProgress(done: number, total: number): string {
  if (total >= GB / 2) return `${(done / GB).toFixed(2)} / ${(total / GB).toFixed(2)} GB`;
  return `${Math.round(done / MB)} / ${Math.round(total / MB)} MB`;
}

/**
 * A start of the app after the guide: fetches the models that are missing, without any window to
 * show it in. A failure is only reported to `warn`.
 */
export async function resumeModels(api: ModelsApi, warn: (message: string) => void): Promise<void> {
  const download = new ModelDownload(api);
  await download.start();
  const { phase } = download;
  if (phase.kind === "failed") warn(`could not fetch the speech models: ${phase.reason}`);
}

/** One run of getting the models, from the window's side. */
export class ModelDownload {
  private current: ModelPhase = { kind: "idle" };
  private running: Promise<void> | null = null;
  private names: string[] = [];
  private seen = new Map<string, Seen>();

  /** `onChange` hears each new phase (also each new byte count). */
  constructor(
    private readonly api: ModelsApi,
    private readonly onChange: (phase: ModelPhase) => void = () => {},
  ) {}

  get phase(): ModelPhase {
    return this.current;
  }

  /**
   * Checks the models and fetches the missing ones. Resolves when that is over, whether it worked
   * (`ready`) or not (`failed`). Called while it runs, it joins that run; after `failed` it tries
   * again; after `ready` it does nothing.
   */
  start(): Promise<void> {
    if (this.running) return this.running;
    if (this.current.kind === "ready") return Promise.resolve();
    this.running = this.run().finally(() => {
      this.running = null;
    });
    return this.running;
  }

  /** Feed it the events of the core. Only the progress of the models being fetched counts. */
  handle(event: MainEvent): void {
    if (event.type !== "ModelProgress" || !this.names.includes(event.name)) return;
    if (this.current.kind !== "downloading" && this.current.kind !== "installing") return;
    this.seen.set(event.name, { done: event.done, total: event.total });
    this.set(downloadPhase(this.names, this.seen));
  }

  private async run(): Promise<void> {
    this.names = [];
    this.seen = new Map();
    this.set({ kind: "checking" });
    try {
      const { models } = await this.api.status();
      const missing = models.filter((m) => !m.ready).map((m) => m.name);
      if (missing.length === 0) {
        this.set({ kind: "ready" });
        return;
      }
      this.names = missing;
      this.set(downloadPhase(missing, this.seen));
      const { ok } = await this.api.ensure(missing);
      this.set(ok ? { kind: "ready" } : { kind: "failed", get reason() { return t("models.001"); } });
    } catch (err) {
      this.set({ kind: "failed", get reason() { return describeError(err); } });
    }
  }

  private set(phase: ModelPhase): void {
    this.current = phase;
    this.onChange(phase);
  }
}
