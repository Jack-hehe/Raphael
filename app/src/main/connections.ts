// One connected view joins managed toolkits, private Google, custom MCP servers and the members' private skills:
// the apps that can be called now first (their number is the core's, the one the sidebar and every model's roster
// show), then the ones that cannot, and the skills. Details group tool switches by write/read and say how many are
// on; removal stays in the overflow menu. The store starts with built-in services and offers Composio as an optional catalog.
// Existing custom MCP setup and device reauthentication stay available.
// Secrets go directly to the core vault and never enter the render snapshot.

import * as api from "./api";
import { type InventoryView, appOf, countText, isConnected, whyNot } from "./apps-inventory";
import {
  type SkillItem, type SkillPage, loadSkillItems, sameSkill, skillChanged, skillDialogHTML, skillErrorText, skillPage, skillsSectionHTML,
} from "./connection-skills";
import { confirmDelete } from "./member-dialogs";
import { managedGroup, pluginLogoURL, pluginTool, storePlugin, type ManagedConnector } from "./plugins-data";
import { openMcpImport } from "./mcp-import";
import { isTopLayer, registerDismissal } from "./dismissals";
import { dayLabel, describeError, formatClock } from "./format";
import { getLocale, t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { SERVICE_ICONS, type ServiceIconId, brandStyle, hasLogo, serviceIconOf, serviceIconSVG, serviceLogoHTML } from "./service-icons";
import type { AuditEntry, ConnectionGroup, ConnectionTool, MainEvent, NewConnection, StoreService, Words } from "./types";
import { DISCONNECT_CONFIRM_MS, type Choice, actionRow, btn, closeMenu, copyText, esc, fieldHTML, ico, iconButton, leave, openPopover, row, sel, st, tabs, tg, toaster } from "./ui";

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

/** Split on the first equals sign only: tokens frequently contain padding or other equals signs. */
export function parseMcpPairs(text: string): PairDraft[] {
  return text.split(/\r?\n/).filter(line => line.trim()).map(line => {
    const at = line.indexOf("=");
    if (at < 1) throw new Error(t("mcp.badPair"));
    return { name: line.slice(0, at).trim(), value: line.slice(at + 1) };
  });
}

/** The form that connects a server of the user's own. */
export interface ConnectionForm {
  id?: string;
  pairLines?: string;
  limit?: number;
  testReport?: string;
  type: "stdio" | "http";
  name: string;
  command: string;
  args: string;
  url: string;
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
  configured: boolean | null;
  categories: Choice[];
  category: string;
  keyOpen: boolean;
  keyBusy: boolean;
  keyError: "" | "openrouter_key" | "consumer_key" | "auth" | "unreachable" | "unknown";
  /** The connected services: being read, read, or not to be had. */
  groups: ConnectionGroup[] | "loading" | "failed";
  /** Built-in services, loaded independently from the optional managed catalog. */
  catalog: StoreService[] | "loading" | "failed";
  composioCatalog: StoreService[] | "loading" | "failed";
  /** The owner's apps as the core counts them: the number beside 「已連結」 is its count, the one of the sidebar and the roster. */
  inventory: InventoryView;
  /** The private skills of the members, beside the apps. */
  skills: SkillItem[] | "loading" | "failed";
  /** The page of a skill that is open over the list. */
  skill: SkillPage | null;
  audit: AuditEntry[];
  tab: Tab;
  /** What was typed in the search box. */
  search: string;
  /** What is open: the dialog of a connected service over the list, or the page of a store entry in its place. */
  open: { tab: Tab; id: string } | null;
  /** The dialog's list of tools is unfolded. */
  toolsOpen: boolean;
  /** The dialog's latest actions are unfolded. */
  auditOpen: boolean;
  /** The service whose 「中斷連線」 was pressed once. */
  confirmingDisconnect: string | null;
  /** The services a call is under way for. */
  busy: ReadonlySet<string>;
  login: Login | null;
  /** The code to type, while a device login waits for it. */
  device: DeviceCode | null;
  /** What went wrong with the latest login, for its store page or connected-app dialog. */
  loginError: { target: string; kind: string } | null;
  /** Feedback stays inside the open dialog, clear of its actions. */
  dialogNotice: { text: string; kind: "ok" | "err" } | null;
  form: ConnectionForm | null;
  now: number;
}

export const emptyConnectionsView = (): ConnectionsView => ({
  configured: null, categories: [], category: "", keyOpen: false, keyBusy: false, keyError: "",
  groups: "loading",
  catalog: "loading",
  composioCatalog: "loading",
  inventory: "loading",
  skills: "loading",
  skill: null,
  audit: [],
  tab: "installed",
  search: "",
  open: null,
  toolsOpen: false,
  auditOpen: false,
  confirmingDisconnect: null,
  busy: new Set(),
  login: null,
  device: null,
  loginError: null,
  dialogNotice: null,
  form: null,
  now: Date.now(),
});

const emptyForm = (): ConnectionForm => ({
  type: "stdio",
  name: "",
  command: "",
  args: "",
  url: "",
  pairLines: "",
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
  toolkit_not_allowed: "conn.err.toolkit_not_allowed",
  error: "conn.err.error",
};

const failureText = (kind: string | null): string => tr(FAILURES[kind ?? ""] ?? FAILURES.error);

/** What to tell the user about a login that did not work out, in a line. */
function loginFailure(kind: string): string {
  if (kind === "toolkit_not_allowed") return failureText(kind);
  if (kind === "denied") return t("conn.loginFail.denied");
  if (kind === "timeout") return t("conn.loginFail.timeout");
  return t("conn.loginFail.other", { p0: failureText(kind) });
}

const loginErrorHTML = (v: ConnectionsView, target: string): string =>
  v.loginError?.target === target ? `<div class="note" role="alert">${st("err", loginFailure(v.loginError.kind))}</div>` : "";

const dialogNoticeHTML = (v: ConnectionsView): string => v.dialogNotice
  ? `<div class="note" role="${v.dialogNotice.kind === "err" ? "alert" : "status"}" data-cs-notice>${st(v.dialogNotice.kind, v.dialogNotice.text)}</div>` : "";

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

/** The picture of a service: its mark, bare, or a neutral plug. */
export function iconHTML(_name: string, icon: ServiceIconId | null, size: "md" | "xl"): string {
  if (icon && hasLogo(icon)) return `<span class="mono ${size} logo-art">${serviceLogoHTML(icon)}</span>`;
  return icon && icon in SERVICE_ICONS
    ? `<span class="mono ${size} logo-mono"${brandStyle(icon)}>${serviceIconSVG(icon)}</span>`
    : `<span class="mono ${size}" aria-hidden="true">${ico("plug")}</span>`;
}

function groupIcon(g: ConnectionGroup, v: ConnectionsView, size: "md" | "xl"): string {
  const logo = pluginLogoURL(g.logo);
  if (logo) return remoteIcon(logo, size);
  const entry = entryOf(g, v);
  const known = isIcon(entry?.icon) ? entry.icon : serviceIconOf({ name: g.name, command: g.command, args: g.args, url: g.url });
  return iconHTML(g.name, known, size);
}

/** The picture of a service of the store (the first-run guide draws it too). */
const remoteIcon = (logo: string, size: "md" | "xl"): string => `<span class="mono ${size} logo-art"><img class="logo" src="${esc(logo)}" alt="" referrerpolicy="no-referrer" loading="lazy"></span>`;
export const entryIcon = (s: StoreService, size: "md" | "xl"): string => {
  const logo = pluginLogoURL(s.logo);
  return logo ? remoteIcon(logo, size) : iconHTML(s.name, isIcon(s.icon) ? s.icon : serviceIconOf({ name: s.name }), size);
};

/** The command or URL of a custom service; the row ellipsizes it and its title keeps it whole. */
function where(g: ConnectionGroup): string {
  return g.transport === "http" ? g.url : [g.command, ...g.args].join(" ");
}

/** The short line under a service's name: what it is for, or what is wrong with it. */
function lineOf(g: ConnectionGroup, v: ConnectionsView): string {
  if (needsLogin(g)) return st("err", t("conn.status.relogin"));
  if (g.status === "error") return st("err", t("conn.status.error", { p0: failureText(g.error) }));
  if (g.status === "unchecked") return esc(t("conn.status.unchecked"));
  // On and well, and still not one of the connected apps: a second way to an app that is counted, or every tool off.
  const state = appOf(v.inventory, g.id)?.state;
  const why = state ? whyNot(state) : null;
  if (why) return st(why === "conn.state.unknown" ? "warn" : "idle", tr(why));
  const entry = entryOf(g, v);
  return esc(g.description || (entry ? words(entry.summary) : where(g)));
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

function barHTML(v: ConnectionsView, entries: StoreEntries): string {
  // 「已連結」 counts the apps whose tools can be called now, as the core counts them (the sidebar's number and the roster's): not the rows below it.
  const installed = countText(v.inventory);
  const store = entries.count;
  const tab = (id: Tab, label: string, count: number | string) => ({ id, label, count, attrs: ` data-conn="tab" data-id="${id}" data-k="conn-tab:${id}"` });
  return (
    `<div class="cs-bar">` +
    tabs([tab("installed", t("conn.tab.installed"), installed), tab("store", t("conn.tab.store"), store)], v.tab, { label: t("conn.tabs"), kind: "nav", plain: true }) +
    `<div class="cs-find">${fieldHTML(t("conn.search"), v.search, { id: "conn-search", icon: "search", label: t("conn.search") })}</div></div>`
  );
}

// ---------- 已安裝: one list, a row for each service ----------

/** The app switch reflects only the app's enabled flag; tool exclusions stay independent. */
function serviceSwitch(g: ConnectionGroup, attrs: string): string {
  return tg(g.enabled, t("conn.switch", { p0: g.name }), attrs);
}

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
    serviceSwitch(g, ` data-conn="enable" data-id="${id}" data-k="cs-enable:${id}"${busy ? " disabled" : ""}`) +
    `<span class="chev" aria-hidden="true">${ico("right")}</span></div>`
  );
}

/** The private skills the search leaves: none while they are read, and the failure to read them is told where they would be. */
function skillItems(v: ConnectionsView): SkillItem[] {
  return Array.isArray(v.skills) ? v.skills.filter(({ member, skill }) => matches(v.search, skill.name, skill.description, member.name)) : [];
}

function installedHTML(v: ConnectionsView): string {
  if (v.groups === "loading") return `<p class="settings-loading" role="status">${esc(t("conn.loading"))}</p>`;
  if (v.groups === "failed") return `<div class="alert core-note">${st("err", t("conn.loadFailed"))}${btn(esc(t("plugins.retry")), "sm", ' data-conn="retry-connections"')}</div>`;
  const shown = v.groups.filter((g) => matches(v.search, g.name, g.description ?? "", entryOf(g, v) ? words(entryOf(g, v)!.summary) : where(g)));
  const skills = skillItems(v);
  const skillsFailed = v.skills === "failed";
  if (!v.groups.length && !skillsFailed && !(Array.isArray(v.skills) && v.skills.length)) return `<p class="cs-none">${esc(t("conn.emptyInstalled"))}</p>`;
  if (!shown.length && !skills.length && !skillsFailed) return `<p class="cs-none">${esc(t("conn.noMatch"))}</p>`;
  // The apps that can be called now first, then the ones that cannot (waiting for a sign-in, failed, off, a second way to an app already
  // counted): each still has its switch and its dialog. While the inventory is not read, or cannot be, they are one list as before.
  const counted = shown.filter((g) => isConnected(v.inventory, g.id) !== false);
  const others = shown.filter((g) => isConnected(v.inventory, g.id) === false);
  const list = (groups: ConnectionGroup[], key: string): string => `<div class="cs-list" data-stagger="${key}">${groups.map((g) => itemHTML(g, v)).join("")}</div>`;
  const named = others.length > 0 || skills.length > 0 || skillsFailed;
  const section = (title: string, body: string): string => `<section class="cs-sec"><h2>${esc(title)}</h2>${body}</section>`;
  return (
    (counted.length ? (named ? section(t("conn.apps"), list(counted, "installed")) : list(counted, "installed")) : "") +
    (others.length ? section(t("conn.notConnected"), list(others, "installed-not")) : "") +
    (skills.length ? skillsSectionHTML(skills) : "") +
    (skillsFailed ? `<section class="cs-sec"><h2>${esc(t("conn.skills"))}</h2><div class="alert core-note">${st("err", t("conn.skills.failed"))}${btn(esc(t("plugins.retry")), "sm", ' data-conn="retry-skills"')}</div></section>` : "")
  );
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

/** What a managed account's status says, in words: Composio's own names for the states of a sign-in. */
const ACCOUNT_STATUS: Record<string, { kind: "ok" | "warn" | "err" | "idle"; key: MessageKey }> = {
  ACTIVE: { kind: "ok", key: "conn.acct.active" },
  INITIALIZING: { kind: "warn", key: "conn.acct.waiting" },
  INITIATED: { kind: "warn", key: "conn.acct.waiting" },
  EXPIRED: { kind: "err", key: "conn.acct.expired" },
  REVOKED: { kind: "err", key: "conn.acct.revoked" },
  FAILED: { kind: "err", key: "conn.acct.failed" },
  INACTIVE: { kind: "idle", key: "conn.acct.inactive" },
};
const accountStatus = (status: string): { kind: "ok" | "warn" | "err" | "idle"; text: string } => {
  const known = ACCOUNT_STATUS[status] ?? ACCOUNT_STATUS.INACTIVE!;
  return { kind: known.kind, text: tr(known.key) };
};

/**
 * The account(s) a service is signed in with, by status only (the core keeps no name or address of an account in what it lists):
 * a row for each account of a managed service (two when one was signed in again and the first has not gone), the owner's own Google as
 * one, and a signed-in server as one. A server that needs no sign-in has none.
 */
function accountsHTML(g: ConnectionGroup): string {
  const rows: { label: string; kind: "ok" | "warn" | "err" | "idle"; text: string }[] =
    g.source === "composio" ? (g.accounts ?? []).map((account, n) => ({ label: t("conn.accountN", { p0: n + 1 }), ...accountStatus(account.status) }))
    : g.source === "google" ? [{ label: t("conn.googleAccount"), ...accountStatus(g.accounts?.[0]?.status ?? (g.status === "ready" ? "ACTIVE" : "INACTIVE")) }]
    : g.auth !== "none" ? [{ label: t("conn.account"), ...(needsLogin(g) ? { kind: "err" as const, text: t("conn.status.relogin") } : { kind: "ok" as const, text: t("conn.signedIn") }) }]
    : [];
  if (!rows.length) return "";
  return `<div class="cs-accts">${rows.map((r) => `<div class="cs-acct">${ico("person")}<span class="nm">${esc(r.label)}</span>${st(r.kind, r.text)}</div>`).join("")}</div>`;
}

/** The dialog's first view: what the service is and holds, what to try, and the rarer actions. */
function summaryHTML(g: ConnectionGroup, v: ConnectionsView): string {
  const busy = v.busy.has(g.id);
  const id = ` data-id="${esc(g.id)}"`;
  const entry = entryOf(g, v);
  const loggingIn = v.login?.target === g.id;
  const head =
    `<div class="cs-dlg-head">${groupIcon(g, v, "xl")}<span class="sp"></span>` +
    serviceSwitch(g, ` data-conn="enable"${id} data-k="cs-dlg-enable"${busy ? " disabled" : ""}`) +
    iconButton("more", t("mcp.actions"), ` data-conn="server-menu"${id} aria-haspopup="menu"${busy ? " disabled" : ""}`) +
    closeButton() +
    `</div><h2 class="cs-dlg-name" id="cs-dlg-title">${esc(g.name)}</h2>`;
  const about = `<p class="cs-desc">${esc(g.description || (entry ? words(entry.about) : t("conn.customServer")))}</p>`;
  const secrets = [...g.env, ...g.headers];
  // A server of the user's own says where it is and which secrets it was given, by name.
  const meta = entry || g.source
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
  // Kind switches change tool permissions; the app switch never rewrites them.
  const on = g.tools.filter((tool) => tool.enabled).length;
  const tools = g.tools_state === "loading" ? `<p role="status">${esc(t("conn.loading"))}</p>`
    : g.tools_state === "failed" ? `<div class="note" role="alert">${esc(t("conn.loadFailed"))}${btn(esc(t("plugins.retry")), "sm", ` data-conn="refresh"${id}`)}</div>`
    : g.tools.length
    ? `<div class="cs-grp cs-tools"><div class="cs-kind-list">${(["write", "read"] as const).map(kind => {
        const list = g.tools.filter(tool => tool.kind === kind), enabled = list.filter(tool => tool.enabled).length;
        return row(`${esc(kindName(kind))}<em class="num">${enabled}/${list.length}</em>`, "",
          tg(list.length > 0 && enabled === list.length, t("conn.groupSwitch", { p0: kindName(kind) }),
            ` data-conn="kind"${id} data-kind="${kind}" data-k="cs-kind:${kind}"${busy || loggingIn || !list.length ? " disabled" : ""}`), { cls: "cs-kind" });
      }).join("")}</div>` +
      btn(`<span class="gt">${esc(t("conn.tools.enabled", { p0: on, p1: g.tools.length, p2: g.write_count, p3: g.read_count }))}</span>${ico(v.toolsOpen ? "down" : "right")}`,
        "gr", ` data-conn="tool-list" aria-expanded="${v.toolsOpen}" data-k="cs-count"`) +
      (v.toolsOpen ? `<div class="cs-plugin-tools">${kinds.map(kind => `<section><h3>${esc(kindName(kind))}</h3><div class="cs-tool-list">${g.tools.filter(tool => tool.kind === kind).map(tool => toolRow(g, tool, busy)).join("")}</div></section>`).join("")}</div>` : "") +
      `</div>`
    : `<p class="cs-count conn-empty">${esc(t("conn.noTools"))}</p>`;
  const example = examplePrompt(entry);
  const acts = actionRow({
    cls: "cs-acts", sticky: true,
    primary: example ? { label: esc(t("conn.tryIt")), attrs: ` data-conn="try"${id} data-k="cs-try"` } : undefined,
    secondary: (g.auth !== "none" && !needsLogin(g)
      ? btn(esc(loggingIn ? t("conn.loggingIn") : t("conn.relogin")), "sm", ` data-conn="relogin"${id} data-k="cs-relogin"${busy || v.login ? " disabled" : ""}`)
      : "") +
    btn(esc(busy ? t("conn.refreshing") : t("conn.refresh")), "sm", ` data-conn="refresh"${id} data-k="cs-refresh"${busy ? " disabled" : ""}`) +
    (g.auth === "device" && g.catalog_id && GRANTS_URL[g.catalog_id] ? btn(esc(t("conn.revokeOpen", { p0: g.name })), "sm", ` data-conn="open-grants"${id}`) : ""),
  });
  return head + about + meta + accountsHTML(g) + alert + loginErrorHTML(v, g.id) + dialogNoticeHTML(v) + (loggingIn ? deviceHTML(v) : "") + tools + auditHTML(v, g.name) + acts;
}

/**
 * The dialog of a connected service, over the settings with the page under it dimmed. It grows in
 * when it opens (`data-dialog`, ui.ts `swapHTML`), not each time it is drawn again; a click on the
 * dimmed part closes it, as Esc and its × do.
 */
function dialogHTML(g: ConnectionGroup, v: ConnectionsView): string {
  const inner = summaryHTML(g, v);
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
  // Managed sign-in has no cancellation RPC; keep the shared action disabled until it completes.
  if (here) return btn(esc(t("conn.cancel")), "sm", ` data-conn="cancel-login"${s.source ? " disabled" : ""}`);
  return btn(`${ico("plus")}${esc(t("conn.join"))}`, "sm", ` data-conn="join" data-id="${esc(s.id)}" aria-label="${esc(t("conn.joinAria", { p0: s.name }))}"${s.can_connect === false ? ` title="${esc(t("plugins.unavailable"))}"` : ""}${v.login || s.can_connect === false ? " disabled" : ""}`);
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

function keyErrorMessage(kind: ConnectionsView["keyError"]): string {
  switch (kind) {
    case "openrouter_key": return t("plugins.keyOpenRouter");
    case "consumer_key": return t("plugins.keyConsumer");
    case "auth": return t("plugins.keyRefused");
    case "unreachable": return t("plugins.keyUnreachable");
    default: return t("plugins.keyFailed");
  }
}

/** Built-in apps win over matching managed slugs or names, regardless of the current filter. */
function uniqueComposio(v: ConnectionsView): StoreService[] {
  if (!v.configured || !Array.isArray(v.composioCatalog)) return [];
  const identity = (value: string): string => value.toLowerCase().replace(/[^a-z0-9]/g, "");
  const owned = new Set((Array.isArray(v.catalog) ? v.catalog : []).flatMap(s => [identity(s.id), identity(s.name)]));
  const seen = new Set<string>();
  return v.composioCatalog.filter(s => {
    const id = identity(s.id), name = identity(s.name);
    if (owned.has(id) || owned.has(name) || seen.has(id)) return false;
    seen.add(id); return true;
  });
}

interface StoreEntries {
  builtin: StoreService[];
  managed: StoreService[];
  count: number;
}

/** One filtered snapshot supplies both the rows and their tab count. */
function storeEntries(v: ConnectionsView): StoreEntries {
  const found = (s: StoreService): boolean => matches(v.search, s.name, s.publisher, words(s.summary));
  const builtin = (Array.isArray(v.catalog) ? v.catalog : []).filter(found);
  const managed = uniqueComposio(v).filter(s => (!v.category || s.category === v.category) && found(s));
  return { builtin, managed, count: builtin.length + managed.length };
}

const composioMark = (): string => `<span class="cs-icon md cs-composio-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h6v10H4zM14 7h6v10h-6zM10 10h4m-4 4h4"/></svg></span>`;

function keyDialogHTML(v: ConnectionsView): string {
  return `<div class="cs-scrim" data-conn="scrim" data-dialog="composio-key"><div class="cs-dialog" id="cs-dialog" role="dialog" aria-modal="true" aria-labelledby="cs-dlg-title" tabindex="-1" data-trap>` +
    `<div class="cs-dlg-head">${composioMark()}<h2 class="cs-dlg-name" id="cs-dlg-title">Composio</h2><span class="sp"></span>${closeButton()}</div>` +
    `<div class="cs-key-form"><label for="conn-key">${esc(t("plugins.keyPrompt"))}</label>` +
    `<input class="field" type="password" id="conn-key" autocomplete="new-password" spellcheck="false"${v.keyBusy ? " disabled" : ""}${v.keyError ? ' aria-invalid="true" aria-describedby="conn-key-error"' : ""}>` +
    (v.keyError ? `<p id="conn-key-error" role="alert">${esc(keyErrorMessage(v.keyError))}</p>` : "") + dialogNoticeHTML(v) +
    actionRow({ primary: { label: esc(t("plugins.saveKey")), attrs: ` data-conn="save-key"${v.keyBusy ? " disabled" : ""}` } }) + `</div></div></div>`;
}

function storeHTML(v: ConnectionsView, entries: StoreEntries = storeEntries(v)): string {
  const section = (key: string, title: string, list: StoreService[]): string =>
    list.length ? `<section class="cs-sec"><h2>${esc(title)}</h2><div class="cs-grid" data-stagger="store:${key}">${list.map(s => cardHTML(s, v)).join("")}</div></section>` : "";
  const retry = (action: string): string => `<div class="alert core-note" role="alert">${st("err", t("conn.loadFailed"))}${btn(esc(t("plugins.retry")), "sm", ` data-conn="${action}"`)}</div>`;
  const loading = `<p class="settings-loading" role="status">${esc(t("conn.loading"))}</p>`;
  const found = entries.builtin;
  const popular = v.search.trim() ? [] : found.filter(s => s.popular);
  const rest = found.filter(s => !popular.includes(s)).sort((a, b) => a.name.localeCompare(b.name));
  let html = v.catalog === "loading" ? loading : v.catalog === "failed" ? retry("retry-builtin-store")
    : section("hot", t("conn.hot"), popular) + section("all", t("conn.all"), rest);
  if (v.configured) {
    const managed = entries.managed;
    // Provider order is preserved; each app belongs to its first category only.
    const grouped = new Map<string, StoreService[]>(v.categories.map(c => [c.value, []]));
    for (const item of managed) {
      if (!grouped.has(item.category)) grouped.set(item.category, []);
      grouped.get(item.category)!.push(item);
    }
    const groups = [...grouped].filter(([, list]) => list.length).map(([key, list]) =>
      `<section class="cs-composio-category" data-category="${esc(key)}"><h3>${esc(v.categories.find(c => c.value === key)?.label || key || t("plugins.uncategorized"))}</h3><div class="cs-grid">${list.map(s => cardHTML(s, v)).join("")}</div></section>`).join("");
    html += `<section class="cs-sec cs-composio-section"><div class="cs-store-heading"><h2>Composio</h2>${sel([{ value: "", label: t("plugins.allCategories") }, ...v.categories], v.category, t("plugins.category"), ' data-k="conn-category"')}</div>` +
      (v.composioCatalog === "loading" ? loading : v.composioCatalog === "failed" ? retry("retry-store")
        : managed.length ? groups : `<p class="cs-none">${esc(t("conn.noMatch"))}</p>`) + `</section>`;
  } else if (v.configured === false) {
    html += `<div class="cs-composio-setup">${composioMark()}<span>${esc(t("plugins.moreApps"))}</span>${btn(esc(t("plugins.configure")), "sm", ' data-conn="configure-composio"')}</div>`;
  }
  if (Array.isArray(v.catalog) && !entries.count && !v.configured) html = `<p class="cs-none">${esc(t("conn.noMatch"))}</p>` + html;
  return html;
}

const backHTML = (): string => `<div class="cs-back">${btn(`${ico("left")}${esc(t("conn.back"))}`, "", ' data-conn="back"')}</div>`;

function storePageHTML(s: StoreService, v: ConnectionsView): string {
  const here = v.login?.target === s.id;
  const action = s.server_id
    ? `<span class="cs-state">${st("ok", t("conn.installed"))}</span>` +
      btn(esc(t("conn.manage")), "", ` data-conn="manage" data-id="${esc(s.server_id)}"`)
    : here
      ? `<span class="cs-wait" role="status">${esc(t(s.auth === "none" ? "conn.connecting" : "conn.loggingIn"))}</span>` + btn(esc(t("conn.cancel")), "", ` data-conn="cancel-login"${s.source ? " disabled" : ""}`)
      : btn(`${ico("plus")}${esc(t("conn.join"))}`, "pri", ` data-conn="join" data-id="${esc(s.id)}"${v.login || s.can_connect === false ? " disabled" : ""}`);
  const failed = loginErrorHTML(v, s.id);
  const examples = (s.examples[getLocale()] ?? s.examples.en ?? []).map((text) => `<li>${ico("chat")}<span>${esc(text)}</span></li>`).join("");
  return (
    backHTML() +
    `<div class="cs-head">${entryIcon(s, "xl")}<div class="tx"><h2 class="big">${esc(s.name)}</h2><div class="by">${esc(t("conn.by", { p0: s.publisher }))}</div></div>` +
    `<div class="cs-join">${action}</div></div>` +
    failed +
    (examples ? `<div class="cs-sec"><h3>${esc(t("conn.try"))}</h3><ul class="cs-say">${examples}</ul></div>` : "") +
    `<div class="cs-sec"><h3>${esc(t("conn.about"))}</h3><p class="cs-desc">${esc(words(s.about))}</p></div>` +
    (s.url ? `<div class="cs-meta"><span>${ico("link")}<span class="cs-key">${esc(t("conn.server"))}</span><span class="code">${esc(s.url)}</span></span></div>` : "")
  );
}

// ---------- the form that connects a server of the user's own ----------

const fld = (label: string, control: string, hint = "", forId = ""): string =>
  `<div class="fld">${forId ? `<label class="flabel" for="${forId}">${esc(label)}</label>` : `<span class="flabel">${esc(label)}</span>`}${hint ? `<span class="fhint">${esc(hint)}</span>` : ""}${control}</div>`;

function pairsHTML(f: ConnectionForm): string {
  const http = f.type === "http";
  const label = http ? t("conn.form.headers") : t("conn.form.env");
  const adder = `<textarea class="field mcp-lines" id="conn-pairs" rows="4" ${f.busy ? "disabled" : ""} spellcheck="false" aria-label="${esc(label)}" placeholder="${http ? 'Authorization=Bearer ...' : 'API_KEY=...'}">${esc(f.pairLines ?? "")}</textarea>`;
  return fld(label, adder, t(f.id ? "mcp.keepSecrets" : "conn.form.secretNote"));
}

function formHTML(f: ConnectionForm): string {
  const off = f.busy;
  const fields =
    f.type === "stdio"
      ? `<div class="two">` +
        fld(t("conn.form.command"), fieldHTML(t("conn.form.commandHint"), f.command, { id: "conn-command", label: t("conn.form.command"), disabled: off }), "", "conn-command") +
        fld(t("mcp.args"), `<textarea class="field mcp-lines" id="conn-args" rows="4" ${off ? "disabled" : ""}>${esc(f.args)}</textarea>`, "", "conn-args") +
        `</div>`
      : fld(t("conn.form.url"), fieldHTML(t("conn.form.urlHint"), f.url, { id: "conn-url", label: t("conn.form.url"), disabled: off }), "", "conn-url");
  const error = f.error ? `<div class="note" role="alert">${st("err", f.error())}</div>` : "";
  const typeSel = sel(typeChoices(), f.type, t("conn.form.type"), ` data-k="conn-type"${off ? " disabled" : ""}`);
  return (
    `<h2 class="big">${esc(t(f.id ? "mcp.edit" : "mcp.add"))}</h2>` +
    `<div class="add-grid">` +
    `<div class="two">${fld(t("conn.form.type"), typeSel)}${fld(t("conn.form.name"), fieldHTML(t("conn.form.nameHint"), f.name, { id: "conn-name", label: t("conn.form.name"), disabled: off }), "", "conn-name")}</div>` +
    fields +
    pairsHTML(f) +
    fld(t("mcp.limit"), `<input class="field" type="number" id="conn-limit" min="100" max="100000" value="${f.limit ?? 8000}" ${off ? "disabled" : ""}>`, "", "conn-limit") +
    (f.testReport ? `<p role="status">${esc(f.testReport)}</p>` : "") +
    `</div><div class="trust">${ico("shield")}<span><b>${esc(t("conn.form.trustTitle"))}</b> ${esc(t("conn.form.trustBody"))}</span></div>` +
    error +
    actionRow({
      cls: "add-acts", sticky: true,
      secondary: btn(t("mcp.test"), "", ` data-conn="test-draft"${off ? " disabled" : ""}`) + btn(esc(t("conn.form.cancel")), "", ` data-conn="form-cancel"${off ? " disabled" : ""}`),
      primary: { label: esc(off ? t("conn.form.connecting") : t("mcp.save")), attrs: ` data-conn="connect"${off ? " disabled" : ""}` },
    })
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
    actionRow({ cls: "cs-device-acts", secondary: btn(esc(t("conn.cancel")), "", ' data-conn="cancel-login"'), primary: { label: esc(t("conn.device.open", { p0: name })), attrs: ' data-conn="device-open"' } }) + `</div>`
  );
}

// ---------- the tab ----------

/** What is under the bar: the list, or the store. */
export function bodyHTML(v: ConnectionsView, entries?: StoreEntries): string {
  return v.tab === "installed" ? installedHTML(v) : storeHTML(v, entries);
}

/** The connected service whose dialog is open, if it is still there. */
const openGroup = (v: ConnectionsView): ConnectionGroup | undefined =>
  v.open?.tab === "installed" && Array.isArray(v.groups) ? v.groups.find((x) => x.id === v.open!.id) : undefined;

/** The tab, below its name and purpose: the form, a store page, or the bar and its view (with a service's dialog over it). */
export function connectionsHTML(v: ConnectionsView): string {
  if (v.form) return `<div class="cs cs-form">${formHTML(v.form)}</div>`;
  if (v.open?.tab === "store") {
    const s = [...(Array.isArray(v.catalog) ? v.catalog : []), ...uniqueComposio(v)].find(x => x.id === v.open!.id);
    if (s) return `<div class="cs cs-page">${deviceHTML(v)}${storePageHTML(s, v)}</div>`;
  }
  const g = openGroup(v);
  // A device login started from a service's dialog shows its code there instead.
  const device = g && v.login?.target === g.id ? "" : deviceHTML(v);
  const skill = openSkill(v);
  const entries = storeEntries(v);
  return `<div class="cs">${device}${barHTML(v, entries)}<div class="cs-body" data-cs-body>${bodyHTML(v, entries)}</div></div>` + (v.keyOpen ? keyDialogHTML(v) : g ? dialogHTML(g, v) : skill ? skillScrimHTML(skill.item, skill.page, dialogNoticeHTML(v)) : "");
}

/** The skill whose page is open, if it is still there. */
const openSkill = (v: ConnectionsView): { item: SkillItem; page: SkillPage } | undefined => {
  const page = v.skill;
  const item = page && Array.isArray(v.skills) ? v.skills.find((each) => sameSkill(each, page.memberId, page.id)) : undefined;
  return page && item ? { item, page } : undefined;
};

/** The page of a skill over the tab, in the dialog an app has (wider: it holds a document). */
const skillScrimHTML = (item: SkillItem, page: SkillPage, notice: string): string =>
  `<div class="cs-scrim" data-conn="scrim" data-dialog="skill:${item.member.id}:${item.skill.id}">` +
  `<div class="cs-dialog wide" id="cs-dialog" role="dialog" aria-modal="true" aria-labelledby="cs-dlg-title" tabindex="-1" data-trap>` +
  skillDialogHTML(item, page, closeButton(), btn(esc(t("plugins.retry")), "sm", ' data-conn="skill-retry"'), notice) + `</div></div>`;

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
  root?: HTMLElement;
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
  let configured: boolean | null = null;
  let categories: Choice[] = [];
  let category = "";
  let keyOpen = false;
  let keySaves = 0;
  let keyBusy = false;
  let keyError: ConnectionsView["keyError"] = "";
  let managed: ManagedConnector[] = [];
  let catalogLoads = 0;
  let groups: ConnectionsView["groups"] = "loading";
  let catalog: ConnectionsView["catalog"] = "loading";
  let composioCatalog: ConnectionsView["composioCatalog"] = "loading";
  let inventory: InventoryView = "loading";
  let skills: ConnectionsView["skills"] = "loading";
  let skill: SkillPage | null = null;
  let skillLoads = 0;
  let audit: AuditEntry[] = [];
  let tab: Tab = "installed";
  let search = "";
  let open: ConnectionsView["open"] = null;
  let toolsOpen = false;
  let auditOpen = false;
  let confirming: string | null = null;
  let confirmTimer: ReturnType<typeof setTimeout> | undefined;
  const busy = new Set<string>();
  let login: Login | null = null;
  let device: DeviceCode | null = null;
  let loginError: ConnectionsView["loginError"] = null;
  let dialogNotice: ConnectionsView["dialogNotice"] = null;
  let form: ConnectionForm | null = null;
  let loads = 0;
  let firstLoad = true;
  /** The dialog is on its way out (140 ms): a second close waits for the first. */
  let closing = false;
  let stopOutside: (() => void) | null = null;

  /** A dialog is over the list: an app's, or a skill's. */
  const dialogOpen = (): boolean => keyOpen || open?.tab === "installed" || skill !== null;

  const redraw = (): void => {
    // The page is drawn again whole: the document that is being edited keeps its caret, its selection and its scroll.
    const area = typeof document !== "undefined" ? document.querySelector<HTMLTextAreaElement>("#skill-text") : null;
    const kept = area && document.activeElement === area ? { start: area.selectionStart, end: area.selectionEnd, top: area.scrollTop } : null;
    if (host.visible()) host.redraw();
    if (kept) {
      const next = document.querySelector<HTMLTextAreaElement>("#skill-text");
      if (next) { next.focus(); next.setSelectionRange(kept.start, kept.end); next.scrollTop = kept.top; }
    }
    if (dialogOpen() && !stopOutside && typeof document !== "undefined") {
      const root = host.root ?? document.querySelector("#cs-dialog")?.parentElement?.parentElement;
      if (root) stopOutside = registerDismissal(() => root.querySelector<HTMLElement>("#cs-dialog"), closeDialog);
    } else if (!dialogOpen()) { stopOutside?.(); stopOutside = null; }
  };

  /** Connection actions report inside the active dialog instead of covering its footer. */
  const notify = (text: string, kind: "ok" | "err" = "ok"): void => {
    if (dialogOpen()) { dialogNotice = { text, kind }; redraw(); }
    else toaster.info(text);
  };

  const view = (): ConnectionsView => ({
    configured, categories, category, keyOpen, keyBusy, keyError,
    groups, catalog, composioCatalog, inventory, skills, skill, audit, tab, search, open, toolsOpen, auditOpen, confirmingDisconnect: confirming, busy, login, device, loginError, dialogNotice, form, now: Date.now(),
  });

  /** Draws only what is under the bar (the search box keeps its focus and its input method). */
  function repaintBody(): void {
    const body = typeof document !== "undefined" ? document.querySelector<HTMLElement>("[data-cs-body]") : null;
    const current = view();
    const entries = storeEntries(current);
    if (body && host.visible()) body.innerHTML = bodyHTML(current, entries);
    else redraw();
    const count = typeof document !== "undefined" ? (host.root ?? document).querySelector('[data-k="conn-tab:store"] .n') : null;
    if (count) count.textContent = String(entries.count);
  }

  const groupById = (id: string): ConnectionGroup | undefined => (Array.isArray(groups) ? groups.find((g) => g.id === id) : undefined);
  const entryById = (id: string): StoreService | undefined => ([...(Array.isArray(catalog) ? catalog : []), ...uniqueComposio(view())].find(s => s.id === id));

  async function load(withSkills = true): Promise<void> {
    const mine = ++loads;
    const [list, status, recent, counted, builtins] = await Promise.allSettled([api.toolsetsList(), api.connectorsStatus(), api.integrationsAudit(AUDIT_READ), api.integrationsInventory(), api.integrationsCatalog()]);
    if (mine !== loads) return;
    // The count is the core's, read with the list it counts: both say the same moment.
    inventory = counted.status === "fulfilled" ? counted.value : "failed";
    if (status.status === "fulfilled") {
      configured = status.value.composio_configured;
      managed = status.value.connectors;
    }
    const previous = Array.isArray(groups) ? groups : [];
    if (list.status === "fulfilled" && status.status === "fulfilled") groups = [
      ...managed.map(c => managedGroup(c, previous.find(g => g.id === c.id))),
      ...list.value.groups.filter(g => !["connectors_composio", "connectors_google"].includes(g.id)),
    ];
    else if (!Array.isArray(groups)) groups = "failed";
    catalog = builtins.status === "fulfilled" ? builtins.value.services : "failed";
    if (Array.isArray(composioCatalog)) composioCatalog = composioCatalog.map(s => s.source ? { ...s,
      server_id: managed.find(c => c.id === `composio:${s.id}` && c.status === "ACTIVE")?.id ?? null } : s);
    if (recent.status === "fulfilled") audit = recent.value.entries;
    // what is open is gone (removed from elsewhere): back to the list
    if (open && !(open.tab === "installed" ? groupById(open.id) : entryById(open.id))) open = null;
    // Nothing connected yet: the store is where there is something to do.
    if (firstLoad && Array.isArray(groups)) {
      firstLoad = false;
      if (!groups.length) tab = "store";
    }
    redraw();
    if (withSkills) void loadSkills();
    if (configured && status.status === "fulfilled" && !Array.isArray(composioCatalog)) await loadCatalog();
  }

  /** The private skills of the members: read again whenever the list is, and when a skill is changed anywhere (`MembersChanged`). */
  async function loadSkills(): Promise<void> {
    const mine = ++skillLoads;
    try {
      const items = await loadSkillItems();
      if (mine !== skillLoads) return;
      skills = items;
    } catch {
      if (mine !== skillLoads) return;
      if (!Array.isArray(skills)) skills = "failed";
    }
    // the page that is open belongs to a skill that is gone (deleted elsewhere): back to the list
    if (skill && Array.isArray(skills) && !skills.some((item) => sameSkill(item, skill!.memberId, skill!.id))) skill = null;
    redraw();
  }

  async function loadCatalog(): Promise<void> {
    if (!configured) return;
    const mine = ++catalogLoads;
    const before = Array.isArray(composioCatalog) ? composioCatalog : null;
    // A refresh keeps the existing rows; drawing them again before its reply forces extra layout.
    if (!before) { composioCatalog = "loading"; repaintBody(); }
    try {
      const result = await api.connectorsCatalog();
      if (mine !== catalogLoads) return;
      composioCatalog = result.items.map(s => storePlugin(s, managed));
      categories = result.categories.map(c => ({ value: c.id, label: c.name }));
    } catch (error) {
      if (mine !== catalogLoads) return;
      if (error instanceof Error && error.message === "missing_key") { configured = false; composioCatalog = []; }
      else composioCatalog = before ?? "failed";
    }
    redraw();
  }

  async function loadTools(id: string): Promise<void> {
    const group = groupById(id);
    if (!group?.source || group.tools_state === "loading") return;
    group.tools_state = "loading"; redraw();
    try {
      const result = await api.connectorsTools(id);
      const current = groupById(id);
      if (!current) return;
      current.tools = result.items.map(pluginTool);
      current.tools_state = "ready";
      current.tool_count = current.tools.length;
      current.write_count = current.tools.filter(t => t.kind === "write").length;
      current.read_count = current.tools.length - current.write_count;
    } catch { const current = groupById(id); if (current) current.tools_state = "failed"; }
    redraw();
  }

  async function saveKey(): Promise<void> {
    const input = (host.root ?? document).querySelector<HTMLInputElement>("#conn-key");
    if (!input?.value.trim() || keyBusy) return;
    const mine = ++keySaves;
    let key = input.value.trim();
    input.value = "";
    keyBusy = true; keyError = ""; redraw();
    try {
      await api.connectorsSetKey(key);
      if (mine !== keySaves) return;
      configured = true; keyOpen = false;
      await loadCatalog();
    } catch (error) {
      if (mine !== keySaves) return;
      const kind = error instanceof Error ? error.message : "";
      keyError = kind === "openrouter_key" || kind === "consumer_key" || kind === "auth" ? kind
        : kind === "network" || kind === "timeout" ? "unreachable" : "unknown";
    }
    finally { key = ""; if (mine === keySaves) { keyBusy = false; redraw(); } }
  }

  /** Does `work` for a service (its buttons wait meanwhile), says what went wrong, and reads everything again. */
  async function act(id: string, work: () => Promise<void>): Promise<void> {
    if (busy.has(id)) return;
    busy.add(id);
    redraw();
    try {
      await work();
    } catch (err) {
      notify(describeError(err), "err");
    }
    busy.delete(id);
    await load(false);
    if (open?.id === id) await loadTools(id);
  }

  /** Opens the dialog of a connected service, at its summary, with the focus in it. */
  function openDialog(id: string): void {
    open = { tab: "installed", id };
    loginError = null;
    dialogNotice = null;
    toolsOpen = false;
    auditOpen = false;
    clearTimeout(confirmTimer);
    confirming = null;
    redraw();
    focusOn("#cs-dialog");
    void loadTools(id);
  }

  /**
   * Closes the dialog: it shrinks and fades away (140 ms), then the list is drawn without it and the
   * focus goes back to the row it was opened from.
   */
  function closeDialog(): void {
    if (closing) return;
    loginError = null;
    dialogNotice = null;
    if (keyOpen) {
      keyOpen = false; keyError = ""; keySaves++; keyBusy = false;
      redraw(); focusOn('[data-conn="configure-composio"]'); return;
    }
    if (skill && open?.tab !== "installed") {
      // The page of a skill goes the same way: the document that was being edited is dropped with it.
      const page = skill;
      closing = true;
      leave(typeof document !== "undefined" ? document.querySelector<HTMLElement>(".cs-scrim") : null, DIALOG_OUT_MS, () => {
        closing = false;
        if (skill !== page) return;
        skill = null;
        redraw();
        focusOn(`[data-k="cs-skill:${page.memberId}:${page.id}"]`);
      });
      return;
    }
    if (!open || open.tab !== "installed") return;
    const id = open.id;
    closing = true;
    const scrim = typeof document !== "undefined" ? document.querySelector<HTMLElement>(".cs-scrim") : null;
    leave(scrim, DIALOG_OUT_MS, () => {
      closing = false;
      if (open?.tab !== "installed" || open.id !== id) return;
      open = null;
      toolsOpen = false;
      auditOpen = false;
      clearTimeout(confirmTimer);
      confirming = null;
      redraw();
      focusOn(`[data-k="cs-open:${typeof CSS !== "undefined" ? CSS.escape(id) : id}"]`);
    });
  }

  // ---------- the private skills ----------

  const skillOf = (memberId: number, id: number): SkillItem | undefined =>
    Array.isArray(skills) ? skills.find((each) => sameSkill(each, memberId, id)) : undefined;

  /** Opens the page of a skill, with the focus in it; its document is read for it. */
  function openSkillPage(memberId: number, id: number): void {
    dialogNotice = null;
    skill = skillPage(memberId, id);
    redraw();
    focusOn("#cs-dialog");
    void readSkill();
  }

  async function readSkill(): Promise<void> {
    const page = skill;
    if (!page) return;
    page.state = "loading";
    page.error = null;
    redraw();
    try {
      const { text } = await api.membersSkillsRead(page.memberId, page.id);
      if (skill !== page) return;
      page.saved = page.draft = text;
      page.state = "ready";
    } catch {
      if (skill !== page) return;
      page.state = "failed";
    }
    redraw();
  }

  /** Keeps the edited document. The name and the line under it follow it, so the skills are read again. */
  async function saveSkill(): Promise<void> {
    const page = skill;
    if (!page || page.busy || !skillChanged(page)) return;
    page.busy = true;
    page.error = null;
    dialogNotice = null;
    redraw();
    try {
      await api.membersSkillsEdit(page.memberId, page.id, page.draft);
      if (skill === page) page.saved = page.draft = page.draft.replace(/\r\n/g, "\n");
      notify(t("conn.skill.saved"));
    } catch (err) {
      if (skill === page) page.error = skillErrorText(err);
    }
    if (skill === page) page.busy = false;
    redraw();
    await loadSkills();
  }

  /** The owner asks to delete a skill: after the shared question, it and its trigger are gone. */
  async function deleteSkill(memberId: number, id: number): Promise<void> {
    const item = skillOf(memberId, id);
    if (!item || !(await confirmDelete(item.skill.name))) return;
    try {
      await api.membersSkillsDelete(memberId, id);
      skill = null;
      dialogNotice = null;
      notify(t("conn.skill.deleted", { p0: item.skill.name }));
    } catch (err) {
      notify(describeError(err), "err");
      return;
    }
    await loadSkills();
  }

  /** The switch of a skill: on, the member is told when to use it; off, it is not. */
  async function toggleSkill(memberId: number, id: number): Promise<void> {
    const item = skillOf(memberId, id);
    if (!item) return;
    try {
      await api.membersSkillsUpdate(memberId, id, !item.skill.enabled);
    } catch (err) {
      notify(describeError(err), "err");
    }
    await loadSkills();
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
      if (group?.source) await api.connectorsDisconnect(id);
      else await api.integrationsDisconnect(id);
      if (open?.id === id) open = null;
      if (group) notify(t(group.auth !== "none" ? "conn.disconnectedLogin" : "conn.disconnected", { p0: group.name }));
    });
  }

  /** Opens the browser login for a store entry (`catalog_id`) or for a service that must sign in again (`id`). */
  async function signIn(target: { catalog_id: string } | { id: string }, name: string, again: boolean): Promise<void> {
    if (login) return;
    const key = "catalog_id" in target ? target.catalog_id : target.id;
    const fromDialog = open?.tab === "installed" && open.id === key ? open : null;
    dialogNotice = null;
    login = { target: key, name };
    loginError = null;
    redraw();
    let result: { connected: boolean; error?: string };
    try {
      result = ("catalog_id" in target ? !entryById(target.catalog_id)?.source : !groupById(target.id)?.source) ? await api.integrationsConnect(target)
        : "id" in target && groupById(target.id)?.source === "google"
        ? await api.connectorsGoogleConnect()
        : await api.connectorsConnect(key.replace(/^composio:/, ""));
    } catch (error) {
      result = { connected: false, error: error instanceof Error && error.message === "toolkit_not_allowed" ? "toolkit_not_allowed" : "error" };
    }
    login = null;
    device = null;
    if (result.connected) {
      loginError = null;
      if (!fromDialog || open === fromDialog || !dialogOpen()) notify(t(again ? "conn.relogged" : "conn.connected", { p0: name }));
      if (!again) {
        tab = "installed";
        open = null;
      }
    } else if (result.error !== "cancelled") {
      // Closing or replacing a dialog discards its pending failure as well as its visible one.
      if (!fromDialog || (open === fromDialog && !closing)) loginError = { target: key, kind: result.error ?? "error" };
      if (!fromDialog) notify(result.error === "toolkit_not_allowed" ? failureText(result.error) : t("plugins.connectFailed"), "err");
    }
    await load(false);
  }

  function followEvent(event: MainEvent): void {
    if (event.type === "ConnectionsChanged" && host.visible()) { void load(false); return; }
    // a skill was imported, switched, edited or deleted, in the member's own tab or by Raphael: the list follows it
    if (event.type === "MembersChanged" && host.visible()) { void loadSkills(); return; }
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
      notify(describeError(err), "err");
    }
  }

  async function connectCustom(): Promise<void> {
    const f = form;
    if (!f || f.busy) return;
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
    const pairs: { name: string; value: string }[] = [];
    const request: NewConnection =
      f.type === "stdio"
        ? { name: f.name.trim(), transport: "stdio", command: f.command.trim(), args: f.args.split(/\r?\n/).map(line => line.trim()).filter(Boolean), env: pairs }
        : { name: f.name.trim(), transport: "http", url: f.url.trim(), headers: pairs };
    f.busy = true;
    f.error = null;
    redraw();
    try {
      request.result_size_limit = f.limit ?? 8000;
      request.env = f.type === "stdio" ? parseMcpPairs(f.pairLines ?? "") : undefined;
      request.headers = f.type === "http" ? parseMcpPairs(f.pairLines ?? "") : undefined;
      if (f.id) await api.integrationsUpdate(f.id, request);
      else await api.integrationsAdd(request);
      form = null; // the values were sent to the core's vault: nothing of them stays here
      tab = "installed";
      notify(t("conn.connected", { p0: request.name }));
      await load(false);
      return;
    } catch (err) {
      f.busy = false;
      const text = describeError(err);
      f.error = () => text;
    }
    await load(false);
    redraw();
  }

  async function testDraft(): Promise<void> {
    const f = form;
    if (!f || f.busy) return;
    f.busy = true; f.error = null; redraw();
    try {
      const values = parseMcpPairs(f.pairLines ?? "");
      const result = await api.integrationsTest({ ...(f.id ? { id: f.id } : {}), name: f.name.trim(), transport: f.type, command: f.command.trim(), args: f.args.split(/\r?\n/).filter(Boolean), url: f.url.trim(),
        env: f.type === "stdio" ? values : undefined, headers: f.type === "http" ? values : undefined, result_size_limit: f.limit ?? 8000 });
      f.testReport = result.ok ? `${t("mcp.testOk")} (${result.tools.length}): ${result.tools.join(", ")}` : `${t("mcp.testFailed")}: ${result.error ?? ""}`;
    } catch (error) { const message = describeError(error); f.error = () => message; }
    f.busy = false; redraw();
  }

  /** Shows another view; what was typed in the form is dropped with it. */
  function show(next: Tab): void {
    clearTimeout(confirmTimer);
    confirming = null;
    open = null;
    search = "";
    tab = next;
    dialogNotice = null;
    loginError = null;
    redraw();
    if (tab === "store" && configured) void loadCatalog();
  }

  function click(target: Element): boolean {
    const button = target.closest<HTMLElement>("[data-conn]");
    if (!button) return false;
    const id = button.dataset.id ?? "";
    switch (button.dataset.conn) {
      case "server-menu": {
        const group = groupById(id);
        if (!group) return true;
        const panel = openPopover(button, [
          ...(!group.source ? [["test-server", t("mcp.test")], ["edit-server", t("mcp.edit")]] : []),
          ["disconnect", t(confirming === id ? group.source ? "conn.disconnectConfirm" : "conn.deleteConfirm" : group.source ? "conn.disconnect" : "mcp.delete")],
        ].map(([act, label]) => `<button type="button" role="menuitem"${act === "disconnect" ? ' class="danger"' : ""} data-conn="${act}" data-id="${esc(id)}">${esc(label)}</button>`).join(""), { className: "menu", role: "menu", label: t("mcp.actions") });
        panel.addEventListener("click", event => {
          const action = (event.target as Element).closest("[data-conn]");
          if (action) {
            const confirm = (action as HTMLElement).dataset.conn === "disconnect" && confirming !== id;
            closeMenu(); click(action);
            if (confirm) {
              const anchor = Array.from((host.root ?? document).querySelectorAll<HTMLElement>('[data-conn="server-menu"]')).find(anchor => anchor.dataset.id === id);
              if (anchor) { click(anchor); document.querySelector<HTMLButtonElement>('.menu [data-conn="disconnect"]')?.focus(); }
            }
          }
        });
        panel.querySelector<HTMLButtonElement>("button")?.focus();
        break;
      }
      case "test-server":
        void api.integrationsTest({ id }).then(result => notify(result.ok ? t("mcp.testOk") + ` (${result.tools.length})` : t("mcp.testFailed") + `: ${result.error ?? ""}`, result.ok ? "ok" : "err"), error => notify(describeError(error), "err"));
        break;
      case "edit-server": {
        const group = groupById(id);
        if (!group || group.catalog_id) return true;
        closeDialog();
        form = { ...emptyForm(), id, type: group.transport, name: group.name, command: group.command, args: group.args.join("\n"), url: group.url, limit: group.result_size_limit ?? 8000,
          pairLines: (group.transport === "stdio" ? group.env : group.headers).map(name => `${name}=`).join("\n") };
        redraw(); focusOn("#conn-name");
        break;
      }
      case "test-draft": void testDraft(); break;
      case "tab":
        if (id === "installed" || id === "store") show(id);
        break;
      case "open":
        if (groupById(id)) openDialog(id);
        break;
      case "scrim":
        // Outside gestures are handled by the shared dismissal layer.
        break;
      case "close":
        closeDialog();
        break;
      case "tool-list":
        toolsOpen = !toolsOpen;
        redraw();
        focusOn('[data-k="cs-count"]');
        break;
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
        if (group) void act(id, async () => {
          if (group.source) await api.connectorsSetTools(id, { enabled: !group.enabled });
          else await api.integrationsSetEnabled(id, !group.enabled);
        });
        break;
      }
      case "kind": {
        const group = groupById(id), kind = button.dataset.kind;
        const tools = group?.tools.filter(tool => tool.kind === kind) ?? [];
        if (group && (kind === "write" || kind === "read") && tools.length && group.tools_state !== "loading" && group.tools_state !== "failed") void act(id, async () => {
          const on = !tools.every(tool => tool.enabled);
          if (group.source) {
            const disabled = new Set(group.disabled_tools ?? []);
            for (const tool of tools) { if (on) disabled.delete(tool.name); else disabled.add(tool.name); }
            await api.connectorsSetTools(id, { disabled_tools: [...disabled] });
          } else await api.integrationsSetToolsEnabled(id, tools.map(tool => tool.name), on);
        });
        break;
      }
      case "tool": {
        const group = groupById(id);
        const tool = group?.tools.find((x) => x.name === button.dataset.tool);
        if (tool && group) void act(id, async () => {
          if (group.source) {
            const disabled = new Set(group.disabled_tools ?? []);
            if (tool.enabled) disabled.add(tool.name); else disabled.delete(tool.name);
            await api.connectorsSetTools(id, { disabled_tools: [...disabled] });
          } else await api.integrationsSetToolEnabled(id, tool.name, !tool.enabled);
        });
        break;
      }
      case "refresh":
        if (groupById(id)?.source) { void loadTools(id); break; }
        void act(id, async () => {
          await api.integrationsRefresh(id);
          notify(t("conn.refreshed"));
        });
        break;
      case "disconnect":
        disconnect(id);
        break;
      case "join": {
        const entry = entryById(id);
        if (entry && entry.can_connect !== false && !entry.server_id) void signIn({ catalog_id: entry.id }, entry.name, false);
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
        if (url) void api.openLink(url).catch((err) => notify(describeError(err), "err"));
        break;
      }
      case "cancel-login":
        if (login && (entryById(login.target)?.source || groupById(login.target)?.source)) break;
        void api.integrationsConnectCancel().catch((err) => notify(describeError(err), "err"));
        break;
      case "form-cancel":
        form = null; // an unsent value is dropped with the form
        redraw();
        break;
      case "connect":
        void connectCustom();
        break;
      case "configure-composio": keyOpen = true; keyError = ""; redraw(); focusOn("#conn-key"); break;
      case "save-key": void saveKey(); break;
      case "skill-open": openSkillPage(Number(button.dataset.member), Number(id)); break;
      case "skill-enable": void toggleSkill(Number(button.dataset.member), Number(id)); break;
      case "skill-save": void saveSkill(); break;
      case "skill-delete": void deleteSkill(Number(button.dataset.member), Number(id)); break;
      case "skill-retry": void readSkill(); break;
      case "retry-skills": void loadSkills(); break;
      case "retry-connections": void load(); break;
      case "retry-builtin-store": void load(false); break;
      case "retry-store": void (configured ? loadCatalog() : load()); break;
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
    if (el.id === "conn-key") return true;
    if (el.id === "skill-text") {
      // The document is edited in place: only the button that keeps it changes, so that the caret stays where it is.
      if (skill?.state === "ready") {
        skill.draft = el.value;
        skill.error = null;
        const save = typeof document !== "undefined" ? document.querySelector<HTMLButtonElement>('[data-conn="skill-save"]') : null;
        if (save) save.disabled = !skillChanged(skill) || skill.busy;
      }
      return true;
    }
    if (!form || !el.id.startsWith("conn-")) return false;
    switch (el.id) {
      case "conn-pairs": form.pairLines = el.value; break;
      case "conn-limit": form.limit = Number(el.value); break;
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
      default:
        return false;
    }
    return true;
  }

  function key(e: KeyboardEvent): boolean {
    if (e.key === "Enter" && (e.target as HTMLElement)?.id === "conn-key") { e.preventDefault(); void saveKey(); return true; }
    const panel = typeof document !== "undefined" ? (host.root ?? document).querySelector<HTMLElement>("#cs-dialog") : null;
    if (e.key === "Escape" && panel && !isTopLayer(panel)) return false;
    if (e.key === "Escape" && dialogOpen() && !e.defaultPrevented) {
      e.preventDefault();
      closeDialog();
      return true;
    }
    const el = e.target as HTMLElement | null;
    if (e.key !== "Enter" || !form || !el?.id?.startsWith("conn-") || el instanceof HTMLTextAreaElement) return false;
    e.preventDefault();
    void connectCustom();
    return true;
  }

  function menuFor(k: string): ConnectionMenu | null {
    switch (k) {
      case "conn-category": return {
        choices: [{ value: "", label: t("plugins.allCategories") }, ...categories], current: category,
        pick: value => { category = value; repaintBody(); },
      };
      case "conn-add":
        return {
          choices: [{ value: "custom", label: t("mcp.add") }, { value: "paste", label: t("mcp.paste") }, { value: "import", label: t("mcp.fromApps") }],
          current: "", // nothing is checked: it is an act, not a state
          pick: (value) => {
            if (value === "paste" || value === "import") { openMcpImport(value === "import", () => void load(false)); return; }
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
                  form.pairLines = ""; // Variables and headers are different things.
                  form.testReport = undefined;
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
    catalogLoads++;
    category = "";
    keyError = ""; keyOpen = false; keySaves++; keyBusy = false;
    stopOutside?.(); stopOutside = null;
    clearTimeout(confirmTimer);
    confirming = null;
    form = null;
    open = null;
    skill = null; // a document that was being edited is dropped with the page
    toolsOpen = false;
    auditOpen = false;
    closing = false;
    search = "";
    tab = "installed";
    loginError = null;
    dialogNotice = null;
    // the code stays: the login still waits for it in the core, which does not send it again
    loads++;
  }

  return { view, load, click, input, key, menuFor, followEvent, reset };
}
