// What the window says to its pages when a whole store of the core was emptied from somewhere other
// than the page that shows it: Settings › 資料 clears the dictation history or the conversation with
// 拉斐爾, and the page that already loaded them must drop what it holds, or the deleted items would
// still be on screen, and could still be acted on, until the page loaded again.

/** What Settings › 資料 can empty: the dictation history, or the conversation with 拉斐爾. */
export type ClearedData = "dictation" | "conversation";

/**
 * One signal for the whole window (`AppContext.cleared`). A page listens for what it shows with
 * `on`; whoever emptied it says so with `say`, only once the core has done it.
 */
export class Cleared {
  private readonly handlers = new Set<{ what: ClearedData; forget: () => void }>();

  /** Calls `forget` each time `what` was emptied. Returns how to stop listening. */
  on(what: ClearedData, forget: () => void): () => void {
    const handler = { what, forget };
    this.handlers.add(handler);
    return () => void this.handlers.delete(handler);
  }

  /** `what` was emptied in the core: every page that shows it forgets it. One that fails does not stop the rest. */
  say(what: ClearedData): void {
    for (const handler of [...this.handlers]) {
      if (handler.what !== what) continue;
      try {
        handler.forget();
      } catch (err) {
        console.warn("cleared: a page could not forget what was cleared", err);
      }
    }
  }
}
