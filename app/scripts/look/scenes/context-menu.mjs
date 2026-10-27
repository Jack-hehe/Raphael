import { scene, click, wait, check, scroll, member, rightClick, settings } from "./steps.mjs";

// R-UI-44: a right click anywhere on a row opens the menu of its ⋯, at the pointer. Each scene presses the real right button,
// checks that the menu is the one a click on the ⋯ opens (it closes on the way), and presses again so that the picture shows it.
const column = { action: "member-column", collapsed: false };
const rightClicked = (target, steps = []) => [...steps, rightClick(target), wait("#app-popover"), check("context-menu"), rightClick(target), wait("#app-popover")];
const details = [member(1), { action: "open-details" }];
const keyed = [{ action: "focus", selector: '.mb-col [data-member="2"]' }, { action: "key", key: "F10", shift: true }, wait("#app-popover")];

export default [
  scene("context-menu-member", "A right click on a member's row opens the menu of its ⋯ at the pointer", rightClicked('.mb-col [data-member-item="2"]', [column])),
  scene("context-menu-conversation", "A right click on a conversation in the member's details opens its rename and delete menu", rightClicked(".mb-det .conversation-row", details)),
  scene("context-menu-routine", "A right click on a routine in the member's details opens the menu of its ⋯", rightClicked(".mb-det .mb-rt", [...details, click('[data-det="tab"][data-tab="routines"]'), wait(".mb-det .mb-rt")])),
  scene("context-menu-connection", "A right click on a custom server's header opens the menu of its ⋯: test, edit and delete", rightClicked(".cs-dlg-head", [
    ...settings("connections"), click('[data-conn="open"][data-id="notes"]'), wait(".cs-dlg-head"),
  ])),
  scene("context-menu-cli-account", "A right click on a subscription account's card opens the menu of its ⋯", rightClicked('.cli-card:has([data-cli-menu="codex"])', [
    ...settings("models"), scroll(".cli-accounts"),
  ])),
  scene("context-menu-message", "A right click on Raphael's answer opens the menu of its ⋯", rightClicked(".ra .chat-message[data-expandable]")),
  scene("context-menu-key", "Shift+F10 on a focused member row opens the menu of its ⋯ under that button", [column, ...keyed, check("context-menu-key"), ...keyed]),
];
