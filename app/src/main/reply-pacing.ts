import type { AgentTurn, TurnMessage } from "./types";

interface Reveal {
  session: string | undefined;
  replies: TurnMessage[];
  through: number;
  timer?: ReturnType<typeof setTimeout>;
}

/** Presentation only: full history remains intact while fresh reply bursts are revealed. */
export class ReplyPacing {
  private readonly reveals = new Map<number, Reveal>();

  constructor(private readonly changed: () => void, private readonly reduced: () => boolean) {}

  get active(): boolean { return this.reveals.size > 0; }
  waiting(id: number): boolean { return this.reveals.has(id); }

  observe(previous: readonly AgentTurn[], next: readonly AgentTurn[], live: boolean, pending: ReadonlySet<string> = new Set()): void {
    if (!live || this.reduced()) { this.flush(); return; }
    for (const turn of next) {
      const replies = (turn.messages ?? []).filter(message => message.kind === "reply");
      const current = this.reveals.get(turn.id);
      if (current?.session === turn.session_id && current) { current.replies = replies; continue; }
      if (current) { clearTimeout(current.timer); this.reveals.delete(turn.id); }
      const old = previous.find(item => item.id === turn.id);
      if (old?.status !== "running" && !pending.has(turn.session_id ?? "")) continue;
      const seen = new Set(old?.messages?.filter(message => message.kind === "reply").map(message => message.seq));
      const fresh = replies.filter(message => !seen.has(message.seq));
      const last = fresh[fresh.length - 1];
      if (fresh.length < 2 || !last || Date.now() - last.at_ms > 5000 || last.at_ms > Date.now() + 1000) continue;
      const reveal: Reveal = { session: turn.session_id, replies, through: fresh[0].seq };
      this.reveals.set(turn.id, reveal);
      this.schedule(turn.id, reveal);
    }
  }

  view(turn: AgentTurn): AgentTurn {
    if (this.reduced()) this.flush();
    const reveal = this.reveals.get(turn.id);
    if (!reveal) return turn;
    return { ...turn, messages: turn.messages?.filter(message => message.kind !== "reply" || message.seq <= reveal.through) };
  }

  flush(): void {
    for (const reveal of this.reveals.values()) clearTimeout(reveal.timer);
    this.reveals.clear();
  }

  private schedule(id: number, reveal: Reveal): void {
    const next = reveal.replies.find(message => message.seq > reveal.through);
    if (!next) { this.reveals.delete(id); return; }
    const delay = Math.min(2200, 450 + 18 * Array.from(next.text).length);
    reveal.timer = setTimeout(() => {
      if (this.reduced()) this.flush();
      else {
        reveal.through = next.seq;
        this.schedule(id, reveal);
      }
      this.changed();
    }, delay);
  }
}
