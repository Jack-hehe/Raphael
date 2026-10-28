// What colleagues asked or handed a member, in that member's own chat: one centred grey line where it
// came, 「訊息來自 Y」 for one question and 「N 則訊息往來：Y」 for several in a row, which opens on each
// question and the member's answer, each with its speaker named once. The asker's chat names the
// same exchange 「已詢問 X」 (handoff-line.ts).
import { NAME, eventLineHTML } from "./event-line";
import { t } from "./i18n";
import { messageHTML } from "./message-content";
import type { TurnLook } from "./timeline";
import type { AgentTurn } from "./types";
import { esc } from "./ui";

/** The member's answer, once there is one. */
const answerOf = (turn: AgentTurn): string =>
  turn.status === "running" || turn.status === "confirm" ? "" : (turn.detail || turn.text).trim();

/** The line of `turns`, exchanges with one colleague in a row, oldest first. */
export function exchangeLineHTML(turns: readonly AgentTurn[], look: TurnLook): string {
  const first = turns[0];
  const peer = first.peer_id ?? 0;
  const name = look.memberName?.(peer) ?? "";
  const own = look.memberName?.(first.member_id ?? 0) ?? "";
  const messages = turns.flatMap((turn) => [[name, turn.question.trim()], [own, answerOf(turn)]]).filter(([, text]) => text);
  const words = turns.length > 1 ? t("exchange.count", { p0: messages.length, p1: NAME }) : t("exchange.from", { p0: NAME });
  return `<li class="tn exchange-row" data-id="${esc(first.id)}">` +
    `<details class="handoff-event exchange-event" data-exchange="${esc(first.id)}">` +
    `<summary>${eventLineHTML(words, look.memberFace?.(peer) ?? "", name)}</summary>` +
    `<div class="handoff-detail exchange-detail">` +
    messages.map(([author, text]) => `<div class="exchange-message"><b>${esc(author)}</b><div class="message-text">${messageHTML(text, "", true)}</div></div>`).join("") +
    `</div></details></li>`;
}
