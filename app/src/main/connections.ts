// The 「連接」 tab of the settings: the tools Raphael can use, in groups. A list on the left (the
// overview, Raphael's own two groups, every program the owner connected, and the button that
// connects another) and the chosen one on the right: the overview, a group with the permission of
// each tool, the form that connects a program, or, right after one is connected, the step that
// decides what its new tools may do. `connectionsHTML` draws the tab from a snapshot (pure, so the
// owner's rules can be tested without a browser); `createConnections` holds the state and does what
// the buttons say, through the core. Nothing secret is kept here: the values of a variable or a
// header are typed, sent to the core's vault once and dropped; only their names are ever shown.

import * as api from "./api";
import { dayLabel, describeError, formatClock } from "./format";
import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { type ServiceIconId, monogram, serviceIconOf, serviceIconSVG } from "./service-icons";
import type { AuditEntry, ConnectionGroup, ConnectionTool, NewConnection, ToolTier } from "./types";
import { type Choice, btn, esc, fieldHTML, ico, row, st, tg, toaster } from "./ui";

/** `t` for a key that is chosen at run time (its placeholders, if any, are none). */
const tr = t as unknown as (key: MessageKey) => string;

export const TOOL_TIERS: readonly ToolTier[] = ["T0", "T1", "T2", "T3"];
/** The value of "no tier" in a dropdown: the tool is blocked. */
const BLOCKED = "";
/** How long 「移除」 waits for its second press. */
const REMOVAL_CONFIRM_MS = 5000;
/** What is selected on the left besides a group: the overview, or the form that adds one. */
export const OVERVIEW = "overview";
export const ADD = "add";

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

/** The step that decides what the new tools of a group may do. Nothing is granted until 「完成」. */
export interface ReviewDraft {
  id: string;
  /** The tier chosen so far for each tool to decide (null: block it); only a proposal. */
  tiers: Record<string, ToolTier | null>;
  busy: boolean;
}

export interface ConnectionsView {
  /** The groups: being read, read, or not to be had. */
  groups: ConnectionGroup[] | "loading" | "failed";
  audit: AuditEntry[];
  /** What the right side shows: `OVERVIEW`, `ADD` (the form) or the id of a group. */
  selected: string;
  /** The tools of the selected group waiting for the owner's decision, with what is proposed. */
  review: ReviewDraft | null;
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
  selected: OVERVIEW,
  review: null,
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

/** The friendly name of a tier, which the T level always goes with. */
const tierLabel = (tier: ToolTier | null): string => (tier === null ? t("conn.tier.none") : tr(TIER_KEYS[tier].label));

/**
 * The choices of a tier dropdown: block, then T0 to T3, each with the T level and what it means.
 * The one the server's read-only claim points at is tagged 「建議」: a suggestion, never a decision.
 */
export function tierChoices(suggested: ToolTier | null = null): Choice[] {
  return [
    { value: BLOCKED, label: t("conn.tier.none"), desc: t("conn.tier.noneDesc") },
    ...TOOL_TIERS.map((tier) => ({
      value: tier,
      label: tierLabel(tier),
      desc: tr(TIER_KEYS[tier].desc),
      detail: tier,
      ...(tier === suggested ? { tag: t("conn.suggested") } : {}),
    })),
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

/** How many tools of a group are still to decide: new, or described differently since. */
const pendingOf = (g: ConnectionGroup): number => g.pending_count ?? g.tools.filter((tool) => !tool.decided).length;

/** The tools the server says only read, and the others: the two lists of a group. */
const isReadOnly = (tool: ConnectionTool): boolean => tool.suggested !== null;
const partition = (tools: ConnectionTool[]): { write: ConnectionTool[]; read: ConnectionTool[] } => ({
  write: tools.filter((tool) => !isReadOnly(tool)),
  read: tools.filter(isReadOnly),
});

// ---------- pictures ----------

/** The picture of a group: its own line icon, the mark of a well-known program, or a letter. */
function iconHTML(g: ConnectionGroup, size: "sm" | "lg"): string {
  if (g.kind === "builtin") return `<span class="mono ${size} builtin">${ico(g.name === "web" ? "globe" : "monitor")}</span>`;
  const known: ServiceIconId | null = serviceIconOf({ name: g.name, command: g.command, args: g.args, url: g.url });
  return known
    ? `<span class="mono ${size} logo-mono">${serviceIconSVG(known)}</span>`
    : `<span class="mono ${size}" aria-hidden="true">${monogram(g.name)}</span>`;
}

// ---------- the list on the left ----------

/** The short line under a group's name in the list: a dot and a few words. */
function navStatus(g: ConnectionGroup): { kind: "ok" | "warn" | "err" | "idle" | ""; text: string } {
  if (g.kind === "builtin") return { kind: "", text: t("conn.nav.builtin", { p0: g.tools.length }) };
  const pending = pendingOf(g);
  switch (g.status) {
    case "error":
      return { kind: "err", text: t("conn.nav.error") };
    case "disabled":
      return { kind: "idle", text: t("conn.status.disabled") };
    case "unchecked":
      return { kind: "idle", text: t("conn.status.unchecked") };
    default:
      return pending > 0
        ? { kind: "warn", text: t("conn.nav.pending", { p0: pending }) }
        : { kind: "ok", text: t("conn.nav.ready", { p0: g.tool_count ?? g.tools.length, p1: g.allowed_count ?? 0 }) };
  }
}

function navItem(id: string, selected: boolean, icon: string, name: string, sub = "", subKind = ""): string {
  const status = sub
    ? `<span class="sub${subKind === "warn" || subKind === "err" ? " " + subKind : ""}">${subKind ? `<span class="dot ${subKind}"></span>` : ""}${esc(sub)}</span>`
    : "";
  return (
    `<button type="button" class="ca-item${id === OVERVIEW ? " overview" : ""}" data-conn="select" data-id="${esc(id)}" data-k="conn-nav:${esc(id)}"` +
    ` aria-current="${selected}">${icon}<span class="tx"><span class="nm">${esc(name)}</span>${status}</span></button>`
  );
}

function listHTML(v: ConnectionsView): string {
  const groups = Array.isArray(v.groups) ? v.groups : [];
  const group = (g: ConnectionGroup): string => {
    const s = navStatus(g);
    return navItem(g.id, v.selected === g.id, iconHTML(g, "sm"), groupName(g), s.text, s.kind);
  };
  const builtin = groups.filter((g) => g.kind === "builtin").map(group).join("");
  const mcp = groups.filter((g) => g.kind === "mcp");
  const servers = mcp.length
    ? mcp.map(group).join("")
    : Array.isArray(v.groups)
      ? `<p class="ca-none">${esc(t("conn.noServers"))}</p>`
      : "";
  return (
    `<div class="ca-list"><div class="ca-scroll">` +
    navItem(OVERVIEW, v.selected === OVERVIEW, ico("grid"), t("conn.nav.overview")) +
    (builtin ? `<div class="ca-sec">${esc(t("conn.sectionBuiltin"))}</div>${builtin}` : "") +
    `<div class="ca-sec">${esc(t("conn.sectionServers"))}</div>${servers}</div>` +
    `<div class="ca-foot"><button type="button" class="btn" data-conn="add" aria-current="${v.selected === ADD}">${ico("plus")}${esc(t("conn.add"))}</button></div></div>`
  );
}

// ---------- the permission dropdown ----------

/**
 * The button of a tier dropdown: the friendly name and, quieter, the T level. A tool nobody
 * decided on yet says 「未指定」 where a block the owner chose says nothing.
 */
function tierSelHTML(k: string, tier: ToolTier | null, decided: boolean, label: string, disabled = false): string {
  const name = tierLabel(tier);
  const tag = tier !== null ? tier : decided ? "" : t("conn.tier.noneTag");
  return (
    `<button type="button" class="sel tsel${tier === null ? " is-none" : ""}" aria-haspopup="menu" aria-expanded="false"` +
    ` aria-label="${esc(label)}" title="${esc(name)}" data-k="${esc(k)}"${disabled ? " disabled" : ""}>` +
    `<span class="tname">${esc(name)}</span>${tag ? `<span class="ttag">${esc(tag)}</span>` : ""}${ico("down")}</button>`
  );
}

/** A tier that is only shown: Raphael's own tools have theirs fixed. */
function tierStatic(tier: ToolTier | null, tag?: string): string {
  const shown = tag ?? (tier ?? "");
  return `<span class="tstatic"><span class="tname">${esc(tierLabel(tier))}</span>${shown ? `<span class="ttag">${esc(shown)}</span>` : ""}</span>`;
}

// ---------- the right side: a group ----------

const chip = (kind: "new" | "warn", text: string): string => `<span class="chip-tag ${kind}">${esc(text)}</span>`;

function toolRow(g: ConnectionGroup, tool: ConnectionTool, control: string): string {
  const name = toolName(g, tool);
  const marks =
    (!tool.decided && !tool.changed && g.kind === "mcp" ? chip("new", t("conn.new")) : "") +
    (tool.changed ? chip("warn", t("conn.changed")) : "");
  const suggest =
    tool.suggested !== null && tool.tier !== tool.suggested ? ` · ${esc(t("conn.suggestAs", { p0: tierLabel(tool.suggested) }))}` : "";
  const label = g.kind === "builtin" ? esc(name) : `<span class="code">${esc(name)}</span>`;
  return row(`${label}${marks}`, `${esc(tool.description)}${suggest}`, control, { cls: "conn-tool" });
}

function toolGroupHTML(g: ConnectionGroup, title: string, tools: ConnectionTool[], group: "read" | "write", busy: boolean): string {
  if (!tools.length) return "";
  const all = `<button type="button" class="sel" aria-haspopup="menu" aria-expanded="false" aria-label="${esc(t("conn.setAllFor", { p0: title }))}" data-k="conn-all:${esc(g.id)}:${group}"${busy ? " disabled" : ""}><span>${esc(t("conn.setAll"))}</span>${ico("down")}</button>`;
  return (
    `<div class="slist tgroup"><div class="ghead"><span class="gt">${esc(title)}<small>${esc(t("conn.group.count", { p0: tools.length }))}</small></span>${all}</div>` +
    tools
      .map((tool) =>
        toolRow(g, tool, tierSelHTML(`conn-tier:${g.id}:${tool.name}`, tool.tier, tool.decided, t("conn.tierOf", { p0: toolName(g, tool) }), busy)),
      )
      .join("") +
    `</div>`
  );
}

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

function serverHTML(g: ConnectionGroup, v: ConnectionsView): string {
  const busy = v.busy.has(g.id);
  const id = ` data-id="${esc(g.id)}"`;
  const head =
    `<div class="cd-head">${iconHTML(g, "lg")}<div class="tx"><h2 class="big">${esc(groupName(g))}</h2><div class="st-line">${statusHTML(g)}</div></div>` +
    btn(`${ico("refresh")}${esc(busy ? t("conn.refreshing") : t("conn.refresh"))}`, "sm", ` data-conn="refresh"${id}${busy ? " disabled" : ""}`) +
    btn(esc(v.confirmingRemoval === g.id ? t("conn.removeConfirm") : t("conn.remove")), "sm", ` data-conn="remove"${id}${busy ? " disabled" : ""}`) +
    tg(g.enabled, t("conn.switch", { p0: groupName(g) }), ` data-conn="enable"${id}${busy ? " disabled" : ""}`) +
    `</div>`;
  const where =
    g.transport === "http"
      ? `<span>${ico("link")}${esc(t("conn.meta.http"))} · <span class="code">${esc(g.url ?? "")}</span></span>`
      : `<span>${ico("monitor")}${esc(t("conn.meta.stdio"))} · <span class="code">${esc(g.command ?? "")}</span></span>`;
  const names = [...(g.env ?? []), ...(g.headers ?? [])];
  const secrets = names.length ? `<span>${ico("lock")}<span class="code">${esc(names.join(", "))}</span> ${esc(t("conn.meta.stored"))}</span>` : "";
  const pending = pendingOf(g);
  const alert = pending
    ? `<div class="ca-alert">${ico("info")}<span class="msg">${esc(t("conn.pending.some", { p0: pending }))}</span>${btn(esc(t("conn.review.open")), "sm", ` data-conn="review"${id}${busy ? " disabled" : ""}`)}</div>`
    : "";
  const { write, read } = partition(g.tools);
  const tools = g.tools.length
    ? toolGroupHTML(g, t("conn.group.write"), write, "write", busy) + toolGroupHTML(g, t("conn.group.read"), read, "read", busy)
    : `<div class="slist"><div class="note conn-empty">${esc(t("conn.noTools"))}</div></div>`;
  return (
    head +
    `<div class="cd-meta">${where}${secrets}</div>` +
    alert +
    `<div class="cd-tools-h"><h3>${esc(t("conn.tools.title"))}</h3><p>${esc(t("conn.tools.sub"))}</p></div>` +
    tools
  );
}

function builtinHTML(g: ConnectionGroup): string {
  const desc = t(g.name === "web" ? "conn.web.desc" : "conn.computer.desc");
  return (
    `<div class="cd-head">${iconHTML(g, "lg")}<div class="tx"><h2 class="big">${esc(groupName(g))}</h2><div class="st-line">${esc(desc)}</div></div></div>` +
    `<div class="cd-tools-h"><h3>${esc(t("conn.tools.title"))}</h3><p>${esc(t("conn.builtin.note"))}</p></div>` +
    `<div class="slist tgroup">${g.tools.map((tool) => toolRow(g, tool, tierStatic(tool.tier))).join("")}</div>`
  );
}

// ---------- the right side: deciding what the new tools may do ----------

function reviewHTML(g: ConnectionGroup, r: ReviewDraft): string {
  const todo = g.tools.filter((tool) => tool.name in r.tiers);
  const { write, read } = partition(todo);
  const list = (title: string, tools: ConnectionTool[]): string =>
    tools.length
      ? `<div class="slist tgroup"><div class="ghead"><span class="gt">${esc(title)}<small>${esc(t("conn.group.count", { p0: tools.length }))}</small></span></div>` +
        tools
          .map((tool) =>
            toolRow(g, tool, tierSelHTML(`conn-draft:${g.id}:${tool.name}`, r.tiers[tool.name] ?? null, true, t("conn.tierOf", { p0: toolName(g, tool) }), r.busy)),
          )
          .join("") +
        `</div>`
      : "";
  return (
    `<div class="cd-head">${iconHTML(g, "lg")}<div class="tx"><h2 class="big">${esc(t("conn.review.title"))}</h2>` +
    `<div class="st-line">${esc(t("conn.review.lead", { p0: groupName(g), p1: todo.length }))}</div></div></div>` +
    `<p class="cd-help">${esc(t("conn.review.help"))}</p>` +
    list(t("conn.group.write"), write) +
    list(t("conn.group.read"), read) +
    `<div class="cd-acts">${btn(esc(r.busy ? t("conn.review.saving") : t("conn.review.done")), "pri", ` data-conn="review-done"${r.busy ? " disabled" : ""}`)}` +
    `${btn(esc(t("conn.review.later")), "", ` data-conn="review-later"${r.busy ? " disabled" : ""}`)}</div>` +
    `<p class="foot cd-foot">${esc(t("conn.review.foot"))}</p>`
  );
}

// ---------- the right side: the form that connects a program ----------

const fld = (label: string, control: string, hint = "", forId = ""): string =>
  `<div class="fld">${forId ? `<label class="flabel" for="${forId}">${esc(label)}</label>` : `<span class="flabel">${esc(label)}</span>`}${hint ? `<span class="fhint">${esc(hint)}</span>` : ""}${control}</div>`;

function pairsHTML(f: ConnectionForm): string {
  const http = f.type === "http";
  const label = http ? t("conn.form.headers") : t("conn.form.env");
  const added = f.pairs
    .map(
      (p, i) =>
        `<div class="secret-row"><span class="nm">${esc(p.name)}</span><span class="where">${ico("lock")}${esc(t("conn.form.willStore"))}</span>` +
        btn(esc(t("conn.remove")), "sm", ` data-conn="pair-drop" data-i="${i}" aria-label="${esc(t("conn.form.pairDrop", { p0: p.name }))}"`) +
        `</div>`,
    )
    .join("");
  const adder =
    `<div class="pair">` +
    fieldHTML(http ? t("conn.form.headerName") : t("conn.form.envName"), f.pairName, { id: "conn-pair-name" }) +
    fieldHTML(t("conn.form.value"), f.pairValue, { id: "conn-pair-value", type: "password" }) +
    btn(esc(t("conn.form.pairAdd")), "", ' data-conn="pair-add"') +
    `</div>`;
  return fld(label, added + adder, t("conn.form.secretNote"));
}

function formHTML(f: ConnectionForm): string {
  const off = f.busy;
  const fields =
    f.type === "stdio"
      ? `<div class="two">` +
        fld(t("conn.form.command"), fieldHTML(t("conn.form.commandHint"), f.command, { id: "conn-command", label: t("conn.form.command"), disabled: off }), "", "conn-command") +
        fld(t("conn.form.args"), fieldHTML(t("conn.form.argsHint"), f.args, { id: "conn-args", label: t("conn.form.args"), disabled: off }), "", "conn-args") +
        `</div>`
      : fld(t("conn.form.url"), fieldHTML(t("conn.form.urlHint"), f.url, { id: "conn-url", label: t("conn.form.url"), disabled: off }), "", "conn-url");
  const error = f.error ? `<div class="note" role="alert">${st("err", f.error())}</div>` : "";
  const typeSel = `<button type="button" class="sel" aria-haspopup="menu" aria-expanded="false" aria-label="${esc(t("conn.form.type"))}" title="${esc(t(f.type === "stdio" ? "conn.type.stdio" : "conn.type.http"))}" data-k="conn-type"${off ? " disabled" : ""}><span>${esc(t(f.type === "stdio" ? "conn.type.stdio" : "conn.type.http"))}</span>${ico("down")}</button>`;
  return (
    `<h2 class="big">${esc(t("conn.add.title"))}</h2><p class="cd-help">${esc(t("conn.add.lead"))}</p>` +
    `<div class="add-grid">` +
    `<div class="two">${fld(t("conn.form.type"), typeSel)}${fld(t("conn.form.name"), fieldHTML(t("conn.form.nameHint"), f.name, { id: "conn-name", label: t("conn.form.name"), disabled: off }), "", "conn-name")}</div>` +
    fields +
    pairsHTML(f) +
    `<div class="trust">${ico("shield")}<span><b>${esc(t("conn.form.trustTitle"))}</b> ${esc(t("conn.form.trustBody"))}</span></div>` +
    error +
    `<div class="add-acts">` +
    btn(esc(off ? t("conn.form.connecting") : t("conn.form.connect")), "pri", ` data-conn="connect"${off ? " disabled" : ""}`) +
    btn(esc(t("conn.form.cancel")), "", ` data-conn="form-cancel"${off ? " disabled" : ""}`) +
    `</div></div>`
  );
}

// ---------- the right side: the overview ----------

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
    `<div class="slist audit">` +
    v.audit
      .map((e) => {
        const when = `${dayLabel(e.at_ms, v.now)} ${formatClock(e.at_ms)}`;
        const outcome = OUTCOMES[e.outcome] ?? OUTCOMES.error;
        const tier = e.tier === null ? t("conn.audit.noTier") : `${tierLabel(e.tier)} · ${e.tier}`;
        const who = tr(APPROVERS[e.approver] ?? APPROVERS.none);
        return row(`${esc(e.server)} / ${esc(e.tool)}`, esc(`${when} · ${tier} · ${who}`), st(outcome.kind, tr(outcome.key)), { cls: "conn-audit" });
      })
      .join("") +
    `</div>`
  );
}

function explainHTML(): string {
  const levels: [ToolTier | null, string][] = [
    [null, t("conn.tier.noneDesc")],
    ...TOOL_TIERS.map((tier): [ToolTier, string] => [tier, tr(TIER_KEYS[tier].desc)]),
  ];
  const rows = levels.map(
    ([tier, desc]) => `<div class="row">${tierStatic(tier, tier === null ? t("conn.tier.noneTag") : undefined)}<div class="d">${esc(desc)}</div></div>`,
  );
  return `<div class="slist legend">${rows.join("")}</div>`;
}

function overviewHTML(v: ConnectionsView): string {
  const waiting = (Array.isArray(v.groups) ? v.groups : []).filter((g) => g.kind === "mcp" && g.enabled && pendingOf(g) > 0);
  const alerts = waiting
    .map(
      (g) =>
        `<div class="ca-alert">${ico("info")}<span class="msg"><b>${esc(groupName(g))}</b>${esc(t("conn.pending.alert", { p0: pendingOf(g) }))}</span>` +
        btn(esc(t("conn.ov.decide")), "sm", ` data-conn="review" data-id="${esc(g.id)}"`) +
        `</div>`,
    )
    .join("");
  return (
    (alerts ? `<div class="sgroup ca-alerts">${alerts}</div>` : "") +
    `<div class="sgroup"><h2>${esc(t("conn.explain.title"))}</h2>${explainHTML()}<p class="foot">${esc(t("conn.explain.foot"))}</p></div>` +
    `<div class="sgroup"><h2>${esc(t("conn.audit.title"))}</h2>${auditHTML(v)}<p class="foot">${esc(t("conn.audit.foot"))}</p></div>`
  );
}

// ---------- the tab ----------

function detailHTML(v: ConnectionsView): string {
  if (v.groups === "loading") return `<p class="settings-loading" role="status">${esc(t("conn.loading"))}</p>`;
  if (v.groups === "failed") return `<div class="alert core-note">${st("err", t("conn.loadFailed"))}</div>`;
  if (v.selected === ADD && v.form) return formHTML(v.form);
  const g = v.groups.find((x) => x.id === v.selected);
  if (!g) return overviewHTML(v);
  if (v.review && v.review.id === g.id) return reviewHTML(g, v.review);
  return g.kind === "builtin" ? builtinHTML(g) : serverHTML(g, v);
}

/** The tab, below its name and purpose: the list and the chosen item. */
export function connectionsHTML(v: ConnectionsView): string {
  return `<div class="ca-split">${listHTML(v)}<div class="ca-detail" data-detail="${esc(v.selected)}">${detailHTML(v)}</div></div>`;
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
  let selected = OVERVIEW;
  let review: ReviewDraft | null = null;
  const busy = new Set<string>();
  let confirmingRemoval: string | null = null;
  let removalTimer: ReturnType<typeof setTimeout> | undefined;
  let form: ConnectionForm | null = null;
  let loads = 0;

  const redraw = (): void => {
    if (host.visible()) host.redraw();
  };

  const view = (): ConnectionsView => ({ groups, audit, selected, review, confirmingRemoval, busy, form, now: Date.now() });

  function groupById(id: string): ConnectionGroup | undefined {
    return Array.isArray(groups) ? groups.find((g) => g.id === id) : undefined;
  }

  async function load(): Promise<void> {
    const mine = ++loads;
    const [list, recent] = await Promise.allSettled([api.toolsetsList(), api.integrationsAudit()]);
    if (mine !== loads) return;
    if (list.status === "fulfilled") {
      groups = list.value.groups;
      // what was selected is gone (removed from elsewhere): back to the overview
      if (selected !== OVERVIEW && selected !== ADD && !groupById(selected)) selected = OVERVIEW;
      if (review && !groupById(review.id)) review = null;
    } else if (!Array.isArray(groups)) groups = "failed";
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
      if (selected === id) selected = OVERVIEW;
      if (review?.id === id) review = null;
      toaster.info(t("conn.removed"));
    });
  }

  /** The tools to decide, each with what the server suggests (blocked when it suggests nothing). */
  function startReview(g: ConnectionGroup): ReviewDraft {
    const tiers: Record<string, ToolTier | null> = {};
    for (const tool of g.tools) if (!tool.decided) tiers[tool.name] = tool.suggested;
    return { id: g.id, tiers, busy: false };
  }

  /** Gives the tools the tier chosen for them, one call for each tier, and nothing before this. */
  async function finishReview(): Promise<void> {
    const r = review;
    if (!r || r.busy) return;
    r.busy = true;
    redraw();
    const byTier = new Map<ToolTier | null, string[]>();
    for (const [tool, tier] of Object.entries(r.tiers)) byTier.set(tier, [...(byTier.get(tier) ?? []), tool]);
    try {
      for (const [tier, tools] of byTier) await api.integrationsSetGroupTier(r.id, tier, tools);
      if (review === r) review = null;
      toaster.info(t("conn.review.saved"));
    } catch (err) {
      r.busy = false;
      toaster.info(describeError(err));
    }
    await load();
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
      selected = added.server.id;
      let read = true;
      try {
        await api.integrationsRefresh(added.server.id);
      } catch {
        read = false; // the group says it could not read the tools
      }
      await load();
      // Its tools are new, so blocked: the owner decides now, or nothing of it does anything.
      const group = groupById(added.server.id);
      if (read && group && pendingOf(group) > 0) review = startReview(group);
      toaster.info(t("conn.connected", { p0: name }));
      redraw();
      return;
    } catch (err) {
      f.busy = false;
      const text = describeError(err);
      f.error = () => text;
    }
    await load();
    redraw();
  }

  /** Shows another item on the right; what was typed in the form is dropped with it. */
  function select(id: string): void {
    if (form?.busy) return;
    clearTimeout(removalTimer);
    confirmingRemoval = null;
    form = null;
    if (review?.id !== id) review = null;
    selected = id;
    redraw();
  }

  function click(target: Element): boolean {
    const button = target.closest<HTMLElement>("[data-conn]");
    if (!button) return false;
    const id = button.dataset.id ?? "";
    switch (button.dataset.conn) {
      case "select":
        select(id);
        break;
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
      case "review": {
        const group = groupById(id);
        if (group && pendingOf(group) > 0) {
          select(id);
          review = startReview(group);
          redraw();
        }
        break;
      }
      case "review-done":
        void finishReview();
        break;
      case "review-later":
        review = null; // nothing was granted: the tools stay to decide
        redraw();
        break;
      case "add":
        if (form?.busy) break;
        select(ADD);
        form = emptyForm();
        redraw();
        focusId("conn-name");
        break;
      case "form-cancel":
        form = null; // an unsent value is dropped with the form
        selected = OVERVIEW;
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
      case "conn-all": {
        // one tier for the tools of one list of the group, the ones it has now
        const group = groupById(id);
        if (!group) return null;
        const part = partition(group.tools)[rest[0] === "read" ? "read" : "write"].map((tool) => tool.name);
        return {
          choices: tierChoices(),
          current: t("conn.setAll"), // nothing is checked: it is an act, not a state
          pick: (value) => {
            if (part.length) void act(id, async () => void (await api.integrationsSetGroupTier(id, tierOf(value), part)));
          },
        };
      }
      case "conn-tier": {
        const name = rest.join(":");
        const tool = groupById(id)?.tools.find((x) => x.name === name);
        const current = tool?.tier ?? BLOCKED;
        return {
          choices: tierChoices(tool?.suggested ?? null),
          current,
          pick: (value) => {
            // A tool nobody decided on is decided by a pick of the block too.
            if (value !== current || tool?.decided === false) void act(id, async () => void (await api.integrationsSetTier(id, name, tierOf(value))));
          },
        };
      }
      case "conn-draft": {
        const name = rest.join(":");
        const r = review;
        if (!r || r.id !== id || !(name in r.tiers)) return null;
        const tool = groupById(id)?.tools.find((x) => x.name === name);
        return {
          choices: tierChoices(tool?.suggested ?? null),
          current: r.tiers[name] ?? BLOCKED,
          pick: (value) => {
            r.tiers[name] = tierOf(value);
            redraw();
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
    review = null;
    selected = OVERVIEW;
    loads++;
  }

  return { view, load, click, input, key, menuFor, reset };
}
