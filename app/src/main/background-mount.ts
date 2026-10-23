// Where the questions of the background wait in a thread of the 拉斐爾 page: a task (or a routine's
// run) of a member that needs the owner's yes pauses, and its gold card shows in that member's
// thread, above the composer, until it is answered there (never from Telegram).
//
// The card itself is another branch's (`createBackgroundQuestions({memberId, changed})`, with
// `background.questions.list {member_id}`), a self-contained component. This is the one place the
// page puts it: `mountBackgroundQuestions` is called for the open thread each time it changes
// (pages/agent.ts), with the slot above the composer, and the merge makes it mount that component
// there. Until then the slot stays empty, and the page is as it was.

export interface BackgroundQuestionsMount {
  /** The member whose thread is open: 0 is Raphael. */
  memberId: number;
  /** A question was answered or went: the member column reads its 「等你處理」 again. */
  changed(): void;
}

/** Puts the waiting questions of the open thread in `slot` (above the composer), and takes the last thread's away. */
export function mountBackgroundQuestions(slot: HTMLElement, o: BackgroundQuestionsMount): void {
  // The merge with the background questions: slot.replaceChildren(createBackgroundQuestions(o).el)
  void o;
  slot.replaceChildren();
}
