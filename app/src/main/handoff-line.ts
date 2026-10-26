import { t } from "./i18n";
import { messageHTML } from "./message-content";
import { faceHTML } from "./members";
import type { HandoffView } from "./types";
import { esc } from "./ui";

/** Durable receipts render as compact events, with work details available on demand. */
export function handoffLineHTML(task: HandoffView): string {
  const label = task.mode === "consult" ? "handoff.asked" : "handoff.forwarded";
  const state = task.state === "queued" ? "handoff.queued" : task.state === "running" ? "handoff.running"
    : task.state === "waiting" ? "handoff.waiting" : task.state === "done" ? "handoff.done"
    : task.state === "withheld" ? "handoff.withheld" : "handoff.failed";
  const reasons={missing_artifact:"handoff.missingArtifact",artifact_changed:"handoff.changedArtifact",work_without_action:"handoff.noAction",missing_terminal:"handoff.invalidFinal",dangling_mention:"handoff.invalidFinal",cancelled:"handoff.cancelled"} as const;
  const reason=task.reason && reasons[task.reason as keyof typeof reasons] ? `<span role="status">${esc(t(reasons[task.reason as keyof typeof reasons],{}))}</span>` : "";
  return `<details class="handoff-event" data-handoff="${task.task_id}" data-state="${esc(task.state)}"><summary><span class="handoff-event-line">${faceHTML({ id: task.member_id, name: task.member_name }, 20)}<span>${esc(t(label, { p0: task.member_name }))}</span><span class="handoff-state">${esc(t(state))}</span></span></summary><div class="handoff-detail"><span>${esc(t("handoff.task", { p0: String(task.task_id) }))}</span>${reason}${task.result ? `<div class="message-text">${messageHTML(task.result, "", true)}</div>` : ""}</div></details>`;
}
