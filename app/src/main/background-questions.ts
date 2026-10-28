// The questions of the background: a task that needs the owner's yes pauses and waits, at most a day,
// for an answer here (the core's `background/service.py`). Each is a gold card, like the card of
// Raphael's YES/NO question, with an outlined 「否」 and a gold 「是」. In Raphael's thread it is his:
// his bare emblem. In a member's thread it is that member's: their face and name. The question is
// plain words either way; his 《問。…》 belongs to the desktop overlay.
// It is answered here only, never from a chat app (`background.answer`, which the core refuses from
// anywhere else).
//
// The cards are one self-contained element for one member's thread (`memberId`, 0 is Raphael): the
// page that has the thread puts `element` where the thread ends. It reads the questions that wait
// (`background.questions.list {member_id}`) when it is shown and whenever a background task moves
// on (`WorkerChanged`: the core tells it when a question opens and when it is answered).
// `backgroundQuestionHTML` draws one card from a question (pure, so it can be tested without a
// browser). Nothing here logs a question.

import { core } from "./api";
import { avatarHTML, onAvatarChange } from "./avatar";
import { describeError } from "./format";
import { onLocaleChange, t } from "./i18n";
import type { MainEvent } from "./types";
import { esc, toaster } from "./ui";

/** A question that waits, as `background.questions.list` gives it. */
export interface BackgroundQuestion {
  id: string;
  task_id: number;
  member_id: number;
  prefix: string;
  text: string;
  tier: string | null;
  created_at_ms: number;
  expires_at_ms: number;
}

/** What `background.answer` says became of an answer. */
export type AnswerStatus = "approved" | "denied" | "expired" | "cancelled" | "unknown" | string;

/**
 * Who asks in a thread: `name` is null for Raphael (his bare emblem), and a
 * member's name otherwise, with `face` that member's face (members.ts `faceHTML`, 22 px).
 */
export interface Asker {
  face: string;
  name: string | null;
}

/** Raphael's own: the emblem, bare (never framed, filled or shadowed), and no name. */
const RAPHAEL: Asker = {
  get face() {
    return avatarHTML({ size: 22, cls: "bgq-face" });
  },
  name: null,
};

/**
 * One card, then 「否」 (outlined) and 「是」 (gold), the buttons off while it is being answered. By
 * Raphael it carries his emblem; by a member, that member's face and name. The question's words are
 * as they are.
 */
export function backgroundQuestionHTML(q: BackgroundQuestion, busy = false, asker: Asker = RAPHAEL): string {
  const off = busy ? " disabled" : "";
  const ids = ` data-task="${q.task_id}" data-id="${esc(q.id)}"`;
  const head = asker.name === null ? "" : `<span class="bgq-name">${esc(asker.name)}</span>`;
  return (
    `<div class="bgq-card${asker.name === null ? "" : " is-member"}" role="group" aria-label="${esc(t("agent.asking"))}" data-bgq-card="${esc(q.id)}">` +
    `<div class="bgq-q">${asker.face}<span class="bgq-t">${head}<span class="bgq-line">${esc(q.text)}</span></span></div>` +
    `<div class="bgq-row">` +
    `<button type="button" class="btn bgq-no" data-bgq="no"${ids}${off}>${esc(t("agent.no"))}</button>` +
    `<button type="button" class="btn bgq-yes" data-bgq="yes"${ids}${off}>${esc(t("agent.yes"))}</button>` +
    `</div></div>`
  );
}

export interface BackgroundQuestionsHost {
  /** The member whose thread this is (0 is Raphael). */
  memberId(): number;
  /** Who asks in this thread (absent: Raphael). Read each time a card is drawn. */
  asker?(): Asker;
  /** The cards changed (one came or went): the page may keep its thread at the bottom. */
  changed?(): void;
}

export interface BackgroundQuestions {
  /** The cards, to be put where the thread ends. Empty (and hidden) while nothing waits. */
  element: HTMLElement;
  /** Read the questions that wait in this thread again. */
  load(): Promise<void>;
  /** The thread is another member's now: the cards of the last one go before this one's are read. */
  reset(): void;
  /** The cards are drawn again (the asker's name or face changed). */
  redraw(): void;
  /** An event of the window: a task that moved on may have asked, or been answered. */
  follow(event: MainEvent): void;
  /** The questions shown now, oldest first. */
  questions(): readonly BackgroundQuestion[];
}

/** Events that come together (a question and the move of its task) read the questions once. */
const REFRESH_DELAY_MS = 120;

export function createBackgroundQuestions(host: BackgroundQuestionsHost): BackgroundQuestions {
  const element = document.createElement("div");
  element.className = "bgq-list";
  element.hidden = true;
  let shown: BackgroundQuestion[] = [];
  const answering = new Set<string>();
  let loads = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  /** The HTML each card was drawn from. */
  const drawn = new WeakMap<Element, string>();

  /** The cards that changed are drawn again; one that is the same is left alone (its movement is not replayed). */
  function draw(): void {
    const before = new Map<string, Element>();
    for (const card of [...element.children]) before.set((card as HTMLElement).dataset.bgqCard ?? "", card);
    const cards = shown.map((q) => {
      const html = backgroundQuestionHTML(q, answering.has(q.id), host.asker?.());
      const old = before.get(q.id);
      if (old && drawn.get(old) === html) return old;
      const made = document.createElement("template");
      made.innerHTML = html;
      const card = made.content.firstElementChild as HTMLElement;
      drawn.set(card, html);
      return card;
    });
    const changed = cards.length !== before.size || cards.some((card, n) => element.children[n] !== card);
    element.replaceChildren(...cards);
    element.hidden = shown.length === 0;
    if (changed) host.changed?.();
  }

  function reset(): void {
    loads++; // a read still on its way belongs to the last thread
    shown = [];
    answering.clear();
    draw();
  }

  async function load(): Promise<void> {
    const mine = ++loads;
    try {
      const next = await core<{ questions: BackgroundQuestion[] }>("background.questions.list", { member_id: host.memberId() });
      if (mine !== loads) return;
      shown = next.questions;
    } catch {
      if (mine !== loads) return; // the cards that were there stay until the core answers again
    }
    draw();
  }

  async function answer(taskId: number, id: string, approved: boolean): Promise<void> {
    if (answering.has(id)) return;
    answering.add(id);
    draw();
    try {
      const reply = await core<{ status: AnswerStatus }>("background.answer", { task_id: taskId, question_id: id, approved });
      if (reply.status !== "approved" && reply.status !== "denied") toaster.info(t("bgq.gone"));
    } catch (err) {
      toaster.info(describeError(err));
    }
    answering.delete(id);
    await load();
  }

  element.addEventListener("click", (e) => {
    const button = (e.target as Element | null)?.closest<HTMLButtonElement>("[data-bgq]");
    if (!button || button.disabled) return;
    const taskId = Number(button.dataset.task);
    const id = button.dataset.id ?? "";
    if (!Number.isInteger(taskId) || !id) return;
    void answer(taskId, id, button.dataset.bgq === "yes");
  });

  function follow(event: MainEvent): void {
    if (event.type !== "WorkerChanged") return;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      void load();
    }, REFRESH_DELAY_MS);
  }

  onLocaleChange(draw);
  onAvatarChange(draw);

  return { element, load, reset, redraw: draw, follow, questions: () => shown };
}
