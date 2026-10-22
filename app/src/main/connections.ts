// The 「連接」 tab of the settings, drawn like Codex's page of plugins: the services Raphael can use, in
// two views under one bar, 「已安裝」 and 「商店」, with a search. 「已安裝」 is one rounded list, a row for
// each connected service: its mark, its name, a line about it, a switch for the whole service and a
// chevron. A click on a row opens the service's dialog over the settings: its switch, what it is,
// how many tools it has, a row for the tools that change something and one for those that only read
// (each opening, in the dialog, the tools with a switch each and one for the whole list), 「試試看」,
// and at the foot what is done less often (sign in again, read the tools again, disconnect) and the
// latest actions, folded. 「商店」 is the services that can be added with one login in the browser,
// as cards in two columns, each opening a page that says what it is for. A server of the user's own
// is added from the 「新增」 menu beside the tab's name.
//
// `connectionsHTML` draws the tab from a snapshot (pure, so the rules of the tab can be tested
// without a browser); `createConnections` holds the state and does what the buttons say, through the
// core. Nothing secret is kept here: the login happens in the browser and its tokens stay in the
// core's vault; the values of a variable or a header are typed, sent to the vault once and dropped;
// only their names are ever shown.
//
// A tool is on until the user turns it off, and one that is on runs without asking. There are no
// levels to choose: that is the whole of the permission.

import * as api from "./api";
import { dayLabel, describeError, formatClock } from "./format";
import { getLocale, t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { SERVICE_ICONS, type ServiceIconId, brandStyle, hasLogo, monogram, serviceIconOf, serviceIconSVG, serviceLogoHTML } from "./service-icons";
import type { AuditEntry, ConnectionGroup, ConnectionTool, MainEvent, NewConnection, StoreService, Words } from "./types";
import { DISCONNECT_CONFIRM_MS, type Choice, btn, copyText, esc, fieldHTML, ico, iconButton, leave, row, st, tabs, tg, toaster } from "./ui";

/** `t` for a key that is chosen at run time (its placeholders, if any, are none). */
const tr = t as unknown as (key: MessageKey) => string;

/** Where a service that is signed in to with a code keeps the list of the apps it has let in. */
const GRANTS_URL: Record<string, string> = { github: "https://github.com/settings/applications" };

/** How many of the latest actions the dialog of a service lists. */
const AUDIT_SHOWN = 5;
/** How many of the latest actions are read, to find each service's own among them. */
const AUDIT_READ = 50;
/** How long the dialog of a service takes to go (styles.css: the way out of `.cs-scrim`). */
const DIALOG_OUT_MS = 140;

// ---------- what the tab is drawn from ----------

export type Tab = "installed" | "store";
export type ToolKind = ConnectionTool["kind"];

export interface PairDraft {
  name: string;
  value: string;
}

/** The form that connects a server of the user's own. */
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

/** The login in the browser that is under way, and what it is for: a store entry's id, or a connected service's. */
export interface Login {
  target: string;
  name: string;
}

/** The code of a device login that is waiting for the user to type it at the service. */
export interface DeviceCode {
  catalog_id: string;
  user_code: string;
  verification_uri: string;
}

export interface ConnectionsView {
  /** The connected services: being read, read, or not to be had. */
  groups: ConnectionGroup[] | "loading" | "failed";
  /** The store, the same way. */
  catalog: StoreService[] | "loading" | "failed";
  audit: AuditEntry[];
  tab: Tab;
  /** What was typed in the search box. */
  search: string;
  /** What is open: the dialog of a connected service over the list, or the page of a store entry in its place. */
  open: { tab: Tab; id: string } | null;
  /** In the dialog of a service, the list of tools that is shown in place of its summary. */
  toolsKind: ToolKind | null;
  /** The dialog's latest actions are unfolded. */
  auditOpen: boolean;
  /** The service whose 「中斷連線」 was pressed once. */
  confirmingDisconnect: string | null;
  /** The services a call is under way for. */
  busy: ReadonlySet<string>;
  login: Login | null;
  /** The code to type, while a device login waits for it. */
  device: DeviceCode | null;
  /** What went wrong with the latest login, for the page of the entry it was for. */
  loginError: { target: string; kind: string } | null;
  form: ConnectionForm | null;
  now: number;
}

export const emptyConnectionsView = (): ConnectionsView => ({
  groups: "loading",
  catalog: "loading",
  audit: [],
  tab: "installed",
  search: "",
  open: null,
  toolsKind: null,
  auditOpen: false,
  confirmingDisconnect: null,
  busy: new Set(),
  login: null,
  device: null,
  loginError: null,
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

const typeChoices = (): Choice[] => [
  { value: "stdio", label: t("conn.type.stdio") },
  { value: "http", label: t("conn.type.http") },
];

/** The words in the language of the window. */
const words = (w: Words): string => w[getLocale()] ?? w.en;

const FAILURES: Record<string, MessageKey> = {
  not_found: "conn.err.not_found",
  timeout: "conn.err.timeout",
  auth: "conn.err.auth",
  network: "conn.err.network",
  protocol: "conn.err.protocol",
  missing_secret: "conn.err.missing_secret",
  gone: "conn.err.gone",
  denied: "conn.err.denied",
  error: "conn.err.error",
};

const failureText = (kind: string | null): string => tr(FAILURES[kind ?? ""] ?? FAILURES.error);

/** What to tell the user about a login that did not work out, in a line. */
function loginFailure(kind: string): string {
  if (kind === "denied") return t("conn.loginFail.denied");
  if (kind === "timeout") return t("conn.loginFail.timeout");
  return t("conn.loginFail.other", { p0: failureText(kind) });
}

// ---------- what the services are ----------

/** The store's entry a connected service was added from. */
function entryOf(g: ConnectionGroup, v: ConnectionsView): StoreService | undefined {
  return g.catalog_id && Array.isArray(v.catalog) ? v.catalog.find((s) => s.id === g.catalog_id) : undefined;
}

/** The login of a service ran out or was never finished: it asks to sign in again. */
export const needsLogin = (g: ConnectionGroup): boolean => g.auth !== "none" && g.status === "error" && g.error === "auth";

/** The first thing to say to 拉斐爾 with a service of the store, in the language of the window: what 「試試看」 puts in the composer. */
export function examplePrompt(s: StoreService | undefined): string | null {
  if (!s) return null;
  return (s.examples[getLocale()] ?? s.examples.en ?? [])[0] ?? null;
}

const isIcon = (id: string | null | undefined): id is ServiceIconId => !!id && (id in SERVICE_ICONS || hasLogo(id as ServiceIconId));

/** The picture of a service: its mark, bare, or a letter. */
function iconHTML(name: string, icon: ServiceIconId | null, size: "md" | "xl"): string {
  if (icon && hasLogo(icon)) return `<span class="mono ${size} logo-art">${serviceLogoHTML(icon)}</span>`;
  return icon
    ? `<span class="mono ${size} logo-mono"${brandStyle(icon)}>${serviceIconSVG(icon)}</span>`
    : `<span class="mono ${size}" aria-hidden="true">${monogram(name)}</span>`;
}

function groupIcon(g: ConnectionGroup, v: ConnectionsView, size: "md" | "xl"): string {
  const entry = entryOf(g, v);
  const known = isIcon(entry?.icon) ? entry.icon : serviceIconOf({ name: g.name, command: g.command, args: g.args, url: g.url });
  return iconHTML(g.name, known, size);
}

/** The picture of a service of the store (the first-run guide draws it too). */
export const entryIcon = (s: StoreService, size: "md" | "xl"): string => iconHTML(s.name, isIcon(s.icon) ? s.icon : null, size);

/** Where a service of the user's own is reached, in a few words. */
function where(g: ConnectionGroup): string {
  if (g.transport === "http") {
    try {
      return new URL(g.url).host;
    } catch {
      return g.url;
    }
  }
  return g.command;
}

/** The short line under a service's name: what it is for, or what is wrong with it. */
function lineOf(g: ConnectionGroup, v: ConnectionsView): string {
  if (needsLogin(g)) return st("err", t("conn.status.relogin"));
  if (g.status === "error") return st("err", t("conn.status.error", { p0: failureText(g.error) }));
  if (g.status === "unchecked") return esc(t("conn.status.unchecked"));
  const entry = entryOf(g, v);
  return esc(entry ? words(entry.summary) : where(g));
}

const matches = (query: string, ...texts: string[]): boolean => {
  const q = query.trim().toLowerCase();
  return !q || texts.some((text) => text.toLowerCase().includes(q));
};

// ---------- the bar: the two views and the search; the menu that adds, beside the tab's name ----------

/** 「新增 ▾」, the menu beside the tab's name that adds a server of the user's own (settings-view.ts puts it there). */
export const connectionsAddHTML = (): string =>
  `<button type="button" class="sel cs-add" aria-haspopup="menu" aria-expanded="false" data-k="conn-add">` +
  `<span>${esc(t("conn.addMenu"))}</span>${ico("down")}</button>`;

function barHTML(v: ConnectionsView): string {
  const installed = Array.isArray(v.groups) ? v.groups.length : 0;
  const store = Array.isArray(v.catalog) ? v.catalog.length : 0;
  const tab = (id: Tab, label: string, count: number) => ({ id, label, count, attrs: ` data-conn="tab" data-id="${id}" data-k="conn-tab:${id}"` });
  return (
    `<div class="cs-bar">` +
    tabs([tab("installed", t("conn.tab.installed"), installed), tab("store", t("conn.tab.store"), store)], v.tab, { label: t("conn.tabs"), kind: "nav", plain: true }) +
    `<div class="cs-find">${fieldHTML(t("conn.search"), v.search, { id: "conn-search", icon: "search", label: t("conn.search") })}</div></div>`
  );
}

// ---------- 已安裝: one list, a row for each service ----------

/**
 * A row of the list. The row itself opens the dialog (a click on its chevron, or anywhere but the
 * switch); its name is a real button for the keyboard; the switch is the whole service's.
 */
function itemHTML(g: ConnectionGroup, v: ConnectionsView): string {
  const busy = v.busy.has(g.id) || v.login?.target === g.id;
  const id = esc(g.id);
  return (
    `<div class="cs-item" data-conn="open" data-id="${id}">` +
    btn(
      `<span class="ic">${groupIcon(g, v, "md")}</span><span class="tx"><span class="nm">${esc(g.name)}</span><span class="ds">${lineOf(g, v)}</span></span>`,
      "cs-open",
      ` data-conn="open" data-id="${id}" data-k="cs-open:${id}" aria-haspopup="dialog" title="${esc(t("conn.open", { p0: g.name }))}"`,
    ) +
    tg(g.enabled, t("conn.switch", { p0: g.name }), ` data-conn="enable" data-id="${id}" data-k="cs-enable:${id}"${busy ? " disabled" : ""}`) +
    `<span class="chev" aria-hidden="true">${ico("right")}</span></div>`
  );
}

function installedHTML(v: ConnectionsView): string {
  if (v.groups === "loading") return `<p class="settings-loading" role="status">${esc(t("conn.loading"))}</p>`;
  if (v.groups === "failed") return `<div class="alert core-note">${st("err", t("conn.loadFailed"))}</div>`;
  const shown = v.groups.filter((g) => matches(v.search, g.name, entryOf(g, v) ? words(entryOf(g, v)!.summary) : where(g)));
  if (!v.groups.length) return `<p class="cs-none">${esc(t("conn.emptyInstalled"))}</p>`;
  if (!shown.length) return `<p class="cs-none">${esc(t("conn.noMatch"))}</p>`;
  return `<div class="cs-list" data-stagger="installed">${shown.map((g) => itemHTML(g, v)).join("")}</div>`;
}

// ---------- the dialog of a connected service ----------

/**
 * The name of a tool for the screen: the title its server gives it, else its name made readable (the
 * service's own name at the start taken off, the dashes and underscores as spaces, a capital first).
 */
export function toolLabel(tool: ConnectionTool, service = ""): string {
  const title = tool.title?.trim();
  if (title) return title;
  let name = tool.name;
  const prefix = service.trim().toLowerCase();
  if (prefix) {
    for (const sep of ["-", "_"]) if (name.toLowerCase().startsWith(prefix + sep) && name.length > prefix.length + 1) name = name.slice(prefix.length + 1);
  }
  const spaced = name.replace(/[-_]+/g, " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : tool.name;
}

function toolRow(g: ConnectionGroup, tool: ConnectionTool, busy: boolean): string {
  return row(
    `<span class="tool-name" title="${esc(tool.name)}">${esc(toolLabel(tool, g.name))}</span>`,
    esc(tool.description),
    tg(
      tool.enabled,
      t("conn.toolSwitch", { p0: toolLabel(tool, g.name) }),
      ` data-conn="tool" data-id="${esc(g.id)}" data-tool="${esc(tool.name)}" data-k="cs-tool:${esc(tool.name)}"${busy ? " disabled" : ""}`,
    ),
    { cls: "conn-tool" },
  );
}

const kindName = (kind: ToolKind): string => t(kind === "write" ? "conn.group.write" : "conn.group.read");

/** The dialog's close button, the same in both of its views. */
const closeButton = (): string => iconButton("x", t("common.close"), ' data-conn="close" data-k="cs-close"');

/** The latest actions of one service, folded under a row that says how many there are. */
function auditHTML(v: ConnectionsView, server: string): string {
  const entries = v.audit.filter((e) => e.server === server).slice(0, AUDIT_SHOWN);
  if (!entries.length) return "";
  const outcomes: Record<AuditEntry["outcome"], { kind: "ok" | "warn" | "err" | "idle"; key: MessageKey }> = {
    ok: { kind: "ok", key: "conn.audit.ok" },
    error: { kind: "err", key: "conn.audit.error" },
    timeout: { kind: "warn", key: "conn.audit.timeout" },
    denied: { kind: "idle", key: "conn.audit.denied" },
    expired: { kind: "idle", key: "conn.audit.expired" },
    blocked: { kind: "warn", key: "conn.audit.blocked" },
  };
  const approvers: Record<AuditEntry["approver"], MessageKey> = { auto: "conn.audit.auto", user: "conn.audit.user", none: "conn.audit.none" };
  const head = btn(
    `<span class="gt">${esc(t("conn.audit.title"))}<em class="num">${entries.length}</em></span>${ico(v.auditOpen ? "down" : "right")}`,
    "gr",
    ` data-conn="audit" aria-expanded="${v.auditOpen}" data-k="cs-audit"`,
  );
  if (!v.auditOpen) return `<div class="cs-grp cs-audit">${head}</div>`;
  const rows = entries.map((e) => {
    const when = `${dayLabel(e.at_ms, v.now)} ${formatClock(e.at_ms)}`;
    const outcome = outcomes[e.outcome] ?? outcomes.error;
    return row(esc(toolLabel({ name: e.tool, description: "", enabled: true, kind: "read" }, e.server)), esc(`${when} · ${tr(approvers[e.approver] ?? approvers.none)}`), st(outcome.kind, tr(outcome.key)), {
      cls: "conn-audit",
    });
  });
  return `<div class="cs-grp cs-audit">${head}<div class="audit">${rows.join("")}<p class="foot">${esc(t("conn.audit.foot"))}</p></div></div>`;
}

/** The dialog's first view: what the service is and holds, what to try, and the rarer actions. */
function summaryHTML(g: ConnectionGroup, v: ConnectionsView): string {
  const busy = v.busy.has(g.id);
  const id = ` data-id="${esc(g.id)}"`;
  const entry = entryOf(g, v);
  const loggingIn = v.login?.target === g.id;
  const head =
    `<div class="cs-dlg-head">${groupIcon(g, v, "xl")}<span class="sp"></span>` +
    tg(g.enabled, t("conn.switch", { p0: g.name }), ` data-conn="enable"${id} data-k="cs-dlg-enable"${busy ? " disabled" : ""}`) +
    closeButton() +
    `</div><h2 class="cs-dlg-name" id="cs-dlg-title">${esc(g.name)}</h2>`;
  const about = `<p class="cs-desc">${esc(entry ? words(entry.about) : t("conn.customServer"))}</p>`;
  const secrets = [...g.env, ...g.headers];
  // A server of the user's own says where it is and which secrets it was given, by name.
  const meta = entry
    ? ""
    : `<div class="cs-meta"><span>${ico("link")}<span class="code">${esc(g.transport === "http" ? g.url : g.command)}</span></span>` +
      (secrets.length ? `<span>${ico("lock")}<span class="code">${esc(secrets.join(", "))}</span></span>` : "") +
      `</div>`;
  const alert = needsLogin(g)
    ? `<div class="ca-alert">${ico("info")}<span class="msg">${esc(t("conn.status.relogin"))}</span>` +
      btn(esc(loggingIn ? t("conn.loggingIn") : t("conn.relogin")), "sm", ` data-conn="relogin"${id} data-k="cs-relogin-top"${busy || v.login ? " disabled" : ""}`) +
      `</div>`
    : g.status === "error"
      ? `<div class="ca-alert">${ico("info")}<span class="msg">${esc(t("conn.status.error", { p0: failureText(g.error) }))}</span></div>`
      : "";
  const kinds = (["write", "read"] as const).filter((kind) => g.tools.some((tool) => tool.kind === kind));
  const tools = g.tools.length
    ? `<p class="cs-count">${esc(t("conn.tools.summary", { p0: g.tool_count, p1: g.write_count, p2: g.read_count }))}</p>` +
      `<div class="cs-grp">${kinds
        .map((kind) =>
          btn(
            `<span class="gt">${esc(kindName(kind))}<em class="num">${g.tools.filter((tool) => tool.kind === kind).length}</em></span>${ico("right")}`,
            "gr",
            ` data-conn="tools" data-kind="${kind}" data-k="cs-kind:${kind}"`,
          ),
        )
        .join("")}</div>`
    : `<p class="cs-count conn-empty">${esc(t("conn.noTools"))}</p>`;
  const example = examplePrompt(entry);
  const tryIt = example ? `<div class="cs-try">${btn(esc(t("conn.tryIt")), "pri", ` data-conn="try"${id} data-k="cs-try"`)}</div>` : "";
  const acts =
    `<div class="cs-acts">` +
    (g.auth !== "none" && !needsLogin(g)
      ? btn(esc(loggingIn ? t("conn.loggingIn") : t("conn.relogin")), "sm", ` data-conn="relogin"${id} data-k="cs-relogin"${busy || v.login ? " disabled" : ""}`)
      : "") +
    btn(esc(busy ? t("conn.refreshing") : t("conn.refresh")), "sm", ` data-conn="refresh"${id} data-k="cs-refresh"${busy ? " disabled" : ""}`) +
    btn(
      esc(v.confirmingDisconnect === g.id ? t("conn.disconnectConfirm") : t("conn.disconnect")),
      v.confirmingDisconnect === g.id ? "sm danger" : "sm",
      ` data-conn="disconnect"${id} data-k="cs-disconnect"${busy ? " disabled" : ""}`,
    ) +
    (g.auth === "device" && g.catalog_id && GRANTS_URL[g.catalog_id] ? btn(esc(t("conn.revokeOpen", { p0: g.name })), "sm", ` data-conn="open-grants"${id}`) : "") +
    `</div>`;
  return head + about + meta + alert + (loggingIn ? deviceHTML(v) : "") + tools + tryIt + acts + auditHTML(v, g.name);
}

/** The dialog's second view: the tools of one kind, each with its switch, and one switch for them all. */
function toolsViewHTML(g: ConnectionGroup, kind: ToolKind, v: ConnectionsView): string {
  const tools = g.tools.filter((tool) => tool.kind === kind);
  const busy = v.busy.has(g.id);
  const on = tools.filter((tool) => tool.enabled).length;
  const state = on === tools.length ? "true" : on === 0 ? "false" : "mixed";
  const head =
    `<div class="cs-dlg-head sub">` +
    iconButton("left", t("conn.back"), ' data-conn="tools-back" data-k="cs-back"') +
    `<h2 class="cs-dlg-name" id="cs-dlg-title">${esc(kindName(kind))}<em class="num">${tools.length}</em></h2>` +
    // a checkbox role, not a switch: only that one has a "mixed" state
    `<button type="button" class="tg" role="checkbox" aria-checked="${state}" aria-label="${esc(t(kind === "write" ? "conn.groupSwitch.write" : "conn.groupSwitch.read"))}" data-conn="group" data-id="${esc(g.id)}" data-kind="${kind}" data-k="cs-group"${busy ? " disabled" : ""}></button>` +
    closeButton() +
    `</div>`;
  return head + `<div class="cs-tool-list" data-stagger="tools:${kind}">${tools.map((tool) => toolRow(g, tool, busy)).join("")}</div>`;
}

/**
 * The dialog of a connected service, over the settings with the page under it dimmed. It grows in
 * when it opens (`data-dialog`, ui.ts `swapHTML`), not each time it is drawn again; a click on the
 * dimmed part closes it, as Esc and its × do.
 */
function dialogHTML(g: ConnectionGroup, v: ConnectionsView): string {
  const inner = v.toolsKind && g.tools.some((tool) => tool.kind === v.toolsKind) ? toolsViewHTML(g, v.toolsKind, v) : summaryHTML(g, v);
  return (
    `<div class="cs-scrim" data-conn="scrim" data-dialog="${esc(g.id)}">` +
    `<div class="cs-dialog" id="cs-dialog" role="dialog" aria-modal="true" aria-labelledby="cs-dlg-title" tabindex="-1" data-trap>${inner}</div></div>`
  );
}

// ---------- 商店: the services that can be added ----------

/** The button that adds a store entry, or what takes its place: the wait for the login, or that it is added. */
function joinControl(s: StoreService, v: ConnectionsView): string {
  const here = v.login?.target === s.id;
  if (s.server_id) return `<span class="cs-state">${st("ok", t("conn.installed"))}</span>`;
  if (here) return btn(esc(t("conn.cancel")), "sm", ' data-conn="cancel-login"');
  return btn(`${ico("plus")}${esc(t("conn.join"))}`, "sm", ` data-conn="join" data-id="${esc(s.id)}" aria-label="${esc(t("conn.joinAria", { p0: s.name }))}"${v.login ? " disabled" : ""}`);
}

function cardHTML(s: StoreService, v: ConnectionsView): string {
  const here = v.login?.target === s.id;
  const line = here ? t(s.auth === "none" ? "conn.connecting" : "conn.loggingIn") : words(s.summary);
  return (
    `<div class="cs-card">` +
    btn(
      `<span class="ic">${entryIcon(s, "md")}</span><span class="tx"><span class="nm">${esc(s.name)}</span><span class="sub${here ? " wait" : ""}">${esc(line)}</span></span>`,
      "cs-main",
      ` data-conn="open-store" data-id="${esc(s.id)}" data-k="cs-store:${esc(s.id)}" title="${esc(`${s.name}: ${words(s.summary)}`)}"`,
    ) +
    `${joinControl(s, v)}</div>`
  );
}

function storeHTML(v: ConnectionsView): string {
  if (v.catalog === "loading") return `<p class="settings-loading" role="status">${esc(t("conn.loading"))}</p>`;
  if (v.catalog === "failed") return `<div class="alert core-note">${st("err", t("conn.loadFailed"))}</div>`;
  const found = v.catalog.filter((s) => matches(v.search, s.name, s.publisher, words(s.summary)));
  if (!found.length) return `<p class="cs-none">${esc(t("conn.noMatch"))}</p>`;
  const section = (key: string, title: string, list: StoreService[]): string =>
    list.length ? `<section class="cs-sec"><h2>${esc(title)}</h2><div class="cs-grid" data-stagger="store:${key}">${list.map((s) => cardHTML(s, v)).join("")}</div></section>` : "";
  // The popular ones first; 「全部」 is the rest, by name. A search lists what it finds under 「全部」 alone.
  const popular = v.search.trim() ? [] : found.filter((s) => s.popular);
  const rest = found.filter((s) => !popular.includes(s)).sort((a, b) => a.name.localeCompare(b.name));
  return section("hot", t("conn.hot"), popular) + section("all", t("conn.all"), rest);
}

const backHTML = (): string => `<div class="cs-back">${btn(`${ico("left")}${esc(t("conn.back"))}`, "", ' data-conn="back"')}</div>`;

function storePageHTML(s: StoreService, v: ConnectionsView): string {
  const here = v.login?.target === s.id;
  const action = s.server_id
    ? `<span class="cs-state">${st("ok", t("conn.installed"))}</span>` +
      btn(esc(t("conn.manage")), "", ` data-conn="manage" data-id="${esc(s.server_id)}"`)
    : here
      ? `<span class="cs-wait" role="status">${esc(t(s.auth === "none" ? "conn.connecting" : "conn.loggingIn"))}</span>` + btn(esc(t("conn.cancel")), "", ' data-conn="cancel-login"')
      : btn(`${ico("plus")}${esc(t("conn.join"))}`, "pri", ` data-conn="join" data-id="${esc(s.id)}"${v.login ? " disabled" : ""}`);
  const failed = v.loginError?.target === s.id ? `<div class="note" role="alert">${st("err", loginFailure(v.loginError.kind))}</div>` : "";
  const examples = (s.examples[getLocale()] ?? s.examples.en ?? []).map((text) => `<li>${ico("chat")}<span>${esc(text)}</span></li>`).join("");
  return (
    backHTML() +
    `<div class="cs-head">${entryIcon(s, "xl")}<div class="tx"><h2 class="big">${esc(s.name)}</h2><div class="by">${esc(t("conn.by", { p0: s.publisher }))}</div></div>` +
    `<div class="cs-join">${action}</div></div>` +
    failed +
    `<div class="cs-sec"><h3>${esc(t("conn.try"))}</h3><ul class="cs-say">${examples}</ul></div>` +
    `<div class="cs-sec"><h3>${esc(t("conn.about"))}</h3><p class="cs-desc">${esc(words(s.about))}</p></div>` +
    `<div class="cs-meta"><span>${ico("link")}<span class="cs-key">${esc(t("conn.server"))}</span><span class="code">${esc(s.url)}</span></span></div>`
  );
}

// ---------- the form that connects a server of the user's own ----------

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

// ---------- the code of a device login ----------

/** The code in keycaps, the one button that copies it and opens the service's page, and a cancel button. */
function deviceHTML(v: ConnectionsView): string {
  const d = v.device;
  if (!d || !v.login) return "";
  const name = (Array.isArray(v.catalog) ? v.catalog.find((s) => s.id === d.catalog_id)?.name : undefined) ?? v.login.name;
  const caps = [...d.user_code].map((ch) => (ch === "-" ? '<span class="cs-dash">-</span>' : `<span class="cs-cap">${esc(ch)}</span>`)).join("");
  return (
    `<div class="cs-device" role="group" aria-label="${esc(name)}"><p class="cs-device-hint">${esc(t("conn.device.hint"))}</p>` +
    `<div class="cs-code" aria-label="${esc(d.user_code)}">${caps}</div>` +
    `<div class="cs-device-acts">${btn(esc(t("conn.device.open", { p0: name })), "pri", ' data-conn="device-open"')}${btn(esc(t("conn.cancel")), "", ' data-conn="cancel-login"')}</div></div>`
  );
}

// ---------- the tab ----------

/** What is under the bar: the list, or the store. */
export function bodyHTML(v: ConnectionsView): string {
  return v.tab === "installed" ? installedHTML(v) : storeHTML(v);
}

/** The connected service whose dialog is open, if it is still there. */
const openGroup = (v: ConnectionsView): ConnectionGroup | undefined =>
  v.open?.tab === "installed" && Array.isArray(v.groups) ? v.groups.find((x) => x.id === v.open!.id) : undefined;

/** The tab, below its name and purpose: the form, a store page, or the bar and its view (with a service's dialog over it). */
export function connectionsHTML(v: ConnectionsView): string {
  if (v.form) return `<div class="cs cs-form">${formHTML(v.form)}</div>`;
  if (v.open?.tab === "store") {
    const s = Array.isArray(v.catalog) ? v.catalog.find((x) => x.id === v.open!.id) : undefined;
    if (s) return `<div class="cs cs-page">${deviceHTML(v)}${storePageHTML(s, v)}</div>`;
  }
  const g = openGroup(v);
  // A device login started from a service's dialog shows its code there instead.
  const device = g && v.login?.target === g.id ? "" : deviceHTML(v);
  return `<div class="cs">${device}${barHTML(v)}<div class="cs-body" data-cs-body>${bodyHTML(v)}</div></div>` + (g ? dialogHTML(g, v) : "");
}

// ---------- doing ----------

/** Focuses the element `selector` names, if the page is there (not in a test without one). */
const focusOn = (selector: string): void => {
  if (typeof document !== "undefined") document.querySelector<HTMLElement>(selector)?.focus();
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
  /** 「試試看」: closes the settings and puts `text` in the composer of the 拉斐爾 page. */
  tryPrompt(text: string): void;
}

export interface Connections {
  view(): ConnectionsView;
  /** Reads the services, the store and the latest actions. */
  load(): Promise<void>;
  /** A click on one of this tab's buttons or switches; false for anything else. */
  click(target: Element): boolean;
  /** Something typed in one of this tab's fields; false for any other input. */
  input(input: HTMLInputElement): boolean;
  /** Enter in one of the form's fields, or Esc with a service's dialog open; false for any other key. */
  key(e: KeyboardEvent): boolean;
  /** The menu of a dropdown of this tab, by its `data-k`. */
  menuFor(k: string): ConnectionMenu | null;
  /** An event of the main window: the code of a device login. */
  followEvent(event: MainEvent): void;
  /** The panel is closed or leaves the tab: drop what must not stay (an unsent form, a pending disconnect). */
  reset(): void;
}

export function createConnections(host: ConnectionsHost): Connections {
  let groups: ConnectionsView["groups"] = "loading";
  let catalog: ConnectionsView["catalog"] = "loading";
  let audit: AuditEntry[] = [];
  let tab: Tab = "installed";
  let search = "";
  let open: ConnectionsView["open"] = null;
  let toolsKind: ToolKind | null = null;
  let auditOpen = false;
  let confirming: string | null = null;
  let confirmTimer: ReturnType<typeof setTimeout> | undefined;
  const busy = new Set<string>();
  let login: Login | null = null;
  let device: DeviceCode | null = null;
  let loginError: ConnectionsView["loginError"] = null;
  let form: ConnectionForm | null = null;
  let loads = 0;
  let firstLoad = true;
  /** The dialog is on its way out (140 ms): a second close waits for the first. */
  let closing = false;

  const redraw = (): void => {
    if (host.visible()) host.redraw();
  };

  const view = (): ConnectionsView => ({
    groups, catalog, audit, tab, search, open, toolsKind, auditOpen, confirmingDisconnect: confirming, busy, login, device, loginError, form, now: Date.now(),
  });

  /** Draws only what is under the bar (the search box keeps its focus and its input method). */
  function repaintBody(): void {
    const body = typeof document !== "undefined" ? document.querySelector<HTMLElement>("[data-cs-body]") : null;
    if (body && host.visible()) body.innerHTML = bodyHTML(view());
    else redraw();
  }

  const groupById = (id: string): ConnectionGroup | undefined => (Array.isArray(groups) ? groups.find((g) => g.id === id) : undefined);
  const entryById = (id: string): StoreService | undefined => (Array.isArray(catalog) ? catalog.find((s) => s.id === id) : undefined);

  async function load(): Promise<void> {
    const mine = ++loads;
    const [list, store, recent] = await Promise.allSettled([api.toolsetsList(), api.integrationsCatalog(), api.integrationsAudit(AUDIT_READ)]);
    if (mine !== loads) return;
    if (list.status === "fulfilled") groups = list.value.groups;
    else if (!Array.isArray(groups)) groups = "failed";
    if (store.status === "fulfilled") catalog = store.value.services;
    else if (!Array.isArray(catalog)) catalog = "failed";
    if (recent.status === "fulfilled") audit = recent.value.entries;
    // what is open is gone (removed from elsewhere): back to the list
    if (open && !(open.tab === "installed" ? groupById(open.id) : entryById(open.id))) open = null;
    // Nothing connected yet: the store is where there is something to do.
    if (firstLoad && Array.isArray(groups)) {
      firstLoad = false;
      if (!groups.length) tab = "store";
    }
    redraw();
  }

  /** Does `work` for a service (its buttons wait meanwhile), says what went wrong, and reads everything again. */
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

  /** Opens the dialog of a connected service, at its summary, with the focus in it. */
  function openDialog(id: string): void {
    open = { tab: "installed", id };
    toolsKind = null;
    auditOpen = false;
    clearTimeout(confirmTimer);
    confirming = null;
    redraw();
    focusOn("#cs-dialog");
  }

  /**
   * Closes the dialog: it shrinks and fades away (140 ms), then the list is drawn without it and the
   * focus goes back to the row it was opened from.
   */
  function closeDialog(): void {
    if (!open || open.tab !== "installed" || closing) return;
    const id = open.id;
    closing = true;
    const scrim = typeof document !== "undefined" ? document.querySelector<HTMLElement>(".cs-scrim") : null;
    leave(scrim, DIALOG_OUT_MS, () => {
      closing = false;
      if (open?.tab !== "installed" || open.id !== id) return;
      open = null;
      toolsKind = null;
      auditOpen = false;
      clearTimeout(confirmTimer);
      confirming = null;
      redraw();
      focusOn(`[data-k="cs-open:${typeof CSS !== "undefined" ? CSS.escape(id) : id}"]`);
    });
  }

  function disconnect(id: string): void {
    clearTimeout(confirmTimer);
    if (confirming !== id) {
      confirming = id;
      confirmTimer = setTimeout(() => {
        confirming = null;
        redraw();
      }, DISCONNECT_CONFIRM_MS);
      redraw();
      return;
    }
    confirming = null;
    const group = groupById(id);
    void act(id, async () => {
      await api.integrationsDisconnect(id);
      if (open?.id === id) open = null;
      if (group) toaster.info(t(group.auth !== "none" ? "conn.disconnectedLogin" : "conn.disconnected", { p0: group.name }));
    });
  }

  /** Opens the browser login for a store entry (`catalog_id`) or for a service that must sign in again (`id`). */
  async function signIn(target: { catalog_id: string } | { id: string }, name: string, again: boolean): Promise<void> {
    if (login) return;
    const key = "catalog_id" in target ? target.catalog_id : target.id;
    login = { target: key, name };
    loginError = null;
    redraw();
    let result: Awaited<ReturnType<typeof api.integrationsConnect>>;
    try {
      result = await api.integrationsConnect(target);
    } catch (err) {
      toaster.info(describeError(err));
      result = { connected: false, error: "cancelled" };
    }
    login = null;
    device = null;
    if (result.connected) {
      toaster.info(t(again ? "conn.relogged" : "conn.connected", { p0: name }));
      if (!again) {
        tab = "installed";
        open = null;
      }
    } else if (result.error !== "cancelled") {
      loginError = { target: key, kind: result.error };
      toaster.info(loginFailure(result.error));
    }
    await load();
  }

  function followEvent(event: MainEvent): void {
    if (event.type !== "DeviceCode" || !login) return;
    device = { catalog_id: event.catalog_id, user_code: event.user_code, verification_uri: event.verification_uri };
    redraw();
  }

  /** Puts the code on the clipboard, so that it is pasted at the page that opens, and opens the page. */
  async function openDevicePage(): Promise<void> {
    const d = device;
    if (!d) return;
    try {
      await copyText(d.user_code);
      await api.openLink(d.verification_uri);
    } catch (err) {
      toaster.info(describeError(err));
    }
  }

  function addPair(f: ConnectionForm): boolean {
    const name = f.pairName.trim();
    if (!name || !f.pairValue) return false;
    f.pairs.push({ name, value: f.pairValue });
    f.pairName = "";
    f.pairValue = "";
    return true;
  }

  async function connectCustom(): Promise<void> {
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
      form = null; // the values were sent to the core's vault: nothing of them stays here
      tab = "installed";
      try {
        await api.integrationsRefresh(added.server.id);
      } catch {
        // the service says it could not read the tools
      }
      toaster.info(t("conn.connected", { p0: request.name }));
      await load();
      return;
    } catch (err) {
      f.busy = false;
      const text = describeError(err);
      f.error = () => text;
    }
    await load();
    redraw();
  }

  /** Shows another view; what was typed in the form is dropped with it. */
  function show(next: Tab): void {
    clearTimeout(confirmTimer);
    confirming = null;
    open = null;
    search = "";
    tab = next;
    redraw();
  }

  function click(target: Element): boolean {
    const button = target.closest<HTMLElement>("[data-conn]");
    if (!button) return false;
    const id = button.dataset.id ?? "";
    switch (button.dataset.conn) {
      case "tab":
        if (id === "installed" || id === "store") show(id);
        break;
      case "open":
        if (groupById(id)) openDialog(id);
        break;
      case "scrim":
        // the dimmed part around the dialog closes it; a click inside it (on no control) does nothing
        if (target === button) closeDialog();
        break;
      case "close":
        closeDialog();
        break;
      case "tools": {
        toolsKind = button.dataset.kind === "read" ? "read" : "write";
        redraw();
        focusOn("#cs-dialog");
        break;
      }
      case "tools-back": {
        const kind = toolsKind;
        toolsKind = null;
        redraw();
        focusOn(`[data-k="cs-kind:${kind}"]`);
        break;
      }
      case "audit":
        auditOpen = !auditOpen;
        redraw();
        break;
      case "try": {
        const group = groupById(id);
        const text = group ? examplePrompt(entryOf(group, view())) : null;
        if (text) host.tryPrompt(text);
        break;
      }
      case "open-store":
        open = { tab: "store", id };
        redraw();
        break;
      case "manage":
        tab = "installed";
        search = "";
        openDialog(id);
        break;
      case "back":
        clearTimeout(confirmTimer);
        confirming = null;
        open = null;
        redraw();
        break;
      case "enable": {
        const group = groupById(id);
        if (group) void act(id, async () => void (await api.integrationsSetEnabled(id, !group.enabled)));
        break;
      }
      case "tool": {
        const tool = groupById(id)?.tools.find((x) => x.name === button.dataset.tool);
        if (tool) void act(id, async () => void (await api.integrationsSetToolEnabled(id, tool.name, !tool.enabled)));
        break;
      }
      case "group": {
        const kind = button.dataset.kind === "read" ? "read" : "write";
        const tools = groupById(id)?.tools.filter((x) => x.kind === kind) ?? [];
        // all on turns all off; off or mixed turns all on
        if (tools.length) void act(id, async () => void (await api.integrationsSetToolsEnabled(id, tools.map((x) => x.name), !tools.every((x) => x.enabled))));
        break;
      }
      case "refresh":
        void act(id, async () => {
          await api.integrationsRefresh(id);
          toaster.info(t("conn.refreshed"));
        });
        break;
      case "disconnect":
        disconnect(id);
        break;
      case "join": {
        const entry = entryById(id);
        if (entry) void signIn({ catalog_id: entry.id }, entry.name, false);
        break;
      }
      case "relogin": {
        const group = groupById(id);
        if (group) void signIn({ id }, group.name, true);
        break;
      }
      case "device-open":
        void openDevicePage();
        break;
      case "open-grants": {
        const url = GRANTS_URL[groupById(id)?.catalog_id ?? ""];
        if (url) void api.openLink(url).catch((err) => toaster.info(describeError(err)));
        break;
      }
      case "cancel-login":
        void api.integrationsConnectCancel().catch((err) => toaster.info(describeError(err)));
        break;
      case "form-cancel":
        form = null; // an unsent value is dropped with the form
        redraw();
        break;
      case "pair-add":
        if (form) {
          addPair(form);
          redraw();
          focusOn("#conn-pair-name");
        }
        break;
      case "pair-drop":
        if (form) {
          form.pairs.splice(Number(button.dataset.i), 1);
          redraw();
        }
        break;
      case "connect":
        void connectCustom();
        break;
      default:
        return false;
    }
    return true;
  }

  function input(el: HTMLInputElement): boolean {
    if (el.id === "conn-search") {
      search = el.value;
      repaintBody();
      return true;
    }
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
    if (e.key === "Escape" && open?.tab === "installed" && !e.defaultPrevented) {
      e.preventDefault();
      closeDialog();
      return true;
    }
    const el = e.target as HTMLElement | null;
    if (e.key !== "Enter" || !form || !el?.id?.startsWith("conn-")) return false;
    e.preventDefault();
    if (el.id.startsWith("conn-pair")) {
      addPair(form);
      redraw();
      focusOn("#conn-pair-name");
    } else void connectCustom();
    return true;
  }

  function menuFor(k: string): ConnectionMenu | null {
    switch (k) {
      case "conn-add":
        return {
          choices: [{ value: "custom", label: t("conn.addCustom") }],
          current: "", // nothing is checked: it is an act, not a state
          pick: (value) => {
            if (value !== "custom" || form?.busy) return;
            open = null;
            form = emptyForm();
            redraw();
            focusOn("#conn-name");
          },
        };
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
      default:
        return null;
    }
  }

  function reset(): void {
    clearTimeout(confirmTimer);
    confirming = null;
    form = null;
    open = null;
    toolsKind = null;
    auditOpen = false;
    closing = false;
    search = "";
    tab = "installed";
    loginError = null;
    // the code stays: the login still waits for it in the core, which does not send it again
    loads++;
  }

  return { view, load, click, input, key, menuFor, followEvent, reset };
}
