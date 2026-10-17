import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { Deletions, UNDO_MS, Undoable, debounce, type Notifier } from "./undo";

function notifier() {
  const shown: { message: string; hasUndo: boolean }[] = [];
  let hidden = 0;
  const n: Notifier = {
    show(message, undo) {
      shown.push({ message, hasUndo: undo !== undefined });
    },
    hide() {
      hidden++;
    },
  };
  return { n, shown, hidden: () => hidden };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("Undoable", () => {
  test("the toast stays for 10 seconds", () => {
    expect(UNDO_MS).toBe(10_000);
  });

  test("the action happens when the time is up, not before", () => {
    const { n, shown } = notifier();
    const u = new Undoable(n);
    const commit = vi.fn();
    u.begin({ message: "已刪除 1 筆紀錄", commit, restore: vi.fn() });
    expect(shown).toEqual([{ message: "已刪除 1 筆紀錄", hasUndo: true }]);
    vi.advanceTimersByTime(UNDO_MS - 1);
    expect(commit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  test("the toast goes with it", () => {
    const { n, hidden } = notifier();
    const u = new Undoable(n);
    u.begin({ message: "x", commit: vi.fn(), restore: vi.fn() });
    vi.advanceTimersByTime(UNDO_MS);
    expect(hidden()).toBe(1);
  });

  test("undoing brings it back and the action never happens", () => {
    const { n, hidden } = notifier();
    const u = new Undoable(n);
    const commit = vi.fn();
    const restore = vi.fn();
    u.begin({ message: "x", commit, restore });
    vi.advanceTimersByTime(5000);
    u.undo();
    expect(restore).toHaveBeenCalledTimes(1);
    expect(hidden()).toBe(1);
    vi.advanceTimersByTime(60_000);
    expect(commit).not.toHaveBeenCalled();
  });

  test("undo with nothing to undo does nothing", () => {
    const { n } = notifier();
    const u = new Undoable(n);
    u.undo();
    const restore = vi.fn();
    u.begin({ message: "x", commit: vi.fn(), restore });
    vi.advanceTimersByTime(UNDO_MS);
    u.undo(); // too late
    expect(restore).not.toHaveBeenCalled();
  });

  test("a second action makes the first one happen at once", () => {
    const { n } = notifier();
    const u = new Undoable(n);
    const first = vi.fn();
    const second = vi.fn();
    u.begin({ message: "a", commit: first, restore: vi.fn() });
    vi.advanceTimersByTime(3000);
    u.begin({ message: "b", commit: second, restore: vi.fn() });
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).not.toHaveBeenCalled();
    // the second has its own 10 seconds
    vi.advanceTimersByTime(UNDO_MS - 1);
    expect(second).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledTimes(1);
  });

  test("flush makes the action happen now", () => {
    const { n } = notifier();
    const u = new Undoable(n);
    const commit = vi.fn();
    u.begin({ message: "x", commit, restore: vi.fn() });
    u.flush();
    expect(commit).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(UNDO_MS * 2);
    expect(commit).toHaveBeenCalledTimes(1);
  });

  test("an action that fails is undone and the failure is told", async () => {
    const { n, shown } = notifier();
    const u = new Undoable(n);
    const restore = vi.fn();
    u.begin({
      message: "已刪除",
      commit: () => Promise.reject(new Error("拉斐爾的核心沒有回應")),
      restore,
      failure: (err) => `無法刪除：${(err as Error).message}`,
    });
    await vi.advanceTimersByTimeAsync(UNDO_MS);
    expect(restore).toHaveBeenCalledTimes(1);
    expect(shown[shown.length - 1]).toEqual({ message: "無法刪除：拉斐爾的核心沒有回應", hasUndo: false });
  });
});

describe("debounce", () => {
  test("runs once, after the last call", () => {
    const fn = vi.fn();
    const d = debounce(fn, 200);
    d("a");
    vi.advanceTimersByTime(150);
    d("ab");
    vi.advanceTimersByTime(150);
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(50);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith("ab");
  });

  test("can be cancelled", () => {
    const fn = vi.fn();
    const d = debounce(fn, 200);
    d();
    d.cancel();
    vi.advanceTimersByTime(1000);
    expect(fn).not.toHaveBeenCalled();
  });
});

describe("Deletions", () => {
  const item = (id: number) => ({ id, text: `item ${id}` });
  const ids = (items: { id: number }[]) => items.map((i) => i.id);

  test("a list that was read before the delete reached the core does not bring the item back", () => {
    const d = new Deletions();
    const read = d.startRead(); // the list is asked for ...
    d.confirm(2); // ... the delete is done ...
    expect(ids(d.filter(read, [item(3), item(2), item(1)]))).toEqual([3, 1]); // ... the list answers
  });

  test("a list that was read after the delete shows what the core says", () => {
    // SQLite gives the id of a deleted last row to the next row, so a new item can have it.
    const d = new Deletions();
    d.confirm(2);
    const read = d.startRead();
    expect(ids(d.filter(read, [item(2), item(1)]))).toEqual([2, 1]);
  });

  test("items that were not deleted pass in their order", () => {
    const d = new Deletions();
    d.confirm(9);
    expect(ids(d.filter(d.startRead(), [item(5), item(7), item(6)]))).toEqual([5, 7, 6]);
  });

  test("with nothing deleted nothing is left out", () => {
    const d = new Deletions();
    expect(ids(d.filter(d.startRead(), [item(1), item(2)]))).toEqual([1, 2]);
  });

  test("each read is judged by when it started", () => {
    const d = new Deletions();
    const early = d.startRead();
    d.confirm(1);
    const middle = d.startRead();
    d.confirm(2);
    const late = d.startRead();
    const all = [item(1), item(2), item(3)];
    expect(ids(d.filter(early, all))).toEqual([3]);
    expect(ids(d.filter(middle, all))).toEqual([1, 3]);
    expect(ids(d.filter(late, all))).toEqual([1, 2, 3]);
  });
});
