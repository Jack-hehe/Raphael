import { scene, click, hover, type, wait, member, room, search, check } from "./steps.mjs";

const picker = [click(".conversation-trigger"), wait("#app-popover [data-conversation-open]"), check("conversation-picker")];
const details = [member(1), member(1), wait(".mb-det:not([hidden])")];
const composer = [click(".cmp-attach"), wait("#app-popover")];
export default [
  scene("chat", "Conversation with public progress, replies and an attachment", [wait(".ra .chat-message[data-expandable]")]),
  scene("running", "Conversation while a reply is being prepared", [], { running: true }),
  scene("empty", "Conversation without turns", [], { turns: [] }),
  scene("picker", "Conversation picker", picker),
  scene("no-conversations", "Conversation picker search without matches", [...picker, type("#app-popover .conversation-tools input", "沒有這個對話"), wait("#app-popover .conversation-empty"), check("conversation-picker")]),
  scene("row-menu", "Conversation actions in the details panel", [...details, click(".mb-det [data-conversation-more]"), wait("#app-popover [data-pick]")]),
  ...["rename", "delete"].map(action => scene(action, `Conversation ${action} dialog`, [...details, click(".mb-det [data-conversation-more]"), click(`[data-pick="${action}"]`), wait(".mb-dialog")])),
  scene("new-conversation", "Create a conversation", [...picker, click("#app-popover [data-conversation-new]"), wait(".page-agent:not([hidden]) .ra-empty:not([hidden])")]),
  scene("details", "Member details", details),
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
    return [scene(`${context}-composer-empty`, `${context} empty composer`, base, { ...(context === "room" ? { messages: [] } : {}), turns: [] }),
      scene(`${context}-text`, `${context} composer draft`, [...base, type(`${form} textarea`, "請整理今天的研究進度。")]),
      scene(`${context}-composer-hover`, `${context} composer send hover`, [...base, type(`${form} textarea`, "請整理今天的研究進度。"), hover(`${form} .cmp-send`)])];
  }),
];
