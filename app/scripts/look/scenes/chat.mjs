import { scene, click, hover, type, wait, member, room, search, check, key, waitScrollEnd } from "./steps.mjs";

const details = [member(1), { action: "open-details" }, check("member-details")];
const composer = [click(".cmp-attach"), wait("#app-popover")];
export default [
  scene("chat", "Conversation with public progress, replies and an attachment", [wait(".ra .chat-message[data-expandable]"), check("conversation-design")]),
  scene("running", "Conversation while a reply is being prepared", [], { running: true }),
  scene("admin-approval", "Raphael asks before creating a member", [wait('.ask-card:not([hidden])'), check("admin-approval")], { adminApproval: "create" }),
  scene("admin-approval-batch", "One approval lists eight members and the new group", [wait('.ask-card:not([hidden])'), click('.ask-q'), key("End"), waitScrollEnd('.ask-q'), check("admin-approval-batch")], { adminApproval: "batch" }),
  scene("admin-approval-long", "Complete long instructions remain readable in the approval card", [wait('.ask-card:not([hidden])'), click('.ask-q'), key("End"), waitScrollEnd('.ask-q'), check("admin-approval-long")], { adminApproval: "long" }),
  scene("admin-delete-approval", "Deleting a member always requires held approval", [wait('.ask-card[data-tier="T3"]:not([hidden])'), check("admin-approval")], { adminApproval: "delete" }),
  scene("admin-approved", "Approve creation and select the live member", [wait('.ask-card:not([hidden])'), click('.ask-yes'), member(8), { action: "open-details" }, wait('.mb-det:not([hidden])'), check("admin-created")], { adminApproval: "create" }),
  scene("admin-denied", "Declining creation leaves the team unchanged", [wait('.ask-card:not([hidden])'), click('.ask-no'), wait('.composer:not([hidden])'), check("admin-denied")], { adminApproval: "create" }),
  scene("member-draft", "Editable member draft cards in the conversation", [wait('.draft-panel:not([hidden])'), check("member-draft")], { adminApproval: "draft" }),
  scene("member-draft-edit", "Owner edits a member draft and removes another before confirming", [wait('.draft-panel:not([hidden])'), type('[data-draft-id="draft-1"] [data-draft-field="role"]', "首席營運長"), click('[data-draft-id="draft-3"] [data-draft-act="remove"]'), check("member-draft-edit")], { adminApproval: "draft" }),
  scene("member-draft-done", "Confirmed drafts settle into a summary and add members to the column", [wait('.draft-panel:not([hidden])'), click('[data-draft-act="confirm"]'), wait('.draft-done'), click('[data-act="draft-done"]'), wait('.draft-done.is-open'), check("member-draft-done")], { adminApproval: "draft" }),
  scene("empty", "Conversation without turns", [], { turns: [] }),
  scene("row-menu", "Conversation actions in the details panel", [...details, click(".mb-det [data-conversation-more]"), wait("#app-popover [data-pick]")]),
  ...["rename", "delete"].map(action => scene(action, `Conversation ${action} dialog`, [...details, click(".mb-det [data-conversation-more]"), click(`[data-pick="${action}"]`), wait(".mb-dialog")])),
  scene("new-conversation", "Create a conversation", [...details, click(".mb-det [data-conversation-new]"), wait(".page-agent:not([hidden]) .ra-empty:not([hidden])")]),
  scene("details", "Title opens member details", [...details, { action: "icon-interactions", selector: '.mb-det [data-det="close"]' }]),
  scene("raphael-details", "Title opens Raphael details", [{ action: "open-details" }, check("member-details")]),
  scene("details-closed", "Title closes details", [...details, click(".conversation-trigger"), wait('.conversation-trigger[aria-expanded="false"]')]),
  scene("conversation-filter", "Search conversations in details", [...details, type(".mb-det .conversation-tools input", "no matching conversation"), wait(".mb-det .conversation-empty")]),
  ...["routines", "memory", "skills", "commands", "voice"].map(tab => scene(`details-${tab}`, `Member ${tab} pane`, [...details, click(`[data-det="tab"][data-tab="${tab}"]`)])),
  scene("member-avatar", "Member avatar picker above details", [...details, click('[data-det="avatar"]'), wait("#app-popover")]),
  scene("member-edit", "Edit member dialog", [...details, click('[data-det="edit"]'), wait(".mb-dialog")]),
  scene("work", "Expanded public work steps", [click('[data-act="thinking"]'), wait(".work-body:not([hidden])")]),
  scene("composer", "Composer attachment menu", composer),
  scene("mode", "Composer action mode submenu", [...composer, click('[data-action="1"]'), wait("#app-submenu, #app-popover [data-value]")]),
  scene("actions", "Answer actions", [hover(".ra .chat-message[data-expandable]"), click('.ra .chat-message[data-expandable] [data-act="more-message"]'), wait("#app-popover")]),
  scene("reply", "Quote a reply into the composer", [hover(".ra .chat-message[data-expandable]"), click('.ra .chat-message[data-expandable] [data-act="reply-message"]')]),
  scene("search", "Search conversation messages", [search("研究"), wait("[data-message-result]")]),
  scene("search-target", "Navigate to a message search match", [search("研究"), click("[data-message-result]"), wait(".is-search-target")]),
  scene("tooltip", "Answer timestamp tooltip", [hover(".ra .chat-message[data-expandable] .message-actions time")]),
  scene("room", "Room conversation", [room(1), wait(".au-room-thread .chat-message")]),
  ...["member", "room", "team"].flatMap(context => {
    const base = context === "member" ? [member(1)] : context === "room" ? [room(1)] : [click('[data-go="team"]')];
    const form = context === "member" ? ".mb-chat:not(.au-room-pane) .composer" : context === "room" ? ".au-room-pane .composer" : ".team-composer .composer";
    return [scene(`${context}-composer-empty`, `${context} empty composer`, [...base, check("composer-pair")], { ...(context === "room" ? { messages: [] } : {}), turns: [] }),
      scene(`${context}-text`, `${context} composer draft`, [...base, type(`${form} textarea`, "請整理今天的研究進度。"), check("composer-pair")]),
      scene(`${context}-composer-hover`, `${context} composer send hover`, [...base, type(`${form} textarea`, "請整理今天的研究進度。"), hover(`${form} .cmp-send`), check("composer-pair")])];
  }),
];
