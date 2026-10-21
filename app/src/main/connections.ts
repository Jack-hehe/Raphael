// The 「連接」 tab of the settings: the tools Raphael can use, in groups. Raphael's own tools are two
// groups that are only shown (their tiers are fixed); every program the owner connects is one more
// group, with a switch, one tier for all its tools, a tier for each tool, and buttons to read its
// tools again and to remove it. `connectionsHTML` draws the tab from a snapshot (pure, so the
// owner's rules can be tested without a browser); `createConnections` holds the state and does
// what the buttons say, through the core. Nothing secret is kept here: the values of a variable or
// a header are typed, sent to the core's vault once and dropped; only their names are ever shown.

import * as api from "./api";
import { dayLabel, describeError, formatClock } from "./format";
import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import type { AuditEntry, ConnectionGroup, ConnectionTool, NewConnection, ToolTier } from "./types";
import { type Choice, btn, esc, fieldHTML, row, sel, st, tg, toaster } from "./ui";

/** `t` for a key that is chosen at run time (its placeholders, if any, are none). */
const tr = t as unknown as (key: MessageKey) => string;

export const TOOL_TIERS: readonly ToolTier[] = ["T0", "T1", "T2", "T3"];
/** The value of "no tier" in a dropdown: the tool is blocked. */
const BLOCKED = "";
/** How long 「移除」 waits for its second press. */
const REMOVAL_CONFIRM_MS = 5000;

// ---------- what the tab is drawn from ----------

export interface PairDraft {
  name: string;
  value: string;
}

/** The form that connects a program. */
export interface ConnectionForm {
  type: "stdio" | "http";
  name: string;
  command: string;
  args: string;
  url: string;
  /** The variables (or headers) added so far; the page shows their names and never their values. */
  pairs: PairDraft[];
  pairName: string;
  pairValue: string;
  busy: boolean;
  error: (() => string) | null;
}

export interface ConnectionsView {
  /** The groups: being read, read, or not to be had. */
  groups: ConnectionGroup[] | "loading" | "failed";
  audit: AuditEntry[];
  /** The groups whose tools are listed. */
  expanded: ReadonlySet<string>;
  /** The group whose 「移除」 was pressed once. */
  confirmingRemoval: string | null;
  /** The groups a call is under way for. */
  busy: ReadonlySet<string>;
  form: ConnectionForm | null;
  now: number;
}

export const emptyConnectionsView = (): ConnectionsView => ({
  groups: "loading",
  audit: [],
  expanded: new Set(),
  confirmingRemoval: null,
  busy: new Set(),
  form: null,
  now: Date.now(),
});

const emptyForm = (): ConnectionForm => ({
  type: "stdio",
  name: "",
  command: "",
  args: "",
  url: "",
  pairs: [],
  pairName: "",
  pairValue: "",
  busy: false,
  error: null,
});

// ---------- words ----------

const TIER_KEYS: Record<ToolTier, { label: MessageKey; desc: MessageKey }> = {
  T0: { label: "conn.tier.T0", desc: "conn.tier.T0Desc" },
  T1: { label: "conn.tier.T1", desc: "conn.tier.T1Desc" },
  T2: { label: "conn.tier.T2", desc: "conn.tier.T2Desc" },
  T3: { label: "conn.tier.T3", desc: "conn.tier.T3Desc" },
};

const tierText = (tier: ToolTier): { label: string; desc: string } => ({
  label: tr(TIER_KEYS[tier].label),
  desc: tr(TIER_KEYS[tier].desc),
});

/** The choices of a tier dropdown: not set (blocked), then T0 to T3 with what each one means. */
export function tierChoices(): Choice[] {
  return [
    { value: BLOCKED, label: t("conn.tier.none"), desc: t("conn.tier.noneDesc") },
    ...TOOL_TIERS.map((tier) => ({ value: tier, ...tierText(tier) })),
  ];
}

const typeChoices = (): Choice[] => [
  { value: "stdio", label: t("conn.type.stdio") },
  { value: "http", label: t("conn.type.http") },
];

/** The name of a group: Raphael's own are translated, a program's is what the owner called it. */
function groupName(g: ConnectionGroup): string {
  if (g.kind !== "builtin") return g.name;
  return g.name === "web" ? t("conn.web.name") : t("conn.computer.name");
}

/** The tools of Raphael's own groups, each with its name in every language. */
const BUILTIN_TOOLS: Record<string, MessageKey> = {
  weather: "conn.tool.weather",
  web_search: "conn.tool.web_search",
  local_time: "conn.tool.local_time",
  open_youtube: "conn.tool.open_youtube",
  open_gmail: "conn.tool.open_gmail",
  open_github: "conn.tool.open_github",
  open_google: "conn.tool.open_google",
  open_notion: "conn.tool.open_notion",
  open_chatgpt: "conn.tool.open_chatgpt",
  open_claude: "conn.tool.open_claude",
  open_google_calendar: "conn.tool.open_google_calendar",
  open_google_drive: "conn.tool.open_google_drive",
  volume_up: "conn.tool.volume_up",
  volume_down: "conn.tool.volume_down",
  volume_mute: "conn.tool.volume_mute",
  media_play_pause: "conn.tool.media_play_pause",
  media_next: "conn.tool.media_next",
  media_previous: "conn.tool.media_previous",
  lock_screen: "conn.tool.lock_screen",
  open_app: "conn.tool.open_app",
  copy_text: "conn.tool.copy_text",
};

/** The name of a tool: Raphael's own are translated, a program's is its own. */
function toolName(g: ConnectionGroup, tool: ConnectionTool): string {
  const key = g.kind === "builtin" ? BUILTIN_TOOLS[tool.name] : undefined;
  return key ? tr(key) : tool.name;
}

const FAILURES: Record<string, MessageKey> = {
  not_found: "conn.err.not_found",
  timeout: "conn.err.timeout",
  auth: "conn.err.auth",
  network: "conn.err.network",
  protocol: "conn.err.protocol",
  missing_secret: "conn.err.missing_secret",
  changed: "conn.err.changed",
  gone: "conn.err.gone",
  error: "conn.err.error",
};

const failureText = (kind: string | null): string => tr(FAILURES[kind ?? ""] ?? FAILURES.error);

function statusHTML(g: ConnectionGroup): string {
  switch (g.status) {
    case "builtin":
      return st("ok", t("conn.status.builtin"));
    case "ready":
      return st("ok", t("conn.status.ready", { p0: g.tool_count ?? g.tools.length, p1: g.allowed_count ?? 0 }));
    case "unchecked":
      return st("idle", t("conn.status.unchecked"));
    case "disabled":
      return st("idle", t("conn.status.disabled"));
    case "error":
      return st("err", t("conn.status.error", { p0: failureText(g.error) }));
  }
}

// ---------- drawing ----------

const block = (title: string, inner: string, foot = ""): string =>
  `<section class="sgroup"><h2>${esc(title)}</h2>${inner}${foot ? `<p class="foot">${esc(foot)}</p>` : ""}</section>`;

function toolRow(g: ConnectionGroup, tool: ConnectionTool): string {
  const name = toolName(g, tool);
  const hint = tool.suggested !== null && tool.tier !== tool.suggested ? `<span class="conn-hint">${esc(t("conn.suggest"))}</span>` : "";
  const changed = tool.changed ? `<span class="conn-hint warn">${esc(t("conn.changed"))}</span>` : "";
  const control = g.tier_fixed
    ? `<span class="val num">${esc(tool.tier === null ? t("conn.tier.none") : tierText(tool.tier).label)}</span>`
    : sel(tierChoices(), tool.tier ?? BLOCKED, t("conn.tierOf", { p0: name }), ` data-k="conn-tier:${esc(g.id)}:${esc(tool.name)}"`);
  return row(`${esc(name)}${hint}${changed}`, esc(tool.description), control, { cls: "conn-tool" });
}

function groupCard(g: ConnectionGroup, v: ConnectionsView): string {
  const open = v.expanded.has(g.id);
  const busy = v.busy.has(g.id);
  const id = ` data-id="${esc(g.id)}"`;
  const builtin = g.kind === "builtin";
  const meta = builtin
    ? esc(t(g.name === "web" ? "conn.web.desc" : "conn.computer.desc"))
    : esc([g.transport === "http" ? g.url : g.command, ...(g.env ?? []), ...(g.headers ?? [])].filter(Boolean).join(" · "));
  const head = row(
    esc(groupName(g)),
    `${statusHTML(g)}${meta ? `<div class="conn-meta">${meta}</div>` : ""}`,
    g.switchable ? tg(g.enabled, t("conn.switch", { p0: groupName(g) }), ` data-conn="enable"${id}${busy ? " disabled" : ""}`) : "",
    { cls: "conn-head" },
  );
  const count = g.tools.length;
  const expand = btn(open ? esc(t("conn.hideTools")) : esc(t("conn.showTools", { p0: count })), "", ` data-conn="expand"${id} aria-expanded="${open}"`);
  const actions = builtin
    ? expand
    : sel([], t("conn.setAll"), t("conn.setAll"), ` data-k="conn-all:${esc(g.id)}"${busy ? " disabled" : ""}`) +
      btn(esc(busy ? t("conn.refreshing") : t("conn.refresh")), "", ` data-conn="refresh"${id}${busy ? " disabled" : ""}`) +
      btn(esc(v.confirmingRemoval === g.id ? t("conn.removeConfirm") : t("conn.remove")), "", ` data-conn="remove"${id}${busy ? " disabled" : ""}`) +
      expand;
  const tools = open
    ? g.tools.length
      ? g.tools.map((tool) => toolRow(g, tool)).join("")
      : `<div class="note conn-empty">${esc(t("conn.noTools"))}</div>`
    : "";
  return `<section class="conn-group" data-group="${esc(g.id)}"><div class="slist">${head}<div class="conn-actions">${actions}</div>${tools}</div></section>`;
}

function pairsHTML(f: ConnectionForm): string {
  const http = f.type === "http";
  const label = http ? t("conn.form.headers") : t("conn.form.env");
  const added = f.pairs
    .map((p, i) => row(esc(p.name), "", btn(esc(t("conn.remove")), "sm", ` data-conn="pair-drop" data-i="${i}" aria-label="${esc(t("conn.form.pairDrop", { p0: p.name }))}"`), { cls: "conn-pair" }))
    .join("");
  const adder = row(
    esc(label),
    esc(t("conn.form.secretNote")),
    fieldHTML(http ? t("conn.form.headerName") : t("conn.form.envName"), f.pairName, { w: 170, id: "conn-pair-name" }) +
      fieldHTML(t("conn.form.value"), f.pairValue, { w: 170, id: "conn-pair-value", type: "password" }) +
      btn(esc(t("conn.form.pairAdd")), "", ' data-conn="pair-add"'),
    { cls: "conn-adder" },
  );
  return adder + added;
}

function formHTML(f: ConnectionForm): string {
  const off = f.busy;
  const fields =
    f.type === "stdio"
      ? row(esc(t("conn.form.command")), "", fieldHTML(t("conn.form.commandHint"), f.command, { w: 260, id: "conn-command", label: t("conn.form.command"), disabled: off })) +
        row(esc(t("conn.form.args")), "", fieldHTML(t("conn.form.argsHint"), f.args, { w: 260, id: "conn-args", label: t("conn.form.args"), disabled: off }))
      : row(esc(t("conn.form.url")), "", fieldHTML(t("conn.form.urlHint"), f.url, { w: 260, id: "conn-url", label: t("conn.form.url"), disabled: off }));
  const error = f.error ? `<div class="note" role="alert">${st("err", f.error())}</div>` : "";
  return (
    `<div class="slist conn-form">` +
    row(esc(t("conn.form.type")), "", sel(typeChoices(), f.type, t("conn.form.type"), ' data-k="conn-type"')) +
    row(esc(t("conn.form.name")), "", fieldHTML(t("conn.form.nameHint"), f.name, { w: 260, id: "conn-name", label: t("conn.form.name"), disabled: off })) +
    fields +
    pairsHTML(f) +
    error +
    `<div class="conn-actions">` +
    btn(esc(off ? t("conn.form.connecting") : t("conn.form.connect")), "pri", ` data-conn="connect"${off ? " disabled" : ""}`) +
    btn(esc(t("conn.form.cancel")), "", ` data-conn="form-cancel"${off ? " disabled" : ""}`) +
    `</div></div>`
  );
}

const OUTCOMES = {
  ok: { kind: "ok", key: "conn.audit.ok" },
  error: { kind: "err", key: "conn.audit.error" },
  timeout: { kind: "warn", key: "conn.audit.timeout" },
  denied: { kind: "idle", key: "conn.audit.denied" },
  expired: { kind: "idle", key: "conn.audit.expired" },
  blocked: { kind: "warn", key: "conn.audit.blocked" },
} as const satisfies Record<AuditEntry["outcome"], { kind: "ok" | "warn" | "err" | "idle"; key: MessageKey }>;

const APPROVERS: Record<AuditEntry["approver"], MessageKey> = {
  auto: "conn.audit.auto",
  user: "conn.audit.user",
  none: "conn.audit.none",
};

function auditHTML(v: ConnectionsView): string {
  if (!v.audit.length) return `<div class="slist">${row(esc(t("conn.audit.empty")))}</div>`;
  return (
    `<div class="slist">` +
    v.audit
      .map((e) => {
        const when = `${dayLabel(e.at_ms, v.now)} ${formatClock(e.at_ms)}`;
        const outcome = OUTCOMES[e.outcome] ?? OUTCOMES.error;
        const tier = e.tier === null ? t("conn.audit.noTier") : e.tier;
        const who = tr(APPROVERS[e.approver] ?? APPROVERS.none);
        return row(`${esc(e.server)} / ${esc(e.tool)}`, esc(`${when} · ${tier} · ${who}`), st(outcome.kind, tr(outcome.key)), { cls: "conn-audit" });
      })
      .join("") +
    `</div>`
  );
}

function explainHTML(): string {
  const blocked = row(esc(t("conn.tier.none")), esc(t("conn.tier.noneDesc")));
  return `<div class="slist">${blocked}${TOOL_TIERS.map((tier) => row(esc(tierText(tier).label), esc(tierText(tier).desc))).join("")}</div>`;
}

/** The tab, below its name and purpose. */
export function connectionsHTML(v: ConnectionsView): string {
  let builtin = "";
  let servers = "";
  if (v.groups === "loading") builtin = `<p class="settings-loading" role="status">${esc(t("conn.loading"))}</p>`;
  else if (v.groups === "failed") builtin = `<div class="alert core-note">${st("err", t("conn.loadFailed"))}</div>`;
  else {
    builtin = v.groups.filter((g) => g.kind === "builtin").map((g) => groupCard(g, v)).join("");
    const mcp = v.groups.filter((g) => g.kind === "mcp");
    servers = mcp.length || v.form ? mcp.map((g) => groupCard(g, v)).join("") : `<div class="slist">${row(esc(t("conn.noServers")))}</div>`;
  }
  const bar = `<div class="conn-bar">${v.form ? "" : btn(esc(t("conn.add")), "pri", ' data-conn="add"')}</div>`;
  return (
    block(t("conn.sectionBuiltin"), builtin) +
    `<section class="sgroup"><h2>${esc(t("conn.sectionServers"))}</h2>${bar}${v.form ? formHTML(v.form) : ""}${servers}</section>` +
    block(t("conn.audit.title"), auditHTML(v), t("conn.audit.foot")) +
    block(t("conn.explain.title"), explainHTML(), t("conn.explain.foot"))
  );
}

// ---------- doing ----------

const focusId = (id: string): void => {
  if (typeof document !== "undefined") document.getElementById(id)?.focus();
};

/** A dropdown's menu, as the settings panel opens it. */
export interface ConnectionMenu {
  choices: Choice[];
  current: string;
  pick: (value: string) => void;
}

export interface ConnectionsHost {
  /** The tab is on screen. */
  visible(): boolean;
  /** Draws the page again. */
  redraw(): void;
}

export interface Connections {
  view(): ConnectionsView;
  /** Reads the groups and the latest actions. */
  load(): Promise<void>;
  /** A click on one of this tab's buttons or switches; false for anything else. */
  click(target: Element): boolean;
  /** Something typed in one of the form's fields; false for any other input. */
  input(input: HTMLInputElement): boolean;
  /** Enter in one of the form's fields; false for any other key. */
  key(e: KeyboardEvent): boolean;
  /** The menu of a dropdown of this tab, by its `data-k`. */
  menuFor(k: string): ConnectionMenu | null;
  /** The panel is closed or leaves the tab: drop what must not stay (an unsent form, a pending removal). */
  reset(): void;
}

export function createConnections(host: ConnectionsHost): Connections {
  let groups: ConnectionsView["groups"] = "loading";
  let audit: AuditEntry[] = [];
  const expanded = new Set<string>();
  const busy = new Set<string>();
  let confirmingRemoval: string | null = null;
  let removalTimer: ReturnType<typeof setTimeout> | undefined;
  let form: ConnectionForm | null = null;
  let loads = 0;

  const redraw = (): void => {
    if (host.visible()) host.redraw();
  };

  const view = (): ConnectionsView => ({ groups, audit, expanded, confirmingRemoval, busy, form, now: Date.now() });

  async function load(): Promise<void> {
    const mine = ++loads;
    const [list, recent] = await Promise.allSettled([api.toolsetsList(), api.integrationsAudit()]);
    if (mine !== loads) return;
    if (list.status === "fulfilled") groups = list.value.groups;
    else if (!Array.isArray(groups)) groups = "failed";
    if (recent.status === "fulfilled") audit = recent.value.entries;
    redraw();
  }

  /** Does `work` for a group (its buttons wait meanwhile), says what went wrong, and reads everything again. */
  async function act(id: string, work: () => Promise<void>): Promise<void> {
    busy.add(id);
    redraw();
    try {
      await work();
    } catch (err) {
      toaster.info(describeError(err));
    }
    busy.delete(id);
    await load();
  }

  function removal(id: string): void {
    clearTimeout(removalTimer);
    if (confirmingRemoval !== id) {
      confirmingRemoval = id;
      removalTimer = setTimeout(() => {
        confirmingRemoval = null;
        redraw();
      }, REMOVAL_CONFIRM_MS);
      redraw();
      return;
    }
    confirmingRemoval = null;
    void act(id, async () => {
      await api.integrationsRemove(id);
      expanded.delete(id);
      toaster.info(t("conn.removed"));
    });
  }

  function groupById(id: string): ConnectionGroup | undefined {
    return Array.isArray(groups) ? groups.find((g) => g.id === id) : undefined;
  }

  function addPair(f: ConnectionForm): boolean {
    const name = f.pairName.trim();
    if (!name || !f.pairValue) return false;
    f.pairs.push({ name, value: f.pairValue });
    f.pairName = "";
    f.pairValue = "";
    return true;
  }

  async function connect(): Promise<void> {
    const f = form;
    if (!f || f.busy) return;
    const half = (f.pairName.trim() !== "") !== (f.pairValue !== "");
    if (half) {
      f.error = () => t("conn.form.needPair");
      redraw();
      return;
    }
    addPair(f);
    const missing = !f.name.trim()
      ? "conn.form.needName"
      : f.type === "stdio" && !f.command.trim()
        ? "conn.form.needCommand"
        : f.type === "http" && !f.url.trim()
          ? "conn.form.needUrl"
          : null;
    if (missing) {
      f.error = () => t(missing);
      redraw();
      return;
    }
    const pairs = f.pairs.map((p) => ({ name: p.name, value: p.value }));
    const request: NewConnection =
      f.type === "stdio"
        ? { name: f.name.trim(), transport: "stdio", command: f.command.trim(), args: f.args.split(/\s+/).filter(Boolean), env: pairs }
        : { name: f.name.trim(), transport: "http", url: f.url.trim(), headers: pairs };
    f.busy = true;
    f.error = null;
    redraw();
    try {
      const added = await api.integrationsAdd(request);
      const name = request.name;
      form = null; // the values were sent to the core's vault: nothing of them stays here
      expanded.add(added.server.id);
      try {
        await api.integrationsRefresh(added.server.id);
      } catch {
        // the group says it could not read the tools
      }
      toaster.info(t("conn.connected", { p0: name }));
    } catch (err) {
      f.busy = false;
      const text = describeError(err);
      f.error = () => text;
    }
    await load();
    redraw();
  }

  function click(target: Element): boolean {
    const button = target.closest<HTMLElement>("[data-conn]");
    if (!button) return false;
    const id = button.dataset.id ?? "";
    switch (button.dataset.conn) {
      case "enable": {
        const group = groupById(id);
        if (group) void act(id, async () => void (await api.integrationsSetEnabled(id, !group.enabled)));
        break;
      }
      case "refresh":
        void act(id, async () => {
          await api.integrationsRefresh(id);
          toaster.info(t("conn.refreshed"));
        });
        break;
      case "remove":
        removal(id);
        break;
      case "expand":
        if (!expanded.delete(id)) expanded.add(id);
        redraw();
        break;
      case "add":
        form = emptyForm();
        redraw();
        focusId("conn-name");
        break;
      case "form-cancel":
        form = null; // an unsent value is dropped with the form
        redraw();
        break;
      case "pair-add":
        if (form) {
          addPair(form);
          redraw();
          focusId("conn-pair-name");
        }
        break;
      case "pair-drop":
        if (form) {
          form.pairs.splice(Number(button.dataset.i), 1);
          redraw();
        }
        break;
      case "connect":
        void connect();
        break;
      default:
        return false;
    }
    return true;
  }

  function input(el: HTMLInputElement): boolean {
    if (!form || !el.id.startsWith("conn-")) return false;
    switch (el.id) {
      case "conn-name":
        form.name = el.value;
        break;
      case "conn-command":
        form.command = el.value;
        break;
      case "conn-args":
        form.args = el.value;
        break;
      case "conn-url":
        form.url = el.value;
        break;
      case "conn-pair-name":
        form.pairName = el.value;
        break;
      case "conn-pair-value":
        form.pairValue = el.value;
        break;
      default:
        return false;
    }
    return true;
  }

  function key(e: KeyboardEvent): boolean {
    const el = e.target as HTMLElement | null;
    if (e.key !== "Enter" || !form || !el?.id?.startsWith("conn-")) return false;
    e.preventDefault();
    if (el.id.startsWith("conn-pair")) {
      addPair(form);
      redraw();
      focusId("conn-pair-name");
    } else void connect();
    return true;
  }

  function menuFor(k: string): ConnectionMenu | null {
    const [kind, id, ...rest] = k.split(":");
    const tierOf = (value: string): ToolTier | null => (TOOL_TIERS.includes(value as ToolTier) ? (value as ToolTier) : null);
    switch (kind) {
      case "conn-type":
        return form
          ? {
              choices: typeChoices(),
              current: form.type,
              pick: (value) => {
                if (form && (value === "stdio" || value === "http") && value !== form.type) {
                  form.type = value;
                  form.pairs = []; // variables and headers are different things
                  form.pairName = "";
                  form.pairValue = "";
                  redraw();
                }
              },
            }
          : null;
      case "conn-all":
        return {
          choices: tierChoices(),
          current: t("conn.setAll"), // nothing is checked: it is an act, not a state
          pick: (value) => void act(id, async () => void (await api.integrationsSetGroupTier(id, tierOf(value)))),
        };
      case "conn-tier": {
        const tool = rest.join(":");
        const current = groupById(id)?.tools.find((x) => x.name === tool)?.tier ?? BLOCKED;
        return {
          choices: tierChoices(),
          current,
          pick: (value) => {
            if (value !== current) void act(id, async () => void (await api.integrationsSetTier(id, tool, tierOf(value))));
          },
        };
      }
      default:
        return null;
    }
  }

  function reset(): void {
    clearTimeout(removalTimer);
    confirmingRemoval = null;
    form = null;
    loads++;
  }

  return { view, load, click, input, key, menuFor, reset };
}
