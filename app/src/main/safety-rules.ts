// The owner's safety rules, in the 拉斐爾 tab of Settings: which actions Raphael asks about first, does
// at once, or never does (the core's `agent/safety.py`). On one card: a line that adds a rule (what
// Raphael wants to do, in the owner's own words, with a dropdown of 先問我 / 直接做 / 不准做 and a
// bordered 「新增」), then the rules under the two columns 「當拉斐爾想要」 and 「他會」, each with a
// borderless pencil, which opens the rule in its own row to change it there, and a trash can, which
// deletes it after the 10 s of 「復原」 that every delete has. There is no switch: the rules are always
// read.
//
// `rulesHTML` draws it from a snapshot (pure, so its rules can be tested without a browser);
// `createRules` holds the state and does what the buttons say, through the core's `safety.rules.*`
// methods. A rule's words are the owner's: they are shown, never logged.

import { core } from "./api";
import { describeError } from "./format";
import { getLocale, t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { type Choice, actionRow, btn, esc, iconButton, note, sel, st } from "./ui";
import type { Undoable } from "./undo";

export type Verdict = "ask" | "allow" | "deny";

/** A rule as `safety.rules.list` gives it. */
export interface SafetyRule {
  id: number;
  action: string;
  verdict: Verdict;
  position: number;
}

/** The most a rule holds (the core's limit). */
export const RULE_MAX = 80;

const VERDICT_KEYS: Record<Verdict, MessageKey> = { ask: "rules.ask", allow: "rules.allow", deny: "rules.deny" };
const tr = t as unknown as (key: MessageKey) => string;

/** What the dropdown of a rule offers, in this order. */
export const verdictChoices = (): Choice[] => (["ask", "allow", "deny"] as const).map((v) => ({ value: v, label: tr(VERDICT_KEYS[v]) }));
export const verdictLabel = (verdict: string): string => (verdict in VERDICT_KEYS ? tr(VERDICT_KEYS[verdict as Verdict]) : verdict);
const isVerdict = (value: string): value is Verdict => value === "ask" || value === "allow" || value === "deny";

/** Why a rule cannot be kept, as the line under it says. */
export type RuleError = "empty" | "too_long" | "duplicate" | "too_many" | { message: string };

const ERROR_KEYS: Record<Exclude<RuleError, { message: string }>, MessageKey> = {
  empty: "rules.err.empty",
  too_long: "rules.err.too_long",
  duplicate: "rules.err.duplicate",
  too_many: "rules.err.too_many",
};
const errorText = (error: RuleError): string => (typeof error === "object" ? error.message : tr(ERROR_KEYS[error]));

/** A rule's words as the core keeps them: one line, without the spaces around it. */
export const cleanAction = (text: string): string => text.split(/\s+/).filter(Boolean).join(" ");

/** What is wrong with `action` as a rule among `rules` (leaving out the one being changed), or null. */
export function ruleProblem(action: string, rules: readonly SafetyRule[], except?: number): RuleError | null {
  const clean = cleanAction(action);
  if (!clean) return "empty";
  if (clean.length > RULE_MAX) return "too_long";
  const key = clean.toLocaleLowerCase();
  if (rules.some((r) => r.id !== except && r.action.toLocaleLowerCase() === key)) return "duplicate";
  return null;
}

/** The kind of a refusal of the core that the line can say in the window's language. */
function refusal(err: unknown): RuleError {
  const text = String(err instanceof Error ? err.message : err);
  if (text.includes("too many rules")) return "too_many";
  if (text.includes("there already")) return "duplicate";
  if (text.includes("too long")) return "too_long";
  if (text.includes("action is empty")) return "empty";
  return { message: describeError(err) };
}

export interface RuleDraft {
  action: string;
  verdict: Verdict;
  error: RuleError | null;
  busy: boolean;
}

export interface RuleEditor extends RuleDraft {
  id: number;
}

export interface RulesView {
  status: SafetyRule[] | "loading" | "failed";
  draft: RuleDraft;
  editing: RuleEditor | null;
  /** Deleted, and waiting out their 10 s of 「復原」: not drawn. */
  hidden: readonly number[];
}

export const emptyRulesView = (): RulesView => ({
  status: "loading",
  draft: { action: "", verdict: "ask", error: null, busy: false },
  editing: null,
  hidden: [],
});

function editorRow(e: RuleEditor, rule: SafetyRule): string {
  const off = e.busy ? " disabled" : "";
  return (
    `<div class="rules-row is-editing" data-rule-row="${rule.id}">` +
    `<label class="field rules-field"${e.error ? " data-invalid" : ""}><input id="rule-edit" type="text" maxlength="${RULE_MAX}" value="${esc(e.action)}" ` +
    `aria-label="${esc(t("rules.editLabel", { p0: rule.action }))}" autocomplete="off" spellcheck="false"${e.error ? ' aria-invalid="true"' : ""}${off}></label>` +
    sel(verdictChoices(), e.verdict, t("rules.verdict"), ` data-k="rule-edit-verdict"${off}`) +
    actionRow({ cls: "rules-acts", secondary: btn(esc(t("common.cancel")), "sm", ` data-rule="cancel"${off}`), primary: { label: esc(t("rules.save")), kind: "sm", attrs: ` data-rule="save"${off}` } }) +
    `</div>` +
    (e.error ? note(st("err", errorText(e.error)), ' role="alert"') : "")
  );
}

function ruleRow(v: RulesView, rule: SafetyRule): string {
  if (v.editing?.id === rule.id) return editorRow(v.editing, rule);
  const off = v.editing?.busy || v.draft.busy ? " disabled" : "";
  return (
    `<div class="rules-row" data-rule-row="${rule.id}">` +
    `<span class="rules-what">${esc(rule.action)}</span>` +
    `<span class="rules-how">${esc(verdictLabel(rule.verdict))}</span>` +
    `<span class="rules-tools">` +
    iconButton("pencil", t("rules.edit", { p0: rule.action }), ` data-rule="edit" data-id="${rule.id}"${off}`) +
    iconButton("trash", t("rules.delete", { p0: rule.action }), ` data-rule="delete" data-id="${rule.id}"${off}`) +
    `</span></div>`
  );
}

/** The section: its name, then the line that adds a rule and the list of the rules, on one card. */
export function rulesHTML(v: RulesView): string {
  const open = (body: string): string =>
    `<section class="sgroup rules" data-rules-section aria-label="${esc(t("rules.title"))}"><h2>${esc(t("rules.title"))}</h2>` +
    `<div class="slist rules-card">${body}</div></section>`;
  if (v.status === "loading") return open(`<div class="rules-wait"><span class="spin" role="status" aria-label="${esc(t("common.loading"))}"></span></div>`);
  if (v.status === "failed") return open(note(st("err", t("rules.loadFailed")), ' role="alert"'));
  const d = v.draft;
  const off = d.busy ? " disabled" : "";
  const add =
    `<div class="rules-add">` +
    `<label class="field rules-field"${d.error ? " data-invalid" : ""}><input id="rule-new" type="text" maxlength="${RULE_MAX}" value="${esc(d.action)}" ` +
    `placeholder="${esc(t("rules.placeholder"))}" aria-label="${esc(t("rules.placeholder"))}" autocomplete="off" spellcheck="false"${d.error ? ' aria-invalid="true"' : ""}${off}></label>` +
    sel(verdictChoices(), d.verdict, t("rules.verdict"), ` data-k="rule-new-verdict"${off}`) +
    actionRow({ primary: { label: esc(t("rules.add")), attrs: ` data-rule="add"${off}` } }) +
    `</div>` +
    (d.error ? note(st("err", errorText(d.error)), ' role="alert"') : "");
  const rules = v.status.filter((r) => !v.hidden.includes(r.id));
  const list = rules.length
    ? `<div class="rules-head" aria-hidden="true"><span>${esc(t("rules.action"))}</span><span>${esc(t("rules.verdict"))}</span><span></span></div>` +
      rules.map((r) => ruleRow(v, r)).join("")
    : `<div class="rules-none">${esc(t("rules.none"))}</div>`;
  return open(add + `<div class="rules-list">${list}</div>`);
}

// ---------- state ----------

export interface RulesHost {
  visible(): boolean;
  redraw(): void;
  undo: Pick<Undoable, "begin">;
}

export interface RulesPanel {
  view(): RulesView;
  load(): Promise<void>;
  /** The menu of a dropdown of the section, by its `data-k`; null for any other. */
  menuFor(k: string): { choices: Choice[]; current: string; pick: (value: string) => void } | null;
  click(target: Element): boolean;
  input(el: HTMLInputElement | HTMLTextAreaElement): boolean;
  key(e: KeyboardEvent): boolean;
  /** The panel is closed or leaves the tab: the rule being changed is closed, what was typed stays. */
  reset(): void;
}

export function createRules(host: RulesHost): RulesPanel {
  let status: RulesView["status"] = "loading";
  let draft: RuleDraft = emptyRulesView().draft;
  let editing: RuleEditor | null = null;
  let hidden: number[] = [];
  let loads = 0;

  const redraw = (): void => {
    if (host.visible()) host.redraw();
  };
  const view = (): RulesView => ({ status, draft, editing, hidden });
  const rules = (): SafetyRule[] => (typeof status === "object" ? status : []);

  async function load(): Promise<void> {
    const mine = ++loads;
    try {
      // The first time, the core puts the three rules of the first run there, in this language.
      const next = await core<{ rules: SafetyRule[] }>("safety.rules.list", { ui_language: getLocale() });
      if (mine !== loads) return;
      status = next.rules;
      if (editing && !next.rules.some((r) => r.id === editing!.id)) editing = null;
    } catch {
      if (mine !== loads) return;
      if (typeof status !== "object") status = "failed";
    }
    redraw();
  }

  async function add(): Promise<void> {
    if (draft.busy) return;
    const problem = ruleProblem(draft.action, rules());
    if (problem) {
      draft = { ...draft, error: problem };
      redraw();
      return;
    }
    draft = { ...draft, busy: true, error: null };
    redraw();
    try {
      await core("safety.rules.create", { action: cleanAction(draft.action), verdict: draft.verdict });
      draft = { action: "", verdict: draft.verdict, error: null, busy: false };
      await load();
    } catch (err) {
      draft = { ...draft, busy: false, error: refusal(err) };
      redraw();
    }
    if (typeof document !== "undefined") document.getElementById("rule-new")?.focus();
  }

  function edit(id: number): void {
    const rule = rules().find((r) => r.id === id);
    if (!rule) return;
    if (editing?.id === id) {
      editing = null;
      redraw();
      return;
    }
    editing = { id, action: rule.action, verdict: rule.verdict, error: null, busy: false };
    redraw();
    if (typeof document !== "undefined") document.getElementById("rule-edit")?.focus();
  }

  async function save(): Promise<void> {
    const e = editing;
    if (!e || e.busy) return;
    const rule = rules().find((r) => r.id === e.id);
    if (!rule) {
      editing = null;
      redraw();
      return;
    }
    const problem = ruleProblem(e.action, rules(), e.id);
    if (problem) {
      editing = { ...e, error: problem };
      redraw();
      return;
    }
    const action = cleanAction(e.action);
    const params: { id: number; action?: string; verdict?: Verdict } = { id: e.id };
    if (action !== rule.action) params.action = action;
    if (e.verdict !== rule.verdict) params.verdict = e.verdict;
    if (params.action === undefined && params.verdict === undefined) {
      editing = null;
      redraw();
      return;
    }
    editing = { ...e, busy: true, error: null };
    redraw();
    try {
      await core("safety.rules.update", params);
      editing = null;
      await load();
    } catch (err) {
      if (editing?.id === e.id) editing = { ...editing, busy: false, error: refusal(err) };
      redraw();
    }
  }

  function remove(id: number): void {
    const rule = rules().find((r) => r.id === id);
    if (!rule) return;
    if (editing?.id === id) editing = null;
    hidden = [...hidden, id];
    redraw();
    host.undo.begin({
      message: t("rules.deleted"),
      commit: async () => {
        await core("safety.rules.delete", { id });
        hidden = hidden.filter((n) => n !== id);
        if (typeof status === "object") status = status.filter((r) => r.id !== id);
        redraw();
      },
      restore: () => {
        hidden = hidden.filter((n) => n !== id);
        redraw();
      },
    });
  }

  function menuFor(k: string): { choices: Choice[]; current: string; pick: (value: string) => void } | null {
    if (k === "rule-new-verdict") {
      return {
        choices: verdictChoices(),
        current: draft.verdict,
        pick: (value) => {
          if (!isVerdict(value) || value === draft.verdict) return;
          draft = { ...draft, verdict: value };
          redraw();
        },
      };
    }
    if (k === "rule-edit-verdict" && editing) {
      return {
        choices: verdictChoices(),
        current: editing.verdict,
        pick: (value) => {
          if (!isVerdict(value) || !editing || value === editing.verdict) return;
          editing = { ...editing, verdict: value };
          redraw();
        },
      };
    }
    return null;
  }

  function click(target: Element): boolean {
    const button = target.closest<HTMLElement>("[data-rule]");
    if (!button) return false;
    const id = Number(button.dataset.id);
    switch (button.dataset.rule) {
      case "add":
        void add();
        return true;
      case "edit":
        edit(id);
        return true;
      case "delete":
        remove(id);
        return true;
      case "cancel":
        editing = null;
        redraw();
        return true;
      case "save":
        void save();
        return true;
    }
    return false;
  }

  /** What is typed is kept as it is typed, without drawing the section again (the caret stays). */
  function input(el: HTMLInputElement | HTMLTextAreaElement): boolean {
    const clearError = (): void => {
      el.closest(".rules-card")?.querySelectorAll(".note[role=alert]").forEach((n) => n.remove());
      el.closest(".field")?.removeAttribute("data-invalid");
    };
    if (el.id === "rule-new") {
      draft = { ...draft, action: el.value, error: null };
      clearError();
      return true;
    }
    if (el.id === "rule-edit" && editing) {
      editing = { ...editing, action: el.value, error: null };
      clearError();
      return true;
    }
    return false;
  }

  function key(e: KeyboardEvent): boolean {
    const field = (e.target as HTMLElement | null)?.id;
    if (field !== "rule-new" && field !== "rule-edit") return false;
    if (e.key === "Enter" && !e.isComposing) {
      e.preventDefault();
      void (field === "rule-new" ? add() : save());
      return true;
    }
    if (e.key === "Escape" && field === "rule-edit") {
      e.preventDefault();
      e.stopPropagation();
      editing = null;
      redraw();
      return true;
    }
    return false;
  }

  function reset(): void {
    editing = null;
    if (draft.error) draft = { ...draft, error: null };
  }

  return { view, load, menuFor, click, input, key, reset };
}

