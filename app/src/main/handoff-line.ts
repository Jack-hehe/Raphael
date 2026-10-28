import { NAME, eventLineHTML } from "./event-line";
import { t } from "./i18n";
import { messageHTML } from "./message-content";
import { quoteHTML } from "./message-quote";
import type { HandoffView, MessageQuote } from "./types";
import { esc, ico } from "./ui";

const STATES = { queued: "handoff.queued", running: "handoff.running", waiting: "handoff.waiting", done: "handoff.done", withheld: "handoff.withheld" } as const;
const REASONS = { missing_artifact: "handoff.missingArtifact", artifact_changed: "handoff.changedArtifact", work_without_action: "handoff.noAction",
  missing_terminal: "handoff.invalidFinal", dangling_mention: "handoff.invalidFinal", cancelled: "handoff.cancelled" } as const;

/**
 * A durable receipt or a quick question to a colleague is one centred event line (the colleague's
 * `face` and `name`, 「已轉交給 X」 or 「已詢問 X」 and the state) that opens on what was asked
 * (`request`, where the thread has it), the result and the files it produced. Task numbers, or the
 * colleague's record for a question with no task, stay in `data-handoff` for the code; the owner
 * never reads them.
 */
export function handoffLineHTML(task: HandoffView, face: string, request?: MessageQuote, name = task.member_name): string {
  const state = STATES[task.state as keyof typeof STATES] ?? "handoff.failed";
  const reason = REASONS[task.reason as keyof typeof REASONS];
  const files = task.evidence.filter(item => item.kind === "artifact" && item.uri).map(item => `<li>${ico("file")}<span>${esc(item.uri!.split("/").pop()!)}</span></li>`);
  return `<details class="handoff-event" data-handoff="${task.task_id ?? `x${task.work_turn_id}`}" data-state="${esc(task.state)}">` +
    `<summary>${eventLineHTML(t(task.mode === "consult" ? "handoff.asked" : "handoff.forwarded", { p0: NAME }), face, name, t(state))}</summary>` +
    `<div class="handoff-detail">` +
    (request?.text.trim() ? quoteHTML(request) : "") +
    (reason ? `<span class="handoff-reason" role="status">${esc(t(reason))}</span>` : "") +
    (task.result ? `<div class="message-text">${messageHTML(task.result, "", true)}</div>` : "") +
    (files.length ? `<ul class="handoff-files">${files.join("")}</ul>` : "") +
    `</div></details>`;
}
