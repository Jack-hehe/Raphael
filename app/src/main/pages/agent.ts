// 拉斐爾: the one conversation with Raphael (`agent.history.list`), oldest at the top and newest at
// the bottom, under the days it was had, whether it was said to the emblem, sent from a chat app or
// typed here. Scrolling up brings the earlier turns; the page follows the newest only while it is
// read to the end. A turn written or completed (`AgentTurn`) and a background task that took a step
// (`WorkerChanged`) bring the newest turns again, and a task under way can be stopped here.
//
// The composer accepts any file type through the picker, clipboard or thread drop target.
// Up to four files share a 100 MB budget; each file may be up to 50 MB. Media thumbnails and
// file cards show in the tray before sending, and stored copies remain available in history.
// The field asks Raphael in writing
// (`agent_ask_text`, Enter sends, Shift+Enter starts a new line) and Raphael's face is the voice
// button, which does what the question key does (the same `window_hotkey` path) and flies up while
// he listens (voice-mode.ts). When he asks a YES/NO question, a gold card takes the composer's
// place: it is the overlay's question again (`AgentQuestion`), and answering either one answers
// both. With no turn at all the page is Raphael alone, the composer under him. A background task
// that waits for the owner's yes shows its question where the thread ends, as a gold card of its own
// (background-questions.ts), answered here only.
//
// Raphael's own settings (the question key, the weather city) are the 拉斐爾 tab of Settings, which
// the sliders button at the top opens; Telegram is in its 訊息平台 tab.
//
// The page is also where the members are (members-shell.ts): the member column on the left, the
// details panel on the right, and the centred pill over the chat. Each member has one thread, as
// Raphael has: picking a member loads its turns (`agent.history.list {member_id}`), and the composer
// asks it (`agent_ask_text {memberId}`). A member's answers are plain bubbles, with none of Raphael's
// 《prefix。》, its composer says "Message <name>" and has no voice button (the voice always goes to
// Raphael), and the gold card shows in the thread whose member asked. Raphael's thread is exactly as
// it was. While the page is on screen the main sidebar is an icon rail (main.ts). Above the composer
// is the slot of the thread's questions from the background (background-mount.ts). A member's
// thread is its own: the search looks in it alone, and no turn of another thread shows in it but
// the collapsed line of an exchange it had a part in.

import { createAttachmentMedia } from "../attachment-media";
import { fileMime, fileKind, fileSize, fileIcon, mountFileDrop } from "../attachments";
import { onLocaleChange, t } from "../i18n";

import { replyLine } from "../../shared/anime";
import * as api from "../api";
import { createLinkPreviews, type LinkPreview } from "../link-previews";
import { messagePlainText } from "../message-content";
import { avatarHTML, onAvatarChange } from "../avatar";
import "../background-questions.css";
import type { BackgroundQuestions } from "../background-questions";
import { RAPHAEL_ID, faceHTML, memberPlaceholder } from "../members";
import { createMembers } from "../members-shell";
import { mountBackgroundQuestions } from "../background-mount";
import { TYPED, VOICE, mergeTurns, oldestId, refreshTurns, sourceOf, threadItems, turnMatches } from "../conversation";
import { describeError } from "../format";
import { loadingHTML } from "../loading";
import {
  type TimelineState,
  type TurnLook,
  dayHTML,
  emptyTimelineState,
  forgetReruns,
  formatDuration,
  isLive,
  markSeen,
  noteTaskEnds,
  pendingTurnHTML,
  parseMediaPayload,
  requestRerun,
  requestStop,
  safeHttpUrl,
  safeMediaSrc,
  stepsOpen,
  turnHTML,
  wantsRefresh,
} from "../timeline";
import type { AgentDraft, AgentQuestion, AgentTurn, AppContext, Page, SettingsPane, TurnAttachment, WireAttachment } from "../types";
import { btn, copyText, esc, ico, iconButton, toaster } from "../ui";
import { VoiceMode } from "../voice-mode";
import "../agent.css";
import "../members.css";

/** How many turns one load brings. */
const PAGE_SIZE = 50;
/** Within this many px of the bottom, the conversation counts as read to the end and follows new turns. */
const BOTTOM_SLACK_PX = 80;
/** Within this many px of the top, the earlier turns are loaded. */
const TOP_SLACK_PX = 160;
/** Events that come together (a task's step and its turn) bring the turns once. */
const REFRESH_DELAY_MS = 150;
/** How long 「清除全部」 waits for its second press. */
const CLEAR_CONFIRM_MS = 4000;
/** The search waits for the typing to pause this long. */
const SEARCH_DELAY_MS = 200;
/** The longest words the search takes (the core's and the app's limit for `agent.history.search`). */
const SEARCH_MAX_CHARS = 200;
/** The most a typed question may hold, an attached file included (the app's own limit is the same). */
export const TYPED_MAX_CHARS = 100_000;
/** The composer grows with its text up to this many lines, then scrolls. */
const COMPOSER_LINES = 6;
/** A question said aloud whose turn never comes (the core went away) stops waiting after this long. */
const HEARING_MAX_MS = 120_000;
/** The YES of a question that cannot be undone (T3) is held this long, as on the overlay (`HOLD_MS`). */
const HOLD_MS = 600;
/** The voice button sent the key, and the app did not start listening within this long: the picture comes back down. */
const LISTEN_WAIT_MS = 1500;
/** The placeholder of the composer: the same words in every language of the window. */
export const COMPOSER_PLACEHOLDER = "Ask Raphael";

/**
 * The question as the card keeps it: what the app sent, with the strictness it really has. A
 * question with the third answer (letting Raphael operate an app) is always T2, never held.
 */
export function openQuestion(q: AgentQuestion): AgentQuestion {
  const always = typeof q.always === "string" && q.always !== "" ? q.always : undefined;
  return { ...q, always, tier: always ? "T2" : q.tier };
}

/**
 * What the card's buttons say: the question's own words for the two plain answers when it brings
 * them (the question about an app says 「允許一次」 and 「不要」), else the page's 是 and 否; `always`
 * is the third button's label, null when the question has two answers.
 */
export function askLabels(q: AgentQuestion | null): { yes: string; no: string; always: string | null } {
  const always = q?.always ? q.always : null;
  return {
    // The question's own words are used only with a third answer: the plain questions keep 是 and 否.
    yes: (always !== null && q?.yes) || t("agent.yes"),
    no: (always !== null && q?.no) || t("agent.no"),
    always,
  };
}

/** The sliders button: Settings, open on the 拉斐爾 tab, as the sidebar's 設定 opens it on its first. */
export function openRaphaelSettings(app: Pick<AppContext, "go">): void {
  app.go("settings", "agent");
}

/** The words of the empty page: the overlay's greeting to a question, without its last stop. */
export const greetingLine = (): string => t("agent.greet");

/**
 * What a typed question is, from the field and the attached text file: the words, then the file's
 * name in 「」 and its text. Null when there is nothing to send or it is too long. With images or
 * PDFs attached (`withFiles`), no words at all is a question too: "", which the core takes as
 * 「看這個」.
 */
export function typedQuestion(words: string, file: { name: string; text: string } | null, withFiles = false): string | null {
  const head = words.trim();
  const body = file ? `「${file.name}」\n${file.text.trim()}` : "";
  const text = [head, body].filter(Boolean).join("\n\n");
  if ((text === "" && !withFiles) || text.length > TYPED_MAX_CHARS) return null;
  return text;
}

// ---------- attachments ----------
/** Shared with the core: four files, 50 MB each and 100 MB in total. */
export const ATTACH_MAX = 4;
export const FILE_MAX_BYTES = 50 * 1024 * 1024;
export const TOTAL_MAX_BYTES = 100 * 1024 * 1024;
export const IMAGE_MAX_BYTES = FILE_MAX_BYTES;
export const PDF_MAX_BYTES = FILE_MAX_BYTES;
export const PDF_TYPE = "application/pdf";
/** An empty accept list leaves the picker open to every file type. */
export const PICKER_ACCEPT = "";
export const attachmentType = fileMime;
export const attachLimit = (_mime: string): number => FILE_MAX_BYTES;
export function attachRefusal(count: number, _mime: string, size: number, total = 0): "count" | "size" | "total" | "empty" | null {
  if (count >= ATTACH_MAX) return "count";
  if (size <= 0) return "empty";
  if (size > FILE_MAX_BYTES) return "size";
  if (size + total > TOTAL_MAX_BYTES) return "total";
  return null;
}

/** `bytes` in base64, a slice at a time (a whole PDF at once is too long an argument list). */
export function base64Of(bytes: Uint8Array): string {
  let binary = "";
  const SLICE = 0x8000;
  for (let at = 0; at < bytes.length; at += SLICE) binary += String.fromCharCode(...bytes.subarray(at, at + SLICE));
  return btoa(binary);
}

/** A file attached to the next question, with its tray identity and byte count. */
export interface ComposerFile extends WireAttachment {
  id: number;
  size?: number;
}

/** The files as `agent.ask` takes them: name, type and bytes, nothing of the page's own. */
export const wireAttachments = (files: readonly ComposerFile[]): WireAttachment[] => files.map(({ name, mime, data }) => ({ name, mime, data }));

/** Whether a key press in the composer sends: Enter alone, never while an input method composes. */
export const sendsOnEnter = (e: Pick<KeyboardEvent, "key" | "shiftKey" | "isComposing" | "keyCode">): boolean =>
  e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229;

/**
 * The words of a draft the page is handed when it opens (`app.go("agent", "draft:" + words)`, from
 * the 「試試看」 of a connected service), cut to what a question may hold; null for none.
 */
export function draftText(arg: SettingsPane | AgentDraft | undefined): string | null {
  if (typeof arg !== "string" || !arg.startsWith("draft:")) return null;
  return arg.slice("draft:".length).slice(0, TYPED_MAX_CHARS);
}

/** A turn typed here that the history does not have yet. */
interface Outgoing {
  key: string;
  /** The member whose thread it was typed in (0: Raphael's). */
  member: number;
  text: string;
  sentAt: number;
  /** The images and PDFs it went with, by name and type, for its chips. */
  files: TurnAttachment[];
}

export function createAgent(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page page-agent";
  el.dataset.page = "agent";
  el.hidden = true;
  el.innerHTML =
    `<section class="mb-chat">` +
    `<div class="page-head mb-head"><h1 class="mb-h1">${esc(t("agent.title"))}</h1><span class="mb-head-l"></span><span class="mb-head-c"></span><div class="ra-tools">` +
    iconButton("search", t("agent.search"), ' data-act="search" aria-expanded="false" hidden', "ra-tool") +
    btn("", "icon danger ra-tool ra-clear", ' data-act="clear" hidden') +
    iconButton("sliders", t("agent.settings"), ' data-act="settings"', "ra-tool") +
    `</div></div>` +
    `<div class="ra-find" hidden><span class="ra-find-ic">${ico("search")}</span>` +
    `<input type="search" class="ra-find-input" autocomplete="off" spellcheck="false" maxlength="${SEARCH_MAX_CHARS}">` +
    `<span class="ra-find-count num" aria-live="polite"></span>` +
    iconButton("x", t("agent.searchClose"), ' data-act="search-close"', "ra-tool") +
    `</div>` +
    `<div class="ra-stage">` +
    `<div class="ra-scroll"><div class="ra-col"><div class="ra-older" hidden></div><ol class="thread"></ol><p class="ra-none" hidden></p></div></div>` +
    `<div class="ra-wait" hidden></div>` +
    `<div class="ra-empty" hidden><div class="ra-hero"></div><p class="ra-hi"></p><div class="ra-slot"></div></div>` +
    `<div class="ra-dock"></div>` +
    `</div>` +
    `</section>`;
  const heading = el.querySelector<HTMLElement>("h1")!;
  const searchButton = el.querySelector<HTMLElement>('[data-act="search"]')!;
  const clearButton = el.querySelector<HTMLElement>('[data-act="clear"]')!;
  const settingsButton = el.querySelector<HTMLElement>('[data-act="settings"]')!;
  const find = el.querySelector<HTMLElement>(".ra-find")!;
  const findInput = el.querySelector<HTMLInputElement>(".ra-find-input")!;
  const findCount = el.querySelector<HTMLElement>(".ra-find-count")!;
  const stage = el.querySelector<HTMLElement>(".ra-stage")!;
  const scroller = el.querySelector<HTMLElement>(".ra-scroll")!;
  const older = el.querySelector<HTMLElement>(".ra-older")!;
  const thread = el.querySelector<HTMLOListElement>(".thread")!;
  const none = el.querySelector<HTMLElement>(".ra-none")!;
  const wait = el.querySelector<HTMLElement>(".ra-wait")!;
  const empty = el.querySelector<HTMLElement>(".ra-empty")!;
  const hero = el.querySelector<HTMLElement>(".ra-hero")!;
  const hi = el.querySelector<HTMLElement>(".ra-hi")!;
  const slot = el.querySelector<HTMLElement>(".ra-slot")!;
  const dock = el.querySelector<HTMLElement>(".ra-dock")!;
  /** The open thread's questions from the background, above the composer wherever it is. */
  let waiting: BackgroundQuestions | null = null;
  const backgroundSlot = document.createElement("div");
  backgroundSlot.className = "ra-bgq";
  backgroundSlot.dataset.mount = "background-questions";

  // ---------- the members ----------

  const members = createMembers({
    app,
    visible: () => visible,
    onSelect: () => switchThread(),
    onUpdate: () => {
      drawComposer();
      drawFaces();
      hi.textContent = emptyLine();
      if (visible && !scroller.hidden) drawThread();
    },
  });
  el.prepend(members.column);
  el.append(members.details);
  if (members.detailsOpen()) el.dataset.det = "open";
  el.querySelector(".mb-head-l")!.append(members.picker);
  el.querySelector(".mb-head-c")!.append(members.pill);
  /** The member whose thread is open: 0 is Raphael. */
  const memberId = (): number => members.current();
  const inRaphael = (): boolean => memberId() === RAPHAEL_ID;

  // ---------- the composer and the approval card ----------

  const composer = document.createElement("form");
  composer.className = "composer";
  composer.noValidate = true;
  composer.innerHTML =
    `<div class="cmp-file" hidden></div>` +
    `<div class="cmp-row">` +
    `<button type="button" class="btn cmp-attach" data-act="attach">${ico("plus")}</button>` +
    `<textarea class="cmp-input" rows="1" autocomplete="off" enterkeyhint="send"></textarea>` +
    `<button type="button" class="btn cmp-voice" data-act="voice"></button>` +
    `</div>` +
    `<input class="cmp-picker" type="file" multiple hidden>`;
  const input = composer.querySelector<HTMLTextAreaElement>(".cmp-input")!;
  const attachButton = composer.querySelector<HTMLElement>(".cmp-attach")!;
  const voiceButton = composer.querySelector<HTMLButtonElement>(".cmp-voice")!;
  const fileChip = composer.querySelector<HTMLElement>(".cmp-file")!;
  const picker = composer.querySelector<HTMLInputElement>(".cmp-picker")!;
  input.placeholder = COMPOSER_PLACEHOLDER;
  input.setAttribute("aria-label", COMPOSER_PLACEHOLDER);

  const askCard = document.createElement("div");
  askCard.className = "ask-card";
  askCard.hidden = true;
  askCard.setAttribute("role", "group");
  askCard.innerHTML =
    `<span class="ask-face"></span><p class="ask-q"></p>` +
    `<div class="ask-row"><button type="button" class="btn ask-no" data-act="no"></button>` +
    `<button type="button" class="btn ask-always" data-act="always" hidden></button>` +
    `<button type="button" class="btn ask-yes" data-act="yes"><span class="ask-yes-t"></span><i class="ask-fill" aria-hidden="true"></i></button></div>`;
  const askFace = askCard.querySelector<HTMLElement>(".ask-face")!;
  const askText = askCard.querySelector<HTMLElement>(".ask-q")!;
  const noButton = askCard.querySelector<HTMLButtonElement>(".ask-no")!;
  const alwaysButton = askCard.querySelector<HTMLButtonElement>(".ask-always")!;
  const yesButton = askCard.querySelector<HTMLButtonElement>(".ask-yes")!;

  // ---------- what the page keeps ----------

  /** What is loaded, newest first. */
  let turns: AgentTurn[] = [];
  let canLoadEarlier = false;
  let loadingEarlier = false;
  let failure: (() => string) | null = null;
  let pending = false;
  let loads = 0;
  let visible = false;
  const details = new Set<number>();
  const state: TimelineState = emptyTimelineState();
  const attachmentMedia = createAttachmentMedia(scroller, id => api.core("attachments.get", { id }));
  const previews = createLinkPreviews(scroller, url => api.core<LinkPreview | null>("links.preview", { url }));
  const regenerating = new Set<number>();
  const messageResize = typeof ResizeObserver === "function" ? new ResizeObserver(() => measureMessages()) : null;
  /** While the toast of 「清除全部」 waits: the turns up to this id are left out. */
  let clearedUpTo = 0;
  let refreshTimer: ReturnType<typeof setTimeout> | null = null;
  /** 「清除全部」 was pressed once: a second press within a few seconds clears. */
  let confirmingClear = false;
  let clearTimer: ReturnType<typeof setTimeout> | undefined;
  let ticker: ReturnType<typeof setInterval> | null = null;
  /** Questions typed here whose turns have not come yet. */
  let outgoing: Outgoing[] = [];
  let sent = 0;
  /** A question said aloud is being answered: since when, and the newest turn there was then. */
  let hearing: { since: number; after: number } | null = null;
  /** Ends `hearing` at `HEARING_MAX_MS` when neither its turn nor any other word of the core comes. */
  let hearingTimer: ReturnType<typeof setTimeout> | undefined;
  /** Draft file bytes, kept in memory until the request is sent. */
  let files: ComposerFile[] = [];
  let fileIds = 0;
  let attachmentEpoch = 0;
  let attaching = 0;
  let attachQueue: Promise<void> = Promise.resolve();
  const dropOverlay = document.createElement("div");
  dropOverlay.className = "attachment-drop";
  dropOverlay.hidden = true;
  dropOverlay.setAttribute("role", "status");
  dropOverlay.innerHTML = `${ico("file")}<span></span>`;
  stage.append(dropOverlay);
  function measureDrop(): void {
    const bounds = stage.getBoundingClientRect();
    const control = composer.hidden ? askCard : composer;
    const top = control.getBoundingClientRect().top;
    dropOverlay.style.bottom = `${Math.max(12, bounds.bottom - top + 12)}px`;
  }
  const dropResize = typeof ResizeObserver === "function" ? new ResizeObserver(measureDrop) : null;
  stage.addEventListener("dragenter", measureDrop, true);
  stage.addEventListener("dragover", measureDrop, true);
  const resetDrop = mountFileDrop(stage, dropOverlay, list => { void attachAll(list); });
  /** The chip of each of them, made once (an image's picture is not loaded again at each drawing). */
  const fileChips = new Map<number, HTMLElement>();
  /** The YES/NO question that waits for an answer, and whether an answer is on its way. */
  let question: AgentQuestion | null = null;
  let answering = false;
  /** The member a typed question is on its way to, while it is: a question it asks is its thread's. */
  let askingMember: number | null = null;
  /** The thread of the question that waits (see `setQuestion`). */
  let questionOwner = RAPHAEL_ID;
  /** The exchanges between members that are open, by turn id. */
  const openExchanges = new Set<number>();
  /** The search: open or not, its words, and what the core found (null: the page looks itself). */
  let searching = false;
  let query = "";
  let found: AgentTurn[] | null = null;
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  let searches = 0;
  /** What each line of the thread was drawn from, so that a line whose HTML is the same stays as it is. */
  const drawnHTML = new WeakMap<Element, string>();
  /** The newest turn drawn: a turn after it comes in from below. */
  let newestDrawn = 0;
  let drewOnce = false;

  const atBottom = (): boolean => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= BOTTOM_SLACK_PX;
  const shownTurns = (): AgentTurn[] => turns.filter((turn) => turn.id > clearedUpTo);
  const newestId = (): number => turns.reduce((max, turn) => Math.max(max, turn.id), 0);
  const isEmpty = (): boolean =>
    shownTurns().length === 0 && !outgoing.some((sent) => sent.member === memberId()) && (hearing === null || !inRaphael());

  const look = (): TurnLook => {
    const latest = shownTurns().filter(turn => turn.question.trim() && sourceOf(turn) !== "routine" && sourceOf(turn) !== "member" && turn.route !== "exchange" && (turn.text.trim() || turn.messages?.some(m => m.kind === "reply"))).reduce((id, turn) => Math.max(id, turn.id), 0);
    const busy = regenerating.size > 0 || outgoing.some(sent => sent.member === memberId()) || shownTurns().some(turn => turn.status === "running" || isLive(turn.task));
    const shared = { regenerateTurnId: busy ? undefined : latest, query: searching ? query : "", openExchanges, memberName: members.nameOf, memberFace: (id: number) => members.faceOf(id, 18) };
    if (inRaphael()) return { ...shared, face: (loop) => avatarHTML({ size: 26, loop }) };
    const member = members.member();
    return { ...shared, face: () => faceHTML(member, 26), plain: true };
  };
  /**
   * The question that waits now (null: none), and whose thread it is in: the member a typed
   * question was on its way to when it came, whose answer is what asks; else Raphael's (the voice
   * and the overlay are his). The thread is kept with the question, so that what is typed later
   * does not move it.
   */
  function setQuestion(next: AgentQuestion | null): void {
    if (next !== null && next.id !== question?.id) questionOwner = askingMember ?? RAPHAEL_ID;
    question = next;
  }

  const voice = new VoiceMode({
    stage,
    button: () => (voiceButton.isConnected && !composer.hidden && !voiceButton.hidden ? voiceButton : null),
    composer: () => (composer.isConnected && !composer.hidden ? composer : null),
    target: () => (empty.hidden ? null : hero.querySelector<HTMLElement>(".av")),
    face: () => avatarHTML({ size: 132, loop: true }),
    onClick: () => void endVoice(),
  });

  // ---------- drawing ----------

  /** The trash button: an icon, and the words of the question while it waits for the second press. */
  function drawClear(): void {
    const label = confirmingClear ? t("agent.clearConfirm") : t("agent.clear");
    clearButton.classList.toggle("is-confirm", confirmingClear);
    clearButton.setAttribute("aria-label", label);
    clearButton.title = label;
    clearButton.innerHTML = ico("trash") + (confirmingClear ? `<span>${esc(label)}</span>` : "");
  }

  /**
   * The face of who the thread is with, where the composer, the card and the empty page show it:
   * Raphael's (his voice button too), or the member's (whose composer has no voice button).
   */
  function drawFaces(): void {
    voiceButton.innerHTML = avatarHTML({ size: 34 });
    if (inRaphael()) {
      askFace.innerHTML = avatarHTML({ size: 30 });
      hero.innerHTML = avatarHTML({ size: 132, loop: true });
    } else {
      const member = members.member();
      askFace.innerHTML = faceHTML(member, 30);
      hero.innerHTML = faceHTML(member, 96);
    }
    voice.redraw();
  }

  /** The composer of the thread: "Ask Raphael" and his voice button, or "Message <name>" with none. */
  function drawComposer(): void {
    const words = inRaphael() ? COMPOSER_PLACEHOLDER : memberPlaceholder(members.member().name);
    input.placeholder = words;
    input.setAttribute("aria-label", words);
    voiceButton.hidden = !inRaphael();
    settingsButton.hidden = !inRaphael();
    empty.classList.toggle("is-member", !inRaphael());
  }

  /** The words of the empty page: Raphael's greeting, or the member's name. */
  const emptyLine = (): string => (inRaphael() ? greetingLine() : members.member().name);

  /** The words of the composer, the card and the search, in the language of the window. */
  function drawWords(): void {
    dropOverlay.querySelector("span")!.textContent = t("agent.dropFiles");
    heading.textContent = t("agent.title");
    for (const [button, label] of [
      [settingsButton, t("agent.settings")],
      [searchButton, t("agent.search")],
      [attachButton, t("agent.attach")],
      [voiceButton, t("agent.voice")],
      [find.querySelector<HTMLElement>('[data-act="search-close"]')!, t("agent.searchClose")],
    ] as const) {
      button.setAttribute("aria-label", label);
      button.title = label;
    }
    findInput.placeholder = t("agent.search");
    findInput.setAttribute("aria-label", t("agent.search"));
    hi.textContent = emptyLine();
    drawAskLabels();
    askCard.setAttribute("aria-label", t("agent.asking"));
    drawFile();
    drawClear();
  }

  /** Keep draft cards mounted while their filenames and remove labels stay current. */
  function drawFile(): void {
    const follows = scroller.scrollHeight - scroller.clientHeight - scroller.scrollTop <= BOTTOM_SLACK_PX;
    for (const [id, chip] of fileChips) {
      if (files.some((file) => file.id === id)) continue;
      chip.remove();
      fileChips.delete(id);
    }
    for (const file of files) {
      let chip = fileChips.get(file.id);
      if (!chip) {
        chip = fileChipElement(file);
        fileChips.set(file.id, chip);
      }
      fileChip.append(chip); // preserve attachment order
      const x = chip.querySelector<HTMLElement>(".chip-x")!;
      x.setAttribute("aria-label", t("agent.unattach"));
      x.title = t("agent.unattach");
    }
    fileChip.hidden = files.length === 0;
    if (follows) scroller.scrollTop = scroller.scrollHeight;
  }

  /** A draft card with a thumbnail or type icon, size and a remove button. */
  function fileChipElement(file: ComposerFile): HTMLElement {
    const chip = document.createElement("span");
    chip.className = "cmp-chip";
    chip.dataset.file = String(file.id);
    chip.innerHTML =
      `<span class="cmp-tile">${fileKind(file.mime) === "image" ? `<img class="cmp-thumb" alt="" width="40" height="40">` : fileIcon(file)}</span>` +
      `<span class="attachment-name"><span class="cmp-chip-t">${esc(file.name)}</span><small>${esc(fileSize(file.size))}</small></span>` +
      `<button type="button" class="chip-x" data-act="unattach-file" data-file="${file.id}">${ico("x")}</button>`;
    const thumb = chip.querySelector<HTMLImageElement>(".cmp-thumb");
    // A raster thumbnail from the draft's own bytes.
    if (thumb) thumb.src = `data:${file.mime};base64,${file.data}`;
    return chip;
  }

  /** The words of the card's buttons: the page's own 是 and 否, or the question's (and its third button, if it has one). */
  function drawAskLabels(): void {
    const labels = askLabels(question);
    noButton.textContent = labels.no;
    askCard.querySelector<HTMLElement>(".ask-yes-t")!.textContent = labels.yes;
    alwaysButton.textContent = labels.always ?? "";
    alwaysButton.hidden = labels.always === null;
  }

  /** The composer or, while Raphael asks, the card in its place; under the thread, or under Raphael on the empty page. */
  function drawDock(): void {
    const home = empty.hidden ? dock : slot;
    if (backgroundSlot.parentElement !== home) home.prepend(backgroundSlot);
    if (composer.parentElement !== home) home.append(composer);
    const asking = question !== null && questionOwner === memberId();
    const waitingTurn = [...shownTurns()].reverse().find((turn) => turn.status === "confirm");
    const inline = asking && waitingTurn ? thread.querySelector<HTMLElement>(`[data-question-turn="${waitingTurn.id}"]`) : null;
    const cardHome = inline ?? home;
    if (askCard.parentElement !== cardHome) cardHome.append(askCard);
    askCard.classList.toggle("is-member", !inRaphael());
    if (asking && !askCard.hidden && askCard.dataset.id === question!.id) return;
    askCard.hidden = !asking;
    composer.hidden = asking;
    if (!asking) {
      delete askCard.dataset.id;
      return;
    }
    if (voice.isUp) voice.drop();
    const q = question!;
    askCard.dataset.id = q.id;
    askCard.dataset.tier = q.tier;
    // a member asks in plain words, Raphael in his 《prefix。》
    askText.textContent = inRaphael() ? replyLine(q.prefix, q.text) : q.text;
    drawAskLabels();
    answering = false;
    noButton.disabled = alwaysButton.disabled = yesButton.disabled = false;
    yesButton.dataset.hold = "idle";
  }

  /** One line of the thread as HTML, with the key it keeps across draws. */
  function lines(): { key: string; html: string; turnId?: number }[] {
    const base = searching && query.trim() !== "" ? (found ?? shownTurns().filter((turn) => turnMatches(turn, query))) : shownTurns();
    const shape = look();
    const out = threadItems(base, state.nowMs).map((item) =>
      item.kind === "day"
        ? { key: item.key, html: dayHTML(item.label) }
        : { key: item.key, html: turnHTML(item.turn, state, details.has(item.turn.id), shape, item.inner), turnId: item.turn.id },
    );
    if (!(searching && query.trim() !== "")) {
      for (const sent of outgoing) if (sent.member === memberId()) out.push({ key: sent.key, html: pendingTurnHTML(sent.text, TYPED, shape, sent.files) });
      // the voice always goes to Raphael
      if (hearing && inRaphael()) out.push({ key: "hearing", html: pendingTurnHTML(null, VOICE, shape) });
    }
    return out;
  }

  /**
   * Draws the thread again where it changed: a line whose HTML is the same is left alone (its
   * movement is not replayed, its pictures not loaded again), a changed one is replaced where it
   * is, and the lines that are new are put in their places without moving the others.
   */
  function drawThread(): void {
    const wanted = lines();
    const before = new Map<string, Element>();
    for (const child of [...thread.children]) before.set((child as HTMLElement).dataset.key ?? "", child);
    const droppedPending = [...before.keys()].some((key) => (key.startsWith("out:") || key === "hearing") && !wanted.some((line) => line.key === key));
    const focusKey = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>("[data-act][data-focus]")?.dataset.focus;
    const keep = new Set<Element>();
    let at = 0;
    for (const line of wanted) {
      let node = before.get(line.key);
      if (!node || drawnHTML.get(node) !== line.html) {
        const fresh = document.createElement("template");
        fresh.innerHTML = line.html;
        const made = fresh.content.firstElementChild as HTMLElement;
        made.dataset.key = line.key;
        for (const control of made.querySelectorAll<HTMLElement>("[data-act]")) control.dataset.focus = `${control.dataset.act}:${control.dataset.task ?? line.key}:${control.closest<HTMLElement>("[data-message]")?.dataset.message ?? ""}:${control.dataset.url ?? ""}`;
        drawnHTML.set(made, line.html);
        // A turn that came after the newest one drawn, or a question just sent, comes in from below.
        const arrives = drewOnce && !node && (line.turnId === undefined ? !line.key.startsWith("day:") : line.turnId > newestDrawn && !droppedPending);
        if (arrives) made.classList.add("is-new");
        if (node) node.replaceWith(made);
        node = made;
      }
      keep.add(node);
      const here = thread.children[at];
      if (here !== node) thread.insertBefore(node, here ?? null);
      at++;
    }
    for (const child of [...thread.children]) if (!keep.has(child)) child.remove();
    if (focusKey) thread.querySelector<HTMLElement>(`[data-focus="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    newestDrawn = Math.max(newestDrawn, ...wanted.map((line) => line.turnId ?? 0));
    drewOnce = true;
    if (visible) attachmentMedia.sync();
    if (visible) previews.sync();
    else previews.pause();
    measureMessages();
  }

  /** Re-check wrapped lines when the window or the member details panel changes width. */
  function measureMessages(): void {
    for (const message of thread.querySelectorAll<HTMLElement>("[data-expandable]")) {
      const text = message.querySelector<HTMLElement>(".message-text")!;
      const toggle = message.querySelector<HTMLButtonElement>('[data-act="expand-message"]')!;
      if (text.clientHeight > 0) toggle.hidden = !message.classList.contains("is-expanded") && text.scrollHeight <= text.clientHeight + 1;
    }
  }

  function render(follow = false): void {
    stage.setAttribute("aria-busy", String(pending));
    if (pending && isEmpty()) {
      wait.innerHTML = loadingHTML();
      wait.hidden = false;
      scroller.hidden = empty.hidden = dock.hidden = true;
      searchButton.hidden = clearButton.hidden = true;
      return;
    }
    wait.hidden = true;
    const shown = shownTurns();
    // 清除全部 empties Raphael's conversation; a member's thread goes with the member
    clearButton.hidden = shown.length === 0 || !inRaphael();
    searchButton.hidden = shown.length === 0;
    if (shown.length === 0 && searching) closeSearch(false);
    if (isEmpty()) {
      scroller.hidden = dock.hidden = true;
      empty.hidden = false;
      empty.classList.toggle("is-failed", failure !== null);
      hi.textContent = failure ? `${t("agent.loadFailed")} ${failure()}` : emptyLine();
      drawDock();
      stopTicker();
      return;
    }
    const wasEmpty = scroller.hidden;
    empty.hidden = true;
    scroller.hidden = dock.hidden = false;
    const keepUp = follow || wasEmpty || atBottom();
    drawDock();
    state.nowMs = Date.now();
    noteTaskEnds(shown, state);
    drawThread();
    drawDock();
    markSeen(shown, state);
    const searchingNow = searching && query.trim() !== "";
    none.hidden = !(searchingNow && thread.querySelector(".tn") === null);
    none.textContent = t("agent.noMatch");
    findCount.textContent = searchingNow ? String(thread.querySelectorAll(".tn:not(.is-pending)").length) : "";
    older.hidden = !loadingEarlier;
    older.innerHTML = loadingEarlier ? loadingHTML() : "";
    if (keepUp) scroller.scrollTop = scroller.scrollHeight;
    if (shown.some((turn) => isLive(turn.task))) startTicker();
    else stopTicker();
    // A first page that does not fill the window has no scrolling to bring the earlier ones.
    if (canLoadEarlier && !loadingEarlier && !searchingNow && scroller.scrollHeight <= scroller.clientHeight) void load(true);
  }

  /** The time since a task began, each second, in the lines that show it. */
  function tick(): void {
    const now = Date.now();
    for (const line of thread.querySelectorAll<HTMLElement>("[data-since]")) line.textContent = formatDuration(now - Number(line.dataset.since));
  }
  function startTicker(): void {
    if (ticker === null && visible) ticker = setInterval(tick, 1000);
  }
  function stopTicker(): void {
    if (ticker !== null) clearInterval(ticker);
    ticker = null;
  }

  // Event messages also cover a missed data-change notification from an older core.
  const routineEvents = new Set<string>();

  // ---------- loading ----------

  /** Loads the newest turns, or with `earlier` the ones before what is loaded, keeping the reader's place. */
  async function load(earlier: boolean): Promise<void> {
    if (earlier && (loadingEarlier || !canLoadEarlier)) return;
    const mine = earlier ? loads : ++loads;
    if (earlier) loadingEarlier = true;
    else pending = true;
    if (turns.length === 0 && !earlier) render();
    else stage.setAttribute("aria-busy", "true");
    let got: AgentTurn[];
    try {
      const before = earlier ? oldestId(turns) : undefined;
      const member = memberId();
      got = (
        await api.agentHistoryList({
          limit: PAGE_SIZE,
          ...(before === undefined ? {} : { before_id: before }),
          ...(member === RAPHAEL_ID ? {} : { member_id: member }),
        })
      ).turns;
    } catch (err) {
      // A newer load of the newest turns took over: what this one shows does not count, but an
      // earlier page that failed must still let the reader page up again.
      if (mine !== loads) {
        if (earlier) loadingEarlier = false;
        return;
      }
      if (earlier) loadingEarlier = false;
      else pending = false;
      if (turns.length === 0) {
        canLoadEarlier = false;
        failure = () => describeError(err);
      } else {
        toaster.info(describeError(err)); // what is on screen stays
      }
      render();
      return;
    }
    if (mine !== loads) {
      if (earlier) loadingEarlier = false;
      return;
    }
    let routinesChanged = false;
    for (const turn of got) {
      for (const message of turn.messages ?? []) {
        if (message.kind !== "event") continue;
        const key = `${turn.id}:${message.seq}`;
        if (routineEvents.has(key)) continue;
        routineEvents.add(key);
        routinesChanged = true;
      }
    }
    if (routinesChanged) members.refreshRoutines(memberId());
    const fromBottom = scroller.scrollHeight - scroller.scrollTop;
    if (earlier) {
      loadingEarlier = false;
      turns = mergeTurns(turns, got);
      canLoadEarlier = got.length === PAGE_SIZE;
    } else {
      pending = false;
      // The newest page replaces what it covers; what was paged back to beyond it stays while it
      // joins the page, and is dropped when more came meanwhile than a page holds (refreshTurns).
      const next = refreshTurns(turns, got, PAGE_SIZE);
      canLoadEarlier = next.keptOlder ? canLoadEarlier : got.length === PAGE_SIZE;
      turns = next.turns;
    }
    // A task that ended is no longer being stopped.
    for (const turn of turns) if (turn.task && !isLive(turn.task)) state.stopping.delete(turn.task.id);
    forgetReruns(state, turns);
    settlePending();
    failure = null;
    render();
    // The earlier turns come in above the reader, who stays where they were.
    if (earlier) scroller.scrollTop = scroller.scrollHeight - fromBottom;
  }

  /** The questions in the history now are no longer waited for: a typed one by its words, a spoken one by any new turn of the voice. */
  function settlePending(): void {
    const current = memberId();
    const sinceSent = (sent: Outgoing): AgentTurn | undefined =>
      sent.member === current
        ? turns.find(
            (turn) =>
              (turn.member_id ?? RAPHAEL_ID) === sent.member &&
              sourceOf(turn) === TYPED &&
              turn.question.trim() === sent.text.trim() &&
              turn.created_at_ms >= sent.sentAt - 5_000,
          )
        : undefined;
    outgoing = outgoing.filter((sent) => sinceSent(sent) === undefined);
    if (
      hearing &&
      ((inRaphael() && turns.some((turn) => turn.id > hearing!.after && sourceOf(turn) === VOICE)) ||
        Date.now() - hearing.since > HEARING_MAX_MS)
    ) {
      stopHearing();
    }
  }

  /**
   * A question said aloud is being answered: its line shows until its turn comes, a last phase of
   * the dictation says it ended, or `HEARING_MAX_MS` has passed. That last one is a timer of its
   * own, because a core that crashed or restarted sends neither a turn nor a phase, and no history
   * load would come to settle it.
   */
  function startHearing(): void {
    hearing = { since: Date.now(), after: newestId() };
    clearTimeout(hearingTimer);
    hearingTimer = setTimeout(() => {
      hearingTimer = undefined;
      if (!hearing) return;
      hearing = null;
      if (visible) render();
    }, HEARING_MAX_MS);
  }
  function stopHearing(): void {
    hearing = null;
    clearTimeout(hearingTimer);
    hearingTimer = undefined;
  }

  function scheduleRefresh(): void {
    if (!visible || refreshTimer !== null) return;
    refreshTimer = setTimeout(() => {
      refreshTimer = null;
      void load(false);
    }, REFRESH_DELAY_MS);
  }

  // ---------- asking ----------

  /** Sends what the composer holds as a typed question: it shows at once, and Raphael's answer when it lands. */
  async function send(): Promise<void> {
    if (attaching) {
      const epoch = attachmentEpoch;
      await attachQueue;
      if (epoch !== attachmentEpoch) return;
    }
    const text = typedQuestion(input.value, null, files.length > 0);
    if (text === null) {
      if (input.value.trim() !== "") toaster.info(t("agent.tooLong"));
      return;
    }
    const sending = files;
    const member = memberId();
    const mine: Outgoing = { key: `out:${++sent}`, member, text, sentAt: Date.now(), files: sending.map(({ name, mime, data, size }) => ({ name, mime, size, preview: fileKind(mime) !== "file" ? `data:${mime};base64,${data}` : undefined })) };
    outgoing.push(mine);
    input.value = "";
    files = [];
    drawFile();
    fit();
    render(true);
    askingMember = member;
    try {
      if (member === RAPHAEL_ID) await api.agentAskText(text, wireAttachments(sending));
      else await api.agentAskText(text, wireAttachments(sending), member);
    } catch (err) {
      if (askingMember === member) askingMember = null;
      outgoing = outgoing.filter((item) => item !== mine);
      if (memberId() === member) {
        if (input.value === "") {
          input.value = text;
          fit();
        }
        // The files come back to the composer, unless others were attached meanwhile.
        if (files.length === 0 && sending.length > 0) {
          files = sending;
          drawFile();
        }
      }
      toaster.info(describeError(err));
      render();
      return;
    }
    if (askingMember === member) askingMember = null;
    // The turn is written by now: the history brings it, and the question waits no more.
    if (memberId() === member) await load(false);
    else members.changed();
    outgoing = outgoing.filter((item) => item !== mine);
    render();
  }

  /** The field grows with its text, up to `COMPOSER_LINES` lines. */
  function fit(): void {
    input.style.height = "auto";
    const line = parseFloat(getComputedStyle(input).lineHeight) || 22;
    input.style.height = `${Math.min(input.scrollHeight, line * COMPOSER_LINES + 2)}px`;
    input.classList.toggle("is-tall", input.scrollHeight > line * COMPOSER_LINES + 2);
  }

  /**
   * A draft handed to the page: its words in the field in place of what was there, to be sent or
   * changed, never sent by itself. The field has the focus, the caret after the words, once the
   * composer is where it stays (`focusComposer`).
   */
  function putDraft(words: string): void {
    input.value = words;
    fit();
    focusComposer();
  }

  /**
   * The focus in the field, the caret at the end, when the composer is on the page: not while
   * Raphael's YES/NO card stands in its place (the draft waits in the field under it).
   */
  function focusComposer(): void {
    if (!visible || composer.hidden || !composer.isConnected) return;
    input.focus({ preventScroll: true });
    input.setSelectionRange(input.value.length, input.value.length);
  }

  /**
   * 「＋」 or a paste: files of this PC for the next question, one after the other. One that cannot
   * be taken says why and the rest are still taken, but for too many: the first one over the limit
   * says so and ends it.
   */
  async function attachAll(list: readonly File[]): Promise<void> {
    const epoch = attachmentEpoch;
    attaching++;
    composer.setAttribute("aria-busy", "true");
    attachQueue = attachQueue.then(async () => {
      for (const file of list) {
        if (epoch !== attachmentEpoch) break;
        if (!(await attachFile(file, attachmentType(file), epoch))) break;
      }
    }).finally(() => {
      attaching--;
      if (!attaching) composer.removeAttribute("aria-busy");
    });
    await attachQueue;
  }

  async function attachFile(file: File, mime: string, epoch: number): Promise<boolean> {
    const name = file.name.trim() || `image.${mime.slice(mime.indexOf("/") + 1)}`;
    const refusal = attachRefusal(files.length, mime, file.size, files.reduce((sum, item) => sum + (item.size ?? 0), 0));
    if (refusal === "count") {
      toaster.info(t("agent.attachTooMany", { p0: ATTACH_MAX }));
      return false;
    }
    if (refusal === "size") {
      toaster.info(t("agent.attachTooBig", { p0: name, p1: FILE_MAX_BYTES / 1024 / 1024 }));
      return true;
    }
    if (refusal === "total") {
      toaster.info(t("agent.attachTotal", { p0: TOTAL_MAX_BYTES / 1024 / 1024 }));
      return true;
    }
    let data: string;
    try {
      if (refusal === "empty") throw new Error("empty");
      data = base64Of(new Uint8Array(await file.arrayBuffer()));
    } catch {
      toaster.info(t("agent.attachUnreadable", { p0: name }));
      return true;
    }
    if (epoch !== attachmentEpoch) return false;
    // Others may have been attached while this one was read.
    if (files.length >= ATTACH_MAX) {
      toaster.info(t("agent.attachTooMany", { p0: ATTACH_MAX }));
      return false;
    }
    files = [...files, { id: ++fileIds, name, mime, data, size: file.size }];
    drawFile();
    input.focus();
    return true;
  }

  // ---------- the voice ----------

  let listenTimer: ReturnType<typeof setTimeout> | undefined;
  /** Whether the app said it is listening since the voice button last sent the key. */
  let listening = false;

  /**
   * The voice button went down: the question key went down (a click is a press and a release).
   * The picture flies at once; if the app does not start listening (it is busy with something
   * else, say), it comes back down.
   */
  function voiceDown(): void {
    if (question) return;
    const starting = !voice.isUp;
    voice.up();
    if (starting) {
      listening = false;
      clearTimeout(listenTimer);
      listenTimer = setTimeout(() => {
        if (!listening) voice.down();
      }, LISTEN_WAIT_MS);
    }
    void api.agentVoice("down").catch((err) => {
      voice.down();
      toaster.info(describeError(err));
    });
  }
  const voiceUp = (): void => void api.agentVoice("up").catch(() => {});

  /** A click on the flyer: in toggle mode, a second press of the key ends the recording. */
  async function endVoice(): Promise<void> {
    if (app.device().dictation_mode === "hold") return; // the release of the button ends it
    try {
      await api.agentVoice("down");
      await api.agentVoice("up");
    } catch {
      // the hotkeys are not running: Esc or the overlay ends it
    }
  }

  // ---------- the approval card ----------

  /** `always` is the third answer: YES, and keep allowing (only a question that has the button). */
  async function answer(approved: boolean, always = false): Promise<void> {
    if (!question || answering) return;
    if (always && !question.always) return;
    answering = true;
    noButton.disabled = alwaysButton.disabled = yesButton.disabled = true;
    try {
      await (always ? api.agentQuestionAnswer(question.id, true, true) : api.agentQuestionAnswer(question.id, approved));
    } catch (err) {
      answering = false;
      noButton.disabled = alwaysButton.disabled = yesButton.disabled = false;
      toaster.info(describeError(err));
    }
  }

  let holdTimer: ReturnType<typeof setTimeout> | undefined;
  /** The YES of a T3 question: held for `HOLD_MS` it is armed, and let go on it then, it answers. */
  function holdStart(): void {
    if (question?.tier !== "T3" || answering) return;
    yesButton.dataset.hold = "holding";
    clearTimeout(holdTimer);
    holdTimer = setTimeout(() => (yesButton.dataset.hold = "armed"), HOLD_MS);
  }
  function holdEnd(onButton: boolean): void {
    if (question?.tier !== "T3") return;
    clearTimeout(holdTimer);
    const armed = yesButton.dataset.hold === "armed";
    yesButton.dataset.hold = "idle";
    if (armed && onButton) void answer(true);
  }

  // ---------- the search ----------

  function openSearch(): void {
    searching = true;
    find.hidden = false;
    searchButton.setAttribute("aria-expanded", "true");
    findInput.focus();
    render(true);
  }

  function closeSearch(draw = true): void {
    if (!searching) return;
    searching = false;
    query = "";
    found = null;
    findInput.value = "";
    find.hidden = true;
    searchButton.setAttribute("aria-expanded", "false");
    clearTimeout(searchTimer);
    if (draw) {
      render(true);
      searchButton.focus();
    }
  }

  /**
   * Looks for the words in the whole history (`agent.history.search`): the turns found show in the
   * thread under their days, however far behind the loaded ones they are. When the core cannot
   * answer this time (it is starting again, say), the loaded turns are looked through instead, and
   * the next search asks the core again.
   */
  async function runSearch(): Promise<void> {
    const mine = ++searches;
    const words = query.trim();
    found = null;
    if (words !== "") {
      try {
        const member = memberId();
        const turnsFound = (member === RAPHAEL_ID ? await api.agentHistorySearch(words) : await api.agentHistorySearch(words, member)).turns;
        if (mine !== searches) return;
        found = turnsFound;
      } catch {
        if (mine !== searches) return;
      }
    }
    render(true);
  }

  // ---------- 清除全部 ----------

  /** A first press asks; the second clears the page at once and the core when the toast has run out. */
  function clearAll(): void {
    const shown = shownTurns();
    if (shown.length === 0) return;
    if (!confirmingClear) {
      confirmingClear = true;
      drawClear();
      clearTimeout(clearTimer);
      clearTimer = setTimeout(() => {
        confirmingClear = false;
        drawClear();
      }, CLEAR_CONFIRM_MS);
      return;
    }
    clearTimeout(clearTimer);
    confirmingClear = false;
    drawClear();
    clearedUpTo = Math.max(...shown.map((turn) => turn.id));
    render();
    app.undo.begin({
      message: t("agent.cleared"),
      commit: async () => {
        await api.agentHistoryClear();
        forgetTurns();
      },
      restore: () => {
        clearedUpTo = 0;
        render(true);
      },
      failure: (err) => t("undo.001", { p0: () => describeError(err) }),
    });
  }

  /** The conversation is gone in the core: the page drops every turn it loaded and shows the empty page. */
  function forgetTurns(): void {
    turns = [];
    state.stopping.clear();
    forgetReruns(state, turns);
    clearedUpTo = 0;
    canLoadEarlier = false;
    newestDrawn = 0;
    if (visible) render();
  }

  // Settings › 資料 emptied the conversation while the page had it loaded (under the panel, or
  // before it was left): a load already under way brings nothing back, and nothing waits to be cleared.
  app.cleared.on("conversation", () => {
    loads++;
    pending = false;
    loadingEarlier = false;
    failure = null;
    clearTimeout(clearTimer);
    confirmingClear = false;
    drawClear();
    forgetTurns();
  });

  // ---------- what a turn shows ----------

  /** Opens or closes what a turn did, where it is: the box slides, nothing else is drawn again. */
  function toggleSteps(button: HTMLElement): void {
    const id = Number(button.closest<HTMLElement>(".tn")?.dataset.id);
    // A turn the search found may be one that is not loaded.
    const turn = turns.find((candidate) => candidate.id === id) ?? found?.find((candidate) => candidate.id === id);
    const box = button.closest<HTMLElement>(".steps");
    const steps = box?.querySelector<HTMLElement>(".steps-body");
    if (!turn || !box || !steps) return;
    const open = !stepsOpen(turn, state);
    state.open.set(id, open);
    box.dataset.open = String(open);
    button.setAttribute("aria-expanded", String(open));
    steps.toggleAttribute("inert", !open);
    rememberLine(button);
  }

  /** Opens or closes the messages of an exchange between members, under its line. */
  function toggleExchange(button: HTMLElement): void {
    const id = Number(button.closest<HTMLElement>(".tn-x, .tn")?.dataset.id);
    if (openExchanges.has(id)) openExchanges.delete(id);
    else openExchanges.add(id);
    rememberLine(button);
    render();
  }

  /**
   * The questions from the background that wait in the open thread (background-mount.ts): the gold
   * card is Raphael's own (his emblem, his 《問。》) in his thread and the member's (their face and
   * name, plain words) in theirs. Called when the thread changes, and it reads the questions again.
   */
  function drawBackgroundQuestions(): void {
    waiting = mountBackgroundQuestions(backgroundSlot, {
      memberId: memberId(),
      asker: () => {
        const member = members.member();
        return inRaphael() ? { face: avatarHTML({ size: 22, cls: "bgq-face" }), name: null } : { face: faceHTML(member, 22, { cls: "bgq-face" }), name: member.name };
      },
      changed: () => {
        members.changed();
        if (visible && !scroller.hidden && atBottom()) scroller.scrollTop = scroller.scrollHeight;
      },
    });
  }

  /**
   * Another member's thread: what was loaded goes, and that member's turns are read. The search
   * closes, the composer and the card become that thread's, and nothing of the last thread moves in.
   */
  function switchThread(): void {
    loads++;
    attachmentEpoch++;
    attachmentMedia.reset();
    resetDrop();
    turns = [];
    found = null;
    canLoadEarlier = false;
    loadingEarlier = false;
    pending = false;
    failure = null;
    clearedUpTo = 0;
    newestDrawn = 0;
    drewOnce = false;
    details.clear();
    openExchanges.clear();
    if (searching) closeSearch(false);
    thread.replaceChildren();
    if (voice.isUp) voice.drop();
    input.value = "";
    files = [];
    drawFile();
    fit();
    drawComposer();
    drawFaces();
    hi.textContent = emptyLine();
    // the card of another thread's question goes, and this thread's comes
    delete askCard.dataset.id;
    drawBackgroundQuestions();
    render();
    if (visible) {
      void load(false).then(() => {
        if (visible && !scroller.hidden) scroller.scrollTop = scroller.scrollHeight;
      });
      focusComposer();
    }
  }

  function toggleDetail(button: HTMLElement): void {
    const turn = button.closest<HTMLElement>(".tn");
    const detail = turn?.querySelector<HTMLElement>(".tn-detail");
    if (!turn || !detail) return;
    const id = Number(turn.dataset.id);
    detail.hidden = !detail.hidden;
    button.setAttribute("aria-expanded", String(!detail.hidden));
    if (detail.hidden) details.delete(id);
    else details.add(id);
    rememberLine(button);
  }

  function toggleThinking(button: HTMLElement): void {
    const box = button.closest<HTMLElement>(".think-box");
    const body = box?.querySelector<HTMLElement>(".think-body");
    if (!box || !body) return;
    const id = Number(box.dataset.thinkingTurn ?? button.closest<HTMLElement>(".tn")?.dataset.id);
    const next = Boolean(body.hidden);
    body.hidden = !next;
    box.classList.toggle("is-open", next);
    button.setAttribute("aria-expanded", String(next));
    if (!Number.isNaN(id)) state.thinkingOpen.set(id, next);
    rememberLine(button);
  }

  function togglePdfExpand(button: HTMLElement): void {
    const card = button.closest<HTMLElement>(".media-pdf");
    if (!card) return;
    const expanded = card.classList.toggle("is-expanded");
    button.setAttribute("aria-expanded", String(expanded));
    button.textContent = expanded ? t("agent.collapsePdf") : t("agent.expandPdf");
    rememberLine(button);
  }

  function openLightbox(button: HTMLElement, localSrc?: string): void {
    const src = localSrc ?? safeMediaSrc(button.dataset.src, true);
    if (!src) return;
    const title = button.dataset.title ?? "";
    document.querySelector(".media-lightbox")?.remove();
    const scrim = document.createElement("div");
    scrim.className = "mb-scrim media-lightbox";
    scrim.setAttribute("role", "dialog");
    scrim.setAttribute("aria-modal", "true");
    scrim.setAttribute("aria-label", title || t("agent.viewImage"));
    scrim.innerHTML =
      `<div class="lb-card">` +
      `<div class="lb-head"><span class="lb-title">${esc(title)}</span>${iconButton("x", t("common.close"), ' data-lb="close"')}</div>` +
      `<img class="lb-img" src="${esc(src)}" alt="${esc(title)}">` +
      `</div>`;
    const close = (): void => {
      scrim.remove();
      document.removeEventListener("keydown", onKey, true);
      button.focus({ preventScroll: true });
    };
    const onKey = (ev: KeyboardEvent): void => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        close();
      }
    };
    scrim.addEventListener("click", (ev) => {
      if (ev.target === scrim || (ev.target as Element).closest('[data-lb="close"]')) close();
    });
    document.addEventListener("keydown", onKey, true);
    document.body.append(scrim);
    scrim.querySelector<HTMLElement>('[data-lb="close"]')?.focus();
  }

  /** A line changed in place by the reader is drawn from new data next time, so it is not kept as it was. */
  function rememberLine(control: HTMLElement): void {
    const line = control.closest<HTMLElement>("[data-key]");
    if (line) drawnHTML.delete(line);
  }

  async function rerun(button: HTMLElement): Promise<void> {
    const asked = requestRerun(state, Number(button.dataset.task), api.workerRerun);
    render(); // the button is off
    const outcome = await asked;
    if (outcome.error !== undefined) toaster.info(describeError(outcome.error));
    if (visible) void load(false);
  }

  async function stop(button: HTMLElement): Promise<void> {
    const taskId = Number(button.dataset.task);
    const asked = requestStop(state, taskId, api.workerCancel);
    render(); // the button says it is stopping
    const outcome = await asked;
    if (outcome.error !== undefined) toaster.info(describeError(outcome.error));
    if (visible) void load(false);
  }

  function messageSource(control: HTMLElement): { turn: AgentTurn; key: string; text: string; markdown: boolean } | null {
    const id = Number(control.closest<HTMLElement>(".tn[data-id]")?.dataset.id);
    const turn = turns.find(item => item.id === id) ?? found?.find(item => item.id === id);
    const key = control.closest<HTMLElement>("[data-message]")?.dataset.message;
    if (!turn || !key) return null;
    if (key === "question") return { turn, key, text: turn.question, markdown: false };
    const message = turn.messages?.find(item => String(item.seq) === key);
    if (key !== "reply" && !message) return null;
    if (message?.kind === "media") {
      const media = parseMediaPayload(message.text);
      return { turn, key, text: media ? [media.title, media.description, media.url].filter(Boolean).join("\n") : message.text, markdown: false };
    }
    const markdown = key === "reply" || message?.kind === "reply";
    const text = message?.text ?? turn.text;
    return { turn, key, text: markdown && inRaphael() && !text.startsWith("《") ? replyLine(turn.prefix, text) : text, markdown };
  }

  async function copyMessage(control: HTMLElement): Promise<void> {
    const source = messageSource(control);
    if (!source) return;
    try { await copyText(messagePlainText(source.text, source.markdown)); }
    catch (err) { toaster.info(describeError(err)); }
  }

  async function regenerate(control: HTMLElement): Promise<void> {
    const source = messageSource(control);
    if (!source || look().regenerateTurnId !== source.turn.id) return;
    const member = memberId(), turn = source.turn;
    regenerating.add(turn.id);
    const mine: Outgoing = { key: `out:${++sent}`, member, text: turn.question, sentAt: Date.now(), files: [] };
    outgoing.push(mine); render(true);
    askingMember = member;
    try {
      if (member === RAPHAEL_ID) await api.agentAskText(turn.question);
      else await api.agentAskText(turn.question, [], member);
      if (visible && memberId() === member) await load(false);
      else members.changed();
    } catch (err) { toaster.info(describeError(err)); }
    finally {
      if (askingMember === member) askingMember = null;
      regenerating.delete(turn.id);
      outgoing = outgoing.filter(item => item !== mine);
      if (visible) render();
    }
  }

  // ---------- input ----------

  el.addEventListener("auxclick", (e) => {
    const link = (e.target as Element).closest<HTMLElement>('[data-act="open-url"]');
    if (!link || e.button !== 1) return;
    e.preventDefault();
    const url = safeHttpUrl(link.dataset.url);
    if (url) void api.openExternalUrl(url).catch(err => toaster.info(describeError(err)));
  });

  el.addEventListener("click", (e) => {
    const act = (e.target as Element).closest<HTMLElement>("[data-act]");
    if (!act || (act as HTMLButtonElement).disabled) return;
    switch (act.dataset.act) {
      case "clear":
        clearAll();
        break;
      case "settings":
        openRaphaelSettings(app);
        break;
      case "search":
        if (searching) closeSearch();
        else openSearch();
        break;
      case "search-close":
        closeSearch();
        break;
      case "steps":
        toggleSteps(act);
        break;
      case "detail":
        toggleDetail(act);
        break;
      case "thinking":
        toggleThinking(act);
        break;
      case "open-url": {
        e.preventDefault();
        const url = safeHttpUrl(act.dataset.url);
        if (url) void api.openExternalUrl(url).catch(err => toaster.info(describeError(err)));
        break;
      }
      case "copy-message":
        void copyMessage(act);
        break;
      case "copy-code": {
        const code = act.closest(".message-code")?.querySelector("code")?.textContent;
        if (code !== undefined && code !== null) void copyText(code).catch(err => toaster.info(describeError(err)));
        break;
      }
      case "regenerate":
        void regenerate(act);
        break;
      case "expand-message": {
        const source = messageSource(act);
        if (!source) break;
        const key = `${source.turn.id}:${source.key}`;
        if (state.expandedMessages.has(key)) state.expandedMessages.delete(key);
        else state.expandedMessages.add(key);
        render();
        break;
      }
      case "attachment-image": {
        const src = attachmentMedia.source(act);
        if (src) openLightbox(act, src);
        break;
      }
      case "attachment-open":
        void api.openAttachment(act.dataset.id ?? "").catch(() => toaster.info(t("agent.fileUnavailable")));
        break;
      case "lightbox":
        openLightbox(act);
        break;
      case "pdf-expand":
        togglePdfExpand(act);
        break;
      case "exchange":
        toggleExchange(act);
        break;
      case "stop":
        void stop(act);
        break;
      case "rerun":
        void rerun(act);
        break;
      case "attach":
        picker.value = "";
        picker.click();
        break;
      case "unattach-file": {
        const id = Number(act.dataset.file);
        files = files.filter((file) => file.id !== id);
        drawFile();
        input.focus();
        break;
      }
      case "no":
        void answer(false);
        break;
      case "always":
        void answer(true, true);
        break;
      case "yes":
        if (question?.tier !== "T3") void answer(true);
        break;
    }
  });

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    void send();
  });
  input.addEventListener("keydown", (e) => {
    if (!sendsOnEnter(e)) return;
    e.preventDefault();
    void send();
  });
  input.addEventListener("input", fit);
  picker.addEventListener("change", () => {
    const chosen = Array.from(picker.files ?? []);
    if (chosen.length > 0) void attachAll(chosen);
  });
  // Clipboard files are attached; any accompanying plain text stays in the composer.
  input.addEventListener("paste", (e) => {
    const pasted = Array.from(e.clipboardData?.files ?? []);
    if (pasted.length === 0) return;
    const words = e.clipboardData?.getData("text/plain") ?? "";
    e.preventDefault();
    if (words) {
      input.setRangeText(words, input.selectionStart ?? input.value.length, input.selectionEnd ?? input.value.length, "end");
      fit();
    }
    void attachAll(pasted);
  });

  // The voice button is the question key: down when it is pressed, up when it is let go.
  voiceButton.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    voiceButton.setPointerCapture(e.pointerId);
    voiceDown();
  });
  voiceButton.addEventListener("pointerup", voiceUp);
  voiceButton.addEventListener("pointercancel", voiceUp);
  // A key held on the button is let go wherever the focus is by then: the release is heard on the
  // whole page, for the key that pressed it, once; a window that loses the focus lets it go too.
  // (The button stays where it is while the picture is up, but the focus may still move: the
  // approval card can take the composer's place.)
  let heldKey: string | null = null;
  const releaseKey = (): void => {
    heldKey = null;
    voiceUp();
  };
  voiceButton.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && !e.repeat) {
      e.preventDefault();
      if (heldKey !== null) return;
      heldKey = e.key;
      voiceDown();
    }
  });
  document.addEventListener("keyup", (e) => {
    if (heldKey !== null && e.key === heldKey) releaseKey();
  });
  window.addEventListener("blur", () => {
    if (heldKey !== null) releaseKey();
  });

  yesButton.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    yesButton.setPointerCapture(e.pointerId);
    holdStart();
  });
  yesButton.addEventListener("pointerup", (e) => {
    const box = yesButton.getBoundingClientRect();
    holdEnd(e.clientX >= box.left && e.clientX <= box.right && e.clientY >= box.top && e.clientY <= box.bottom);
  });
  yesButton.addEventListener("pointercancel", () => holdEnd(false));
  yesButton.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && !e.repeat && question?.tier === "T3") {
      e.preventDefault();
      holdStart();
    }
  });
  yesButton.addEventListener("keyup", (e) => {
    if (e.key === "Enter" || e.key === " ") holdEnd(true);
  });

  findInput.addEventListener("input", () => {
    query = findInput.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => void runSearch(), SEARCH_DELAY_MS);
  });
  findInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeSearch();
    }
  });

  scroller.addEventListener("scroll", () => {
    if (scroller.scrollTop < TOP_SLACK_PX && canLoadEarlier && !loadingEarlier && !(searching && query.trim() !== "")) void load(true);
  });

  app.onEvent((event) => {
    members.follow(event);
    if (wantsRefresh(event)) {
      // a turn of another thread changes only that member's row
      if (event.type !== "AgentTurn" || (event.member_id ?? RAPHAEL_ID) === memberId()) scheduleRefresh();
      if (visible) members.changed();
    }
    waiting?.follow(event);
    if (event.type === "AgentQuestion") {
      setQuestion(openQuestion(event));
      drawDock();
      if (visible) members.changed();
    } else if (event.type === "AgentQuestionClosed") {
      if (question?.id === event.id) {
        setQuestion(null);
        drawDock();
      }
      if (visible) members.changed();
    } else if (event.type === "VoiceLevel") {
      voice.level(event.value);
    } else if (event.type === "Dictation" && event.agent) {
      if (event.phase === "listening") {
        listening = true;
        if (visible && !question && inRaphael()) voice.up();
      } else {
        listening = false;
        voice.down();
        if (event.phase === "processing") {
          startHearing();
          if (visible) render(true);
        } else if (hearing) {
          stopHearing();
          if (visible) render();
        }
      }
    }
  });

  onAvatarChange(() => {
    drawFaces();
    void members.load();
    for (const child of thread.children) drawnHTML.delete(child); // every face is drawn again
    if (visible) render();
  });

  onLocaleChange(() => {
    drawWords();
    drawComposer();
    for (const child of thread.children) drawnHTML.delete(child);
    render();
  });
  drawWords();
  drawFaces();
  drawComposer();
  drawBackgroundQuestions();

  return {
    id: "agent",
    el,
    show(arg) {
      const wasVisible = visible;
      visible = true;
      messageResize?.observe(scroller);
      dropResize?.observe(stage);
      dropResize?.observe(composer);
      dropResize?.observe(askCard);
      void members.load();
      // A draft goes into the field at once; the composer may then move (the empty page has it
      // under Raphael, the thread under the turns) or give way to a question, which takes the
      // focus away: it is given back once each of those is known.
      const draft = draftText(arg);
      if (draft !== null) putDraft(draft);
      const refocus = (): void => {
        if (draft !== null) focusComposer();
      };
      // The page opens at the newest turn, and with the question that waits, if one does.
      void api
        .agentQuestionOpen()
        .then((open) => {
          setQuestion(open && openQuestion(open));
          drawDock();
          refocus();
        })
        .catch(() => {});
      void load(false).then(() => {
        if (visible && !scroller.hidden && (!wasVisible || draft !== null)) scroller.scrollTop = scroller.scrollHeight;
        refocus();
      });
      drawBackgroundQuestions();
    },
    hide() {
      visible = false;
      resetDrop();
      attachmentMedia.reset();
      previews.pause();
      messageResize?.disconnect();
      dropResize?.disconnect();
      members.hide();
      clearTimeout(clearTimer);
      clearTimeout(holdTimer);
      clearTimeout(listenTimer);
      confirmingClear = false;
      drawClear();
      loads++;
      loadingEarlier = false;
      stopTicker();
      voice.drop();
      if (refreshTimer !== null) clearTimeout(refreshTimer);
      refreshTimer = null;
    },
  };
}
