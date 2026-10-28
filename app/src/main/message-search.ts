// Cross-conversation search is kept outside the member column so its grouping stays independent.
import * as api from "./api";
import { t } from "./i18n";
import { conversationTitle, relativeTime } from "./conversation-list";
import { esc } from "./ui";
import { describeError } from "./format";

interface Hit {
  id: number; member_id: number; conversation_id: number; conversation_title: string;
  question: string; text: string; created_at_ms: number;
}

function snippet(hit: Hit, query: string): string {
  const text = [hit.question, hit.text].find(text => text.toLocaleLowerCase().includes(query.toLocaleLowerCase())) ?? hit.question;
  const index = text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase());
  const start = Math.max(0, index - 24), end = Math.min(text.length, Math.max(index, start) + query.length + 65);
  if (index < 0) return esc(text.slice(0, 100));
  return (start ? "…" : "") + esc(text.slice(start, index)) + `<mark>${esc(text.slice(index, index + query.length))}</mark>` + esc(text.slice(index + query.length, end)) + (end < text.length ? "…" : "");
}

export function createMessageSearch(host: HTMLElement, options: { face(member: number): string; name(member: number): string; open(member: number, conversation: number, turn: number): void }): { draw(query: string, membersHTML: string): void } {
  let query = "", members = "", hits: Hit[] = [], error = "", epoch = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  function render(): void {
    if (!query) { host.innerHTML = members; return; }
    host.innerHTML = `<div class="search-group-label">${esc(t("chat.members"))}</div>${members}` +
      `<div class="search-group-label">${esc(t("chat.messages"))}</div>` +
      (error ? `<p class="mb-none">${esc(error)}</p>` : hits.length ? hits.map(hit =>
        `<button type="button" class="nav message-search-result" data-message-result="${hit.id}">${options.face(hit.member_id)}` +
        `<span class="message-search-main"><span class="message-search-title"><b>${esc(options.name(hit.member_id))}</b><span>${esc(conversationTitle({ title: hit.conversation_title } as Parameters<typeof conversationTitle>[0]))}</span></span><span class="message-search-snippet">${snippet(hit, query)}</span>` +
        `<small>${esc(relativeTime(hit.created_at_ms))}</small></span></button>`).join("") : `<p class="mb-none">${esc(t("chat.noMessages"))}</p>`);
  }
  host.addEventListener("click", event => {
    const id = (event.target as Element).closest<HTMLElement>("[data-message-result]")?.dataset.messageResult;
    const hit = hits.find(hit => String(hit.id) === id);
    if (hit) options.open(hit.member_id, hit.conversation_id, hit.id);
  });
  return { draw(next, html) {
    members = html;
    const changed = next.trim() !== query;
    query = next.trim();
    if (changed) {
      const mine = ++epoch;
      clearTimeout(timer); hits = []; error = "";
      if (query) timer = setTimeout(() => {
        void api.core<{ turns: Hit[] }>("messages.search", { query, limit: 50 }).then(result => {
          if (mine === epoch) { hits = result.turns; render(); }
        }).catch(reason => { if (mine === epoch) { error = describeError(reason); render(); } });
      }, 200);
    }
    render();
  } };
}
