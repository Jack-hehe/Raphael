import { scene, click, wait, check, scroll } from "./steps.mjs";

const ready = [wait(".page-home:not([hidden]) .ho-raphael"), check("home-overview")];
export default [
  scene("home", "Home briefing with reports, pending items, calendar and usage", ready),
  scene("home-empty", "Home without pending work, reports, events or usage", ready, { homeEmpty: true }),
  scene("home-720", "Home responsive layout and lower dictation strip", [...ready, scroll(".ho-dictation"), check("home-overview")]),
  scene("home-report", "A member report opens its conversation at the reported turn", [...ready, click('.ho-row[data-home-item="report:2"]'), wait('.page-agent:not([hidden]) .tn[data-id="2"]'), check("home-report")]),
  scene("home-routine", "A scheduled routine opens automations", [...ready, click('.ho-row[data-home-item="routine:1"]'), wait('.au-page:not([hidden])')]),
  scene("home-expand", "Expand the overview to reveal all returned pending items", [...ready, click('[data-home-expand="today"]'), wait('[data-home-expand="today"][aria-expanded="true"]'), check("home-expanded")], { homeManyQuestions: true }),
];
