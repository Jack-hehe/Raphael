// Subscription metadata only; authentication is handled by each CLI's own terminal.
import * as api from "./api";
import { t } from "./i18n";
import { providerMark } from "./provider-marks";
import type { CatalogModel, CliBrain, CliBrainId, MainEvent } from "./types";
import { btn, esc } from "./ui";

export const CLIS = [
  { id: "claude", name: "Claude Code", mark: "anthropic", install: "https://code.claude.com/docs/en/setup" },
  { id: "codex", name: "Codex", mark: "openai", install: "https://developers.openai.com/codex/cli/" },
  { id: "gemini", name: "Antigravity", mark: "google", install: "https://antigravity.google/download" },
] as const;
export interface CliBrainsView {
  clis: readonly CliBrain[];
  state: "loading" | "ready" | "failed";
  busy: CliBrainId | null;
  error: boolean;
}
export const emptyCliBrainsView = (): CliBrainsView => ({ clis: [], state: "loading", busy: null, error: false });
export function cliBrainLine(ref: string): string {
  const cli = CLIS.find((c) => ref === `cli:${c.id}` || ref.startsWith(`cli:${c.id}:`));
  return cli ? `<span class="mb-brain">${providerMark(cli.mark)}<span>${esc(cli.name)} · ${esc(t("brains.subscription"))}</span></span>` : "";
}
export function cliModels(clis: readonly CliBrain[], availableOnly = true): CatalogModel[] {
  return clis.filter((c) => !availableOnly || c.installed && c.logged_in === true).flatMap((c) => {
    const models = c.models.filter((m) => m.id !== `cli:${c.id}`).map((m) => ({ ...m, id: m.id.startsWith(`cli:${c.id}:`) ? m.id : `cli:${c.id}:${m.id}` }));
    return models.map((m) => ({
      ...m, provider: m.provider ?? { claude: "Anthropic", codex: "OpenAI", gemini: "Google" }[c.id],
      provider_id: `cli:${c.id}`, provider_name: `${CLIS.find((known) => known.id === c.id)!.name} ${t("brains.subscription")}`,
      input_per_mtok: null, output_per_mtok: null, per_minute: null, free: false, variable_price: false,
      context_length: null, created_at_ms: null, capabilities: {}, input_modalities: ["text"], roles: { answer: { usable: true, reason: null } },
    }));
  });
}
export function cliBrainsHTML(v: CliBrainsView): string {
  return `<section class="sgroup cli-accounts"><h2>${esc(t("brains.accounts"))}</h2><div class="pv-grid">` + CLIS.map((known) => {
    const cli = v.clis.find((c) => c.id === known.id);
    const status = v.state !== "ready" ? t(v.state === "loading" ? "common.loading" : "providers.error") :
      t(!cli?.installed ? "brains.missing" : cli.logged_in === true ? "brains.ready" : "brains.signedOut");
    const version = cli?.installed && cli.logged_in === true ? cli.version?.match(/\d+(?:\.\d+)+(?:[-+][\w.-]+)?/)?.[0] : null;
    const label = t(cli?.installed ? "brains.login" : "brains.install");
    const action = v.state !== "ready" || cli?.installed && cli.logged_in === true ? "" : btn(esc(label), "", ` data-cli="${known.id}" data-k="cli:${known.id}" aria-label="${esc(label + " " + known.name)}"${v.busy ? " disabled" : ""}`);
    return `<div class="pv-card cli-card"><span class="ic">${providerMark(known.mark)}</span><span class="tx"><span class="nm">${esc(known.name)}</span><span class="ds">${esc(status)}${version ? ` · v${esc(version)}` : ""}</span></span>${action}</div>`;
  }).join("") + `</div>${v.error ? `<p class="note" role="alert">${esc(t("providers.error"))}</p>` : ""}</section>`;
}

export function createCliBrains(host: { visible(): boolean; redraw(): void }) {
  let value = emptyCliBrainsView();
  let generation = 0;
  let timer: ReturnType<typeof setInterval> | null = null;
  async function load(): Promise<void> {
    const mine = ++generation;
    try {
      const { clis } = await api.brainsCli();
      if (mine !== generation) return;
      value = { ...value, clis, state: "ready" };
    } catch {
      if (mine !== generation) return;
      value = { ...value, state: "failed" };
    }
    if (host.visible()) host.redraw();
  }
  return {
    view: () => value,
    load,
    start() {
      if (timer) clearInterval(timer);
      void load();
      timer = setInterval(() => { if (host.visible()) void load(); }, 120_000);
    },
    stop() { if (timer) clearInterval(timer); timer = null; generation++; },
    followEvent(event: MainEvent) { if (event.type === "BrainsChanged" && host.visible()) void load(); },
    async click(target: Element): Promise<boolean> {
      const button = target.closest<HTMLButtonElement>("button[data-cli]");
      const known = CLIS.find((c) => c.id === button?.dataset.cli);
      if (!known) return false;
      if (value.busy || value.state !== "ready") return true;
      const cli = value.clis.find((c) => c.id === known.id);
      if (cli?.installed && cli.logged_in === true) return true;
      value = { ...value, busy: known.id, error: false }; host.redraw();
      try {
        if (cli?.installed) { await api.brainsCliLogin(known.id); await load(); }
        else await api.openExternalUrl(known.install);
      } catch { value = { ...value, error: true }; }
      finally { value = { ...value, busy: null }; if (host.visible()) host.redraw(); }
      return true;
    },
  };
}
