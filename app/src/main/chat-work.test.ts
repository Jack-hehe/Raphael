// @vitest-environment happy-dom
import { expect, test } from "vitest";
import { turnHTML, emptyTimelineState } from "./timeline";
import { setLocale } from "./i18n";

test("completed work is one closed section above the answer; opening retains public progress and events", () => {
  setLocale("en");
  const state = emptyTimelineState();
  const turn = { id: 1, created_at_ms: 1000, question: "Question", prefix: "", text: "Done", detail: null, route: "answer", status: "done", messages: [
    { seq: 1, kind: "thinking" as const, text: "Checking sources", at_ms: 2000 },
    { seq: 2, kind: "progress" as const, text: "Found the report", at_ms: 3000 },
    { seq: 3, kind: "event" as const, text: "Read source", at_ms: 4000 },
    { seq: 4, kind: "reply" as const, text: "Done", at_ms: 27000 },
  ] };
  const host = document.createElement("div"); host.innerHTML = turnHTML(turn, state, false, { face: () => "" });
  expect(host.querySelector(".work-toggle")?.textContent).toContain("Worked for 26s");
  expect(host.querySelector(".work-body")?.hasAttribute("hidden")).toBe(true);
  expect(host.querySelector(".work-body")?.textContent).toContain("Found the report");
  expect(host.querySelectorAll(".work-section")).toHaveLength(1);
  state.thinkingOpen.set(1, true);
  host.innerHTML = turnHTML(turn, state, false, { face: () => "" });
  expect(host.querySelector(".work-body")?.hasAttribute("hidden")).toBe(false);
  expect(host.querySelector('[data-message="4"] .message-actions [data-act="reply-message"]')).not.toBeNull();
  expect(host.querySelector('[data-message="4"] time')?.hasAttribute("data-tooltip")).toBe(true);
});

test("running work keeps the latest public update, ticking time and dots in one closed row", () => {
  setLocale("en");
  const state = emptyTimelineState(); state.nowMs = 5000;
  const turn = { id: 2, created_at_ms: 1000, question: "Question", prefix: "", text: "", detail: null, route: "answer", status: "running", messages: [
    { seq: 1, kind: "progress" as const, text: "Reading sources", at_ms: 2000 },
    { seq: 2, kind: "progress" as const, text: "Comparing findings", at_ms: 4000 },
  ] };
  const host = document.createElement("div"); host.innerHTML = turnHTML(turn, state, false, { face: () => "" });
  expect(host.querySelector(".work-latest")?.textContent).toBe("Comparing findings");
  expect(host.querySelector(".work-time")?.textContent).toContain("4");
  expect(host.querySelectorAll(".think")).toHaveLength(1);
  expect(host.querySelectorAll(".work-toggle .think i")).toHaveLength(3);
  expect(host.querySelector(".work-body")?.hasAttribute("hidden")).toBe(true);
});
