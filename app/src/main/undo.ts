// Things that wait before they happen, and the little timing helper the pages use.

import { describeError } from "./format";

/** How long a deleted item can be got back (spec §12.1, point 4). */
export const UNDO_MS = 10_000;

/** Where the message goes: a toast at the bottom of the window. */
export interface Notifier {
  /** Shows `message`; with `undo` the toast has a 「復原」 button that calls it. */
  show(message: string, undo?: () => void): void;
  hide(): void;
}

export interface UndoableAction {
  /** What the toast says, such as 「已刪除 1 筆紀錄」. */
  message: string;
  /** What really happens when the time is up, such as the `history.delete` call. */
  commit: () => void | Promise<void>;
  /** Puts back what was hidden: after 「復原」, and when `commit` failed. */
  restore: () => void;
  /** What the toast says when `commit` failed. */
  failure?: (err: unknown) => string;
}

/**
 * An action that is announced, waits for 10 seconds and only then happens. Until then 「復原」
 * cancels it. There is one at a time: starting a second makes the first one happen at once, as the
 * toast of the first is replaced.
 */
export class Undoable {
  private current: { action: UndoableAction; timer: ReturnType<typeof setTimeout> } | null = null;

  constructor(private readonly notifier: Notifier) {}

  begin(action: UndoableAction): void {
    this.flush();
    this.notifier.show(action.message, () => this.undo());
    this.current = { action, timer: setTimeout(() => this.flush(), UNDO_MS) };
  }

  /** Gets the waiting action back. Too late once it has happened. */
  undo(): void {
    const current = this.current;
    if (!current) return;
    clearTimeout(current.timer);
    this.current = null;
    this.notifier.hide();
    current.action.restore();
  }

  /** Lets the waiting action happen now. */
  flush(): void {
    const current = this.current;
    if (!current) return;
    clearTimeout(current.timer);
    this.current = null;
    this.notifier.hide();
    const fail = (err: unknown): void => {
      current.action.restore();
      this.notifier.show(current.action.failure?.(err) ?? `操作失敗：${describeError(err)}`);
    };
    try {
      const result = current.action.commit();
      if (result instanceof Promise) result.catch(fail);
    } catch (err) {
      fail(err);
    }
  }
}

/** `fn`, but it runs `ms` after the last call, once. `cancel()` drops the call that is waiting. */
export function debounce<A extends unknown[]>(
  fn: (...args: A) => void,
  ms: number,
): ((...args: A) => void) & { cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const wrapped = (...args: A): void => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      timer = undefined;
      fn(...args);
    }, ms);
  };
  wrapped.cancel = (): void => {
    clearTimeout(timer);
    timer = undefined;
  };
  return wrapped;
}
