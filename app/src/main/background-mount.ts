// Where the questions of the background wait in a thread of the 拉斐爾 page: a task (or a routine's
// run) of a member that needs the owner's yes pauses, and its gold card shows in that member's
// thread, above the composer, until it is answered there (never from Telegram).
//
// The card itself is a self-contained component (background-questions.ts,
// `createBackgroundQuestions`, which reads `background.questions.list {member_id}`). This is the one
// place the page puts it: `mountBackgroundQuestions` is called for the open thread each time it
// changes (pages/agent.ts), with the slot above the composer. The slot holds one component for as
// long as the page lives; when the thread becomes another member's, the cards of the last one go
// at once and the new thread's are read. Raphael's thread (member 0) is the only one with his
// 《問。》 line and bare emblem; a member's shows that member's face and name (`asker`).

import { type Asker, type BackgroundQuestions, createBackgroundQuestions } from "./background-questions";

export interface BackgroundQuestionsMount {
  /** The member whose thread is open: 0 is Raphael. */
  memberId: number;
  /** Who asks in that thread: Raphael's emblem (name null) or the member's face and name. */
  asker(): Asker;
  /** A question was answered, came or went: the member column reads its 「等你處理」 again. */
  changed(): void;
}

interface Mounted {
  questions: BackgroundQuestions;
  current: BackgroundQuestionsMount;
}

const mounted = new WeakMap<HTMLElement, Mounted>();

/**
 * Puts the waiting questions of the open thread in `slot` (above the composer), and takes the last
 * thread's away. Returns the component, so that the page can hand it the events of the window
 * (`follow`). Called again for the same thread it only reads the questions again.
 */
export function mountBackgroundQuestions(slot: HTMLElement, o: BackgroundQuestionsMount): BackgroundQuestions {
  let entry = mounted.get(slot);
  if (!entry) {
    const made: Mounted = {
      current: o,
      // The host reads the page's latest thread each time, so one component serves every thread.
      questions: createBackgroundQuestions({
        memberId: () => made.current.memberId,
        asker: () => made.current.asker(),
        changed: () => made.current.changed(),
      }),
    };
    entry = made;
    mounted.set(slot, entry);
    slot.replaceChildren(entry.questions.element);
  } else if (entry.current.memberId !== o.memberId) {
    entry.current = o;
    entry.questions.reset();
  } else {
    entry.current = o;
    entry.questions.redraw();
  }
  void entry.questions.load();
  return entry.questions;
}
