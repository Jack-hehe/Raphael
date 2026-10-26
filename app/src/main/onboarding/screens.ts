// What each screen of the guide looks like, as HTML for a given state. Every screen is one title,
// at most one short line, and what the step is about (keys, states, a list); the one main button
// and the way back and on are drawn by mount.ts. Nothing here reads the window or the core, so a
// screen can be drawn from made-up data (the browser check does).

import { t } from "../i18n";

import { DICTATION_MODES } from "../dictation";
import { formatSeconds } from "../format";
import { entryIcon } from "../connections";
import type { StoreService } from "../types";
import { btn, esc, ico, iconButton, keys, sel, st } from "../ui";
import { ABILITIES, type AbilityId } from "./abilities";
import { changeCount, textDiff, type Piece } from "./diff";
import type { Lesson, Miss } from "./practice";
import { capsHTML } from "./keycaps";
import type { SetupState } from "./flow";

/** The title and the one line under it. */
export const head = (title: string, line = ""): string =>
  `<h2 tabindex="-1">${esc(title)}</h2>${line ? `<p class="ob-sub">${esc(line)}</p>` : ""}`;

// ---------- the bar, the way back and the way past ----------

export interface Top {
  segments: { total: number; current: number };
  /** The way back: false on the first screen of a first run. */
  back: boolean;
  /** The way past a step that waits for something outside. */
  skip: boolean;
}

export function topHTML(top: Top): string {
  const bar = Array.from({ length: top.segments.total }, (_, i) => `<i class="${i < top.segments.current ? "done" : i === top.segments.current ? "now" : ""}"></i>`).join("");
  return (
    (top.back ? iconButton("left", t("onboarding.back"), ' data-ob="back"') : `<span class="ob-gap"></span>`) +
    `<div class="ob-bar-steps" role="img" aria-label="${esc(t("onboarding.progress", { p0: Math.max(1, top.segments.current + 1), p1: Math.max(1, top.segments.total) }))}">${bar}</div>` +
    (top.skip ? btn(esc(t("onboarding.skip")), "sm", ' data-ob="skip"') : `<span class="ob-gap"></span>`)
  );
}

// ---------- welcome ----------

export function welcomeHTML(o: { rerun: boolean; languageSelect: string }): string {
  return (
    `<div class="stage onboarding-stage"><canvas class="emb" aria-hidden="true"></canvas><div class="subs" aria-live="polite"></div></div>` +
    head(o.rerun ? t("onboarding.rerunTitle") : t("onboarding.welcomeTitle")) +
    `<div class="ob-main ob-lang"><span class="ob-lang-label">${esc(t("settings.059"))}</span>${o.languageSelect}</div>`
  );
}

// ---------- the checklist of abilities ----------

/** A ticked-or-not row: a real checkbox that the whole row is. */
function abilityRow(id: AbilityId, on: boolean): string {
  const ability = ABILITIES.find((a) => a.id === id)!;
  return (
    `<button type="button" class="ob-ability" role="checkbox" aria-checked="${on}" data-ability="${id}">` +
    `<span class="ob-ability-icon">${ico(ability.icon)}</span>` +
    `<span class="ob-ability-text"><b>${esc(ability.name())}</b><span>${esc(ability.desc())}</span></span>` +
    `<span class="ob-check" aria-hidden="true">${ico("check")}</span>` +
    `</button>`
  );
}

export const abilitiesHTML = (ticked: readonly AbilityId[]): string =>
  head(t("onboarding.abilitiesTitle"), t("onboarding.abilitiesLine")) +
  `<div class="ob-main ob-abilities" role="group" aria-label="${esc(t("onboarding.abilitiesTitle"))}">${ABILITIES.map((a) => abilityRow(a.id, ticked.includes(a.id))).join("")}</div>`;

// ---------- the key ----------

export const keyHTML = (groupHTML: string): string => head(t("onboarding.keyTitle"), t("onboarding.keyLine")) + `<div class="ob-main" id="ob-keygroup">${groupHTML}</div>`;

// ---------- the microphone ----------

export type MicBlock = "system" | "user" | "desktop_apps";

export interface MicView {
  micRowHTML: string;
  /** The privacy settings of Windows keep the microphone shut: which switch. */
  blocked: MicBlock | null;
  heard: boolean;
  modelRowHTML: string;
}

const BLOCK_LINES: Record<MicBlock, () => string> = {
  system: () => t("onboarding.micBlockedSystem"),
  user: () => t("onboarding.micBlockedUser"),
  desktop_apps: () => t("onboarding.micBlockedDesktop"),
};

export function micHTML(v: MicView): string {
  const state = v.blocked ? "" : st(v.heard ? "ok" : "idle", v.heard ? t("onboarding.micHeard") : t("onboarding.micWaiting"));
  const block = v.blocked
    ? `<div class="alert" role="alert">${st("warn", BLOCK_LINES[v.blocked]())}${btn(esc(t("onboarding.micOpenSettings")), "sm", ' data-ob="mic-settings"')}</div>`
    : "";
  return (
    head(t("onboarding.micTitle"), t("onboarding.micLine")) +
    `<div class="ob-main"><div class="list">${v.micRowHTML}${v.modelRowHTML}</div>` +
    `<div class="ob-state" data-ob-mic-state>${state}</div>${block}</div>`
  );
}

/** The model row's control: a bar while fetching, a dot while unpacking. */
export function modelControlHTML(o: { kind: "idle" | "downloading" | "installing" | "ready" | "failed"; percent: number | null; detail: string }): string {
  switch (o.kind) {
    case "idle":
      return st("idle", t("common.checking"));
    case "downloading": {
      const bar = o.percent === null ? "" : `<span class="bar ob-bar" role="progressbar" aria-label="${esc(t("onboarding.modelName"))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${o.percent}"><i style="width:${o.percent}%"></i></span>`;
      return bar + `<span class="val num">${esc(o.percent === null ? t("onboarding.modelPreparing") : o.detail)}</span>`;
    }
    case "installing":
      return `<span class="st"><span class="dot busy"></span>${esc(t("onboarding.modelInstalling"))}</span>`;
    case "ready":
      return st("ok", t("onboarding.modelReady"));
    case "failed":
      return st("err", t("onboarding.modelFailed")) + btn(esc(t("onboarding.retry")), "sm", ' data-ob="model-retry"');
  }
}

// ---------- connections ----------

export interface ConnectionsView {
  services: StoreService[] | "loading" | "failed";
  /** The service whose login is open in the browser. */
  waiting: string | null;
  /** A login that did not come through. */
  failed: string | null;
}

export function connectionsHTML(v: ConnectionsView): string {
  let body: string;
  if (v.services === "loading") body = `<div class="ob-state"><span class="spin" role="status" aria-label="${esc(t("conn.loading"))}"></span></div>`;
  else if (v.services === "failed") body = `<div class="alert" role="alert">${st("err", t("onboarding.connLoadFailed"))}${btn(esc(t("onboarding.retry")), "sm", ' data-ob="conn-reload"')}</div>`;
  else {
    const rows = v.services.map((s) => {
      const connected = s.server_id !== null;
      const waiting = v.waiting === s.id;
      const control = connected
        ? st("ok", t("onboarding.connConnected"))
        : waiting
          ? st("idle", t("onboarding.connWaiting")) + btn(esc(t("common.cancel")), "sm", ' data-ob="conn-cancel"')
          : btn(esc(t("onboarding.connLogin")), "sm", ` data-ob="conn-login" data-service="${esc(s.id)}"${v.waiting ? " disabled" : ""}`);
      return `<div class="row ob-service">${entryIcon(s, "md")}<div class="lbl"><div class="t">${esc(s.name)}</div></div><div class="ctl">${control}</div></div>`;
    });
    body = `<div class="list">${rows.join("")}</div>` + (v.failed ? `<div class="note" role="alert">${st("err", v.failed)}</div>` : "");
  }
  return head(t("onboarding.connTitle"), t("onboarding.connLine")) + `<div class="ob-main" id="ob-conn">${body}</div>`;
}

// ---------- Telegram ----------

export const telegramHTML = (messagingHTML: string): string => head(t("onboarding.tgTitle"), t("onboarding.tgLine")) + `<div class="ob-main" id="ob-tg">${messagingHTML}</div>`;

// ---------- the practice ----------

export interface LessonView {
  lesson: Lesson;
  mode: "toggle" | "hold";
  dictationKeys: string;
  agentKeys: string;
  /** The AltGr layout makes the dictation key a poor choice. */
  altgr: boolean;
  /** The question key looks taken by another program. */
  conflict: boolean;
  /** Why dictating cannot start yet: the speech model is still on its way (a percent, or null when unknown). */
  waitingForModel: { percent: number | null } | null;
  /** The sentence or command to say, in the language of the window. */
  say: string;
}

const MISS_LINES: Record<Miss, () => string> = {
  tooShort: () => t("onboarding.missTooShort"),
  fallback: () => t("onboarding.missFallback"),
  error: () => t("onboarding.missError"),
  cancelled: () => t("onboarding.missCancelled"),
  notCommand: () => t("onboarding.missNotCommand"),
  refused: () => t("onboarding.missRefused"),
  noText: () => t("onboarding.missNoText"),
};

const sayBlock = (say: string): string => `<p class="ob-say">${esc(t("onboarding.quote", { p0: say }))}</p>`;

/** The state of the lesson, as one line with a dot. */
export function lessonStatus(v: LessonView): string {
  const l = v.lesson;
  if (l.phase === "ok") {
    const spent = l.item ? t("onboarding.lessonTook", { p0: formatSeconds(l.item.stt_ms + l.item.llm_ms) }) : "";
    return st("ok", spent ? `${t("onboarding.lessonOk")} · ${spent}` : t("onboarding.lessonOk"));
  }
  if (l.miss && (l.phase === "idle" || l.phase === "failed")) return st("warn", MISS_LINES[l.miss]());
  if (v.waitingForModel && l.id !== "key") {
    const percent = v.waitingForModel.percent === null ? "" : t("onboarding.modelPercent", { p0: v.waitingForModel.percent });
    return st("idle", t("onboarding.modelWaiting", { p0: percent }));
  }
  switch (l.phase) {
    case "pressed":
      return st("idle", t("onboarding.lessonPressed"));
    case "listening":
      return st("ok", t("onboarding.lessonListening"));
    case "processing":
      return st("idle", t(l.id === "ask" || l.id === "act" ? "onboarding.lessonThinking" : "onboarding.lessonProcessing"));
    default:
      return st("idle", t("onboarding.lessonReady"));
  }
}

/** The result of lesson 3: what was said next to what the cleanup made of it. */
export function diffHTML(raw: string, cleaned: string): string {
  const diff = textDiff(raw, cleaned);
  const piece = (p: Piece, cls: string): string => (p.changed ? `<${cls}>${esc(p.text)}</${cls}>` : esc(p.text));
  const count = changeCount(diff);
  return (
    `<div class="ob-diff card">` +
    `<div class="ob-diff-row"><span class="ob-diff-label">${esc(t("onboarding.cleanupSaid"))}</span><p>${diff.spoken.map((p) => piece(p, "del")).join("")}</p></div>` +
    `<div class="ob-diff-row"><span class="ob-diff-label">${esc(t("onboarding.cleanupMade"))}</span><p class="out">${diff.cleaned.map((p) => piece(p, "ins")).join("")}</p></div>` +
    `<div class="ob-diff-foot">${count > 0 ? esc(t("onboarding.cleanupChanged", { p0: count })) : esc(t("onboarding.cleanupSame"))}</div>` +
    `</div>`
  );
}

/** Raphael's reply, as the desktop shows it: 《答。……》, with the question as it was heard. */
export function replyHTML(question: string, prefix: string, text: string): string {
  return (
    `<div class="ob-reply card">` +
    (question ? `<p class="ob-heard">${esc(t("onboarding.heard", { p0: question }))}</p>` : "") +
    `<p class="ob-answer">《${esc(prefix)}。${esc(text)}》</p>` +
    `</div>`
  );
}

export function practiceHTML(v: LessonView): string {
  const l = v.lesson;
  const status = `<div class="ob-state" role="status" data-ob-status>${lessonStatus(v)}</div>`;
  const again = btn(`${ico("refresh")}${esc(t("onboarding.retry"))}`, "sm", ' data-ob="lesson-retry"');
  // A miss offers another try; so does a lesson that shows its result in place of the sentence.
  const retry = l.phase === "failed" || (l.phase === "idle" && l.miss !== null) ? again : "";
  const retryRow = (button: string): string => (button ? `<div class="ob-retry">${button}</div>` : "");
  const box = (rows: number): string =>
    `<textarea class="trybox" id="ob-box" rows="${rows}" placeholder="${esc(t("onboarding.boxPlaceholder"))}" aria-label="${esc(t("onboarding.boxLabel"))}" spellcheck="false"></textarea>`;
  const change = (field: "dictation" | "agent"): string => btn(esc(t("onboarding.changeKey")), "sm", ` data-ob="change-key" data-field="${field}"`);
  switch (l.id) {
    case "key": {
      const mode = sel(DICTATION_MODES, v.mode, t("settings.055"), ' data-ob="mode"');
      const warn = v.altgr ? `<div class="alert" role="alert">${st("warn", t("settings.054"))}${change("dictation")}</div>` : "";
      return (
        head(t(v.mode === "hold" ? "onboarding.keyHoldTitle" : "onboarding.keyToggleTitle"), t(v.mode === "hold" ? "onboarding.keyHoldLine" : "onboarding.keyToggleLine")) +
        `<div class="ob-main"><div class="ob-keys big" data-ob-caps>${capsHTML(v.dictationKeys, t("home.020"))}</div>${status}<div class="ob-mode">${mode}</div>${warn}</div>`
      );
    }
    case "dictate":
      return (
        head(t("onboarding.dictateTitle"), t("onboarding.dictateLine")) +
        `<div class="ob-main">${sayBlock(v.say)}<div class="ob-keys" data-ob-caps>${capsHTML(v.dictationKeys, t("home.020"))}</div>${box(3)}${status}</div>`
      );
    case "cleanup": {
      // Done: the result takes the place of the sentence and the box, so that it is not below the fold.
      if (l.item && l.phase === "ok") {
        return head(t("onboarding.cleanupTitle"), t("onboarding.cleanupLine")) + `<div class="ob-main">${diffHTML(l.item.raw_text, l.item.text)}${status}${retryRow(again)}</div>`;
      }
      return (
        head(t("onboarding.cleanupTitle"), t("onboarding.cleanupLine")) +
        `<div class="ob-main">${sayBlock(v.say)}<div class="ob-keys" data-ob-caps>${capsHTML(v.dictationKeys, t("home.020"))}</div>${box(2)}${status}${retryRow(retry)}</div>`
      );
    }
    case "ask":
    case "act": {
      const title = head(t(l.id === "ask" ? "onboarding.askTitle" : "onboarding.actTitle"), t(l.id === "ask" ? "onboarding.askLine" : "onboarding.actLine"));
      if (l.turn && l.phase === "ok") {
        return title + `<div class="ob-main">${replyHTML(l.turn.question, l.turn.prefix, l.turn.text)}${status}${retryRow(again)}</div>`;
      }
      const conflict = v.conflict ? `<div class="alert" role="alert">${st("warn", t("onboarding.conflict"))}${change("agent")}</div>` : "";
      const then = v.mode === "toggle" ? `<span class="ob-then" aria-hidden="true">→</span>${capsHTML(v.dictationKeys)}` : "";
      return (
        title +
        `<div class="ob-main">${sayBlock(v.say)}<div class="ob-keys" data-ob-caps>${capsHTML(v.agentKeys, t("settings.agentKeys"))}${then}</div>${status}${retryRow(retry)}${conflict}</div>`
      );
    }
  }
}

// ---------- the end ----------

export interface DoneView {
  rerun: boolean;
  /** The steps that were part of this run and how each ended, with what to say about it. */
  rows: { label: string; state: "ok" | "warn"; text: string }[];
  dictationKeys: string;
  agentKeys: string;
  pasteKeys: string;
  /** No key: the guide ends with a way to the settings for it. */
  noKey: boolean;
}

export function doneHTML(v: DoneView): string {
  const rows = v.rows.map((r) => `<div class="row"><div class="lbl"><div class="t">${esc(r.label)}</div></div><div class="ctl">${st(r.state, r.text)}</div></div>`).join("");
  const shortcut = (binding: string, label: string): string => `<div class="ob-shortcut">${capsHTML(binding)}<span>${esc(label)}</span></div>`;
  return (
    head(t("onboarding.doneTitle"), v.noKey ? t("onboarding.doneNoKey") : "") +
    `<div class="ob-main">${rows ? `<div class="list">${rows}</div>` : ""}` +
    `<div class="ob-shortcuts">${shortcut(v.dictationKeys, t("home.020"))}${shortcut(v.agentKeys, t("settings.agentKeys"))}${v.pasteKeys ? shortcut(v.pasteKeys, t("home.021")) : ""}<div class="ob-shortcut">${keys(["Esc"])}<span>${esc(t("common.cancel"))}</span></div></div></div>`
  );
}

/** Which setup states say 「已設定」 and which 「已跳過」, for the end's rows. */
export const setupText = (state: SetupState): { state: "ok" | "warn"; text: string } =>
  state === "done" ? { state: "ok", text: t("onboarding.doneSet") } : { state: "warn", text: t("onboarding.doneSkipped") };
