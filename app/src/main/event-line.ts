// A system event in a conversation is one centred grey line, as colleagues' chats show who was asked,
// who a task went to or which meeting opened: the window language's words with a small face or icon
// before the name. The words keep each language's order: `NAME` stands where the name goes.
import { esc } from "./ui";

/** Pass as the name's value to `t()`; `eventLineHTML` puts the face and the name in its place. */
export const NAME = "\u0000";

/** The line itself: `words` from `t(key, { p0: NAME })`, `lead` a face or an icon, `state` quieter at the end. */
export function eventLineHTML(words: string, lead: string, name: string, state = ""): string {
  const [before = "", after = ""] = words.split(NAME);
  return `<span class="sys event-line">` +
    (before.trim() ? `<span>${esc(before)}</span>` : "") + lead + `<b>${esc(name)}</b>` +
    (after.trim() ? `<span>${esc(after)}</span>` : "") +
    (state ? `<span class="event-state">${esc(state)}</span>` : "") + `</span>`;
}
