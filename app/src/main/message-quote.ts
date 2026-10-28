import { t } from "./i18n";
import type { MessageQuote } from "./types";
import { esc, ico } from "./ui";

export const QUOTE_MAX_CHARS = 20_000;

/** The same bounded visual excerpt in a sent bubble and in its editable draft. */
export function quoteHTML(quote: MessageQuote): string {
  return `<span class="message-quote"><span class="quote-author">${esc(quote.author)}</span><span class="quote-text">${esc(quote.text)}</span></span>`;
}

/** A selection can quote exactly one message; toolbar/composer selections are never eligible. */
export function selectionQuote(options: {
  host: HTMLElement; thread: HTMLElement; active: () => boolean;
  author: (message: HTMLElement) => string; onQuote: (quote: MessageQuote) => void;
}): () => void {
  const { host, thread } = options;
  const pill = document.createElement("button");
  pill.type = "button";
  pill.className = "btn quote-pill";
  pill.hidden = true;
  host.append(pill);
  let selected: MessageQuote | null = null;
  const hide = (): void => { pill.hidden = true; selected = null; };
  const messageOf = (node: Node | null): HTMLElement | null =>
    (node instanceof Element ? node : node?.parentElement)?.closest<HTMLElement>(".bubble, .message-text") ?? null;
  const update = (): void => {
    const selection = document.getSelection();
    if (!options.active() || !selection || selection.isCollapsed || selection.rangeCount !== 1) { hide(); return; }
    const message = messageOf(selection.anchorNode);
    if (!message || !thread.contains(message) || messageOf(selection.focusNode) !== message) { hide(); return; }
    const text = selection.toString().trim();
    if (!text || text.length > QUOTE_MAX_CHARS) { hide(); return; }
    const range = selection.getRangeAt(0), rects = Array.from(range.getClientRects());
    const end = rects[rects.length - 1];
    if (!end) { hide(); return; }
    selected = { text, author: options.author(message) };
    pill.innerHTML = ico("copy") + `<span>${esc(t("chat.quote"))}</span>`;
    pill.hidden = false;
    const size = pill.getBoundingClientRect();
    const top = Math.min(...rects.map(rect => rect.top)) - Math.max(size.height, 32) - 8;
    if (top < 4 || end.top >= window.innerHeight) { hide(); return; }
    pill.style.top = `${top}px`;
    pill.style.left = `${Math.max(8, Math.min(window.innerWidth - Math.max(size.width, 74) - 8, end.right - Math.max(size.width, 74)))}px`;
  };
  pill.addEventListener("pointerdown", event => event.preventDefault());
  pill.addEventListener("click", () => {
    if (selected) options.onQuote(selected);
    hide();
  });
  document.addEventListener("selectionchange", update);
  thread.addEventListener("pointerup", update);
  thread.addEventListener("keyup", update);
  host.addEventListener("scroll", hide, true);
  document.addEventListener("keydown", event => { if (event.key === "Escape") hide(); });
  document.addEventListener("pointerdown", event => { if (!pill.contains(event.target as Node)) hide(); });
  return hide;
}
