import { expect, test } from "vitest";
import { WAITING_LINE_MS, nextWaitingLine } from "./waiting-lines";

const BOOK = ["one", "two", "three", "four", "five"];

test("a line changes every 1.5 s", () => {
  expect(WAITING_LINE_MS).toBe(1500);
});

test("the next line is another one, from the same book", () => {
  for (const previous of BOOK) {
    for (const value of [0, 0.25, 0.5, 0.75, 0.999999]) {
      const line = nextWaitingLine(BOOK, previous, () => value);
      expect(BOOK).toContain(line);
      expect(line).not.toBe(previous);
    }
  }
});

test("any of the other lines can come", () => {
  const previous = BOOK[3];
  const reached = new Set<string>();
  for (let i = 0; i < 1000; i++) reached.add(nextWaitingLine(BOOK, previous, () => i / 1000));
  expect(reached).toEqual(new Set(BOOK.filter((line) => line !== previous)));
});

test("the first line may be any line of the book", () => {
  const reached = new Set<string>();
  for (let i = 0; i < 1000; i++) reached.add(nextWaitingLine(BOOK, null, () => i / 1000));
  expect(reached).toEqual(new Set(BOOK));
});

test("a line that is not in the book (the book changed) does not shorten it", () => {
  const reached = new Set<string>();
  for (let i = 0; i < 1000; i++) reached.add(nextWaitingLine(BOOK, "gone", () => i / 1000));
  expect(reached).toEqual(new Set(BOOK));
});

test("a book of one line has no other to change to", () => {
  expect(nextWaitingLine(["only"], "only")).toBe("only");
  expect(nextWaitingLine(["only"], null)).toBe("only");
});

test("a random number that is out of its range or not a number still gives a line", () => {
  for (const value of [-1, 1, 2, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
    const line = nextWaitingLine(BOOK, BOOK[0], () => value);
    expect(BOOK).toContain(line);
    expect(line).not.toBe(BOOK[0]);
  }
});

test("the real Math.random never gives the same line twice in a row", () => {
  let line: string | null = null;
  for (let i = 0; i < 2000; i++) {
    const next = nextWaitingLine(BOOK, line);
    expect(next).not.toBe(line);
    line = next;
  }
});
