import { expect, test } from "vitest";
import { ARC, ProgressArc, cleaningEstimate, expectedCleanupSeconds, type ProgressReport } from "./progress";

const tr = (done: number, total: number, audioSeconds = 5): ProgressReport => ({ stage: "transcribing", done, total, audioSeconds });
const cleaning = (audioSeconds = 5): ProgressReport => ({ stage: "cleaning", done: 1, total: 1, audioSeconds });
const DONE: ProgressReport = { stage: "done", done: 0, total: 0, audioSeconds: 0 };

/** Runs the arc for `seconds` at 60 frames a second. */
function run(arc: ProgressArc, seconds: number): void {
  for (let t = 0; t < seconds - 1e-9; t += 1 / 60) arc.advance(1 / 60);
}

test("the stages fill their own part of the ring", () => {
  expect(ARC.transcribing).toEqual([0, 0.6]);
  expect(ARC.cleaning).toEqual([0.6, 0.95]);
});

test("transcribing fills 0 to 60 % by the segments done", () => {
  const arc = new ProgressArc();
  expect(arc.active).toBe(false);
  arc.report(tr(1, 4));
  expect(arc.active).toBe(true);
  run(arc, 2);
  expect(arc.fraction).toBeCloseTo(0.15, 3);
  arc.report(tr(4, 4));
  run(arc, 2);
  expect(arc.fraction).toBeCloseTo(0.6, 3);
});

test("the arc eases towards a new count instead of jumping", () => {
  const arc = new ProgressArc();
  arc.report(tr(0, 2));
  arc.report(tr(2, 2));
  arc.advance(1 / 24);
  expect(arc.fraction).toBeGreaterThan(0);
  expect(arc.fraction).toBeLessThan(0.4);
  run(arc, 1);
  expect(arc.fraction).toBeCloseTo(0.6, 2);
});

test("nothing left to count is all of the transcribing part", () => {
  const arc = new ProgressArc();
  arc.report(tr(0, 0));
  run(arc, 2);
  expect(arc.fraction).toBeCloseTo(0.6, 3);
});

test("the arc never goes back, even when the core finds another segment", () => {
  const arc = new ProgressArc();
  arc.report(tr(3, 4));
  run(arc, 2);
  const before = arc.fraction;
  arc.report(tr(3, 5));
  run(arc, 2);
  expect(arc.fraction).toBeGreaterThanOrEqual(before);
});

test("counts that make no sense cannot break the arc", () => {
  const arc = new ProgressArc();
  for (const r of [tr(Number.NaN, 3), tr(5, 3), tr(-1, -1), tr(1, Number.POSITIVE_INFINITY)]) {
    arc.report(r);
    run(arc, 0.5);
    expect(Number.isFinite(arc.fraction)).toBe(true);
    expect(arc.fraction).toBeGreaterThanOrEqual(0);
    expect(arc.fraction).toBeLessThanOrEqual(0.6);
  }
});

test("the expected cleanup time is 1.5 s plus 0.02 s per second of speech", () => {
  expect(expectedCleanupSeconds(0)).toBeCloseTo(1.5, 10);
  expect(expectedCleanupSeconds(100)).toBeCloseTo(3.5, 10);
  expect(expectedCleanupSeconds(-3)).toBeCloseTo(1.5, 10);
  expect(expectedCleanupSeconds(Number.NaN)).toBeCloseTo(1.5, 10);
});

test("the cleaning estimate is at 90 % of its range at the expected time and never all of it", () => {
  expect(cleaningEstimate(0, 2)).toBe(0);
  expect(cleaningEstimate(2, 2)).toBeCloseTo(0.9, 10);
  expect(cleaningEstimate(1000, 2)).toBeLessThanOrEqual(1);
  let last = 0;
  for (let t = 0.1; t < 10; t += 0.1) {
    const e = cleaningEstimate(t, 2);
    expect(e).toBeGreaterThan(last);
    last = e;
  }
});

test("cleaning eases from 60 % towards 95 % and never reaches 100 % before the reply", () => {
  const arc = new ProgressArc();
  arc.report(tr(2, 2));
  run(arc, 1);
  arc.report(cleaning(25)); // expected 2 s
  run(arc, 0.1);
  expect(arc.fraction).toBeGreaterThanOrEqual(0.6);
  expect(arc.fraction).toBeLessThan(0.65);
  run(arc, 1.9);
  // 90 % of the cleaning part at the expected time, less the arc's small lag
  expect(arc.fraction).toBeGreaterThan(0.6 + 0.35 * 0.8);
  expect(arc.fraction).toBeLessThan(0.6 + 0.35 * 0.9 + 1e-9);
  run(arc, 60);
  expect(arc.fraction).toBeLessThan(0.95);
  expect(arc.fraction).toBeGreaterThan(0.94);
});

test("a longer dictation is expected to take longer to clean", () => {
  const short = new ProgressArc();
  const long = new ProgressArc();
  short.report(cleaning(0));
  long.report(cleaning(200));
  run(short, 1);
  run(long, 1);
  expect(long.fraction).toBeLessThan(short.fraction);
});

test("transcription that ended while recording goes straight to the cleaning part", () => {
  const arc = new ProgressArc();
  arc.report(tr(2, 3));
  arc.report(tr(2, 2));
  arc.report(cleaning());
  run(arc, 0.5);
  expect(arc.fraction).toBeGreaterThan(0.6);
});

test("done fills the arc at once", () => {
  const arc = new ProgressArc();
  arc.report(cleaning());
  run(arc, 0.5);
  arc.report(DONE);
  expect(arc.fraction).toBe(1);
  run(arc, 0.3);
  expect(arc.fraction).toBe(1);
});

test("done without any report before it (cleanup skipped, quick reply) still fills the ring", () => {
  const arc = new ProgressArc();
  arc.report(DONE);
  expect(arc.fraction).toBe(1);
  expect(arc.active).toBe(true);
});

test("a report from an earlier stage that comes late is ignored", () => {
  const arc = new ProgressArc();
  arc.report(DONE);
  arc.report(tr(1, 4));
  arc.report(cleaning());
  run(arc, 1);
  expect(arc.fraction).toBe(1);
  const c = new ProgressArc();
  c.report(cleaning());
  run(c, 1);
  const f = c.fraction;
  c.report(tr(0, 4));
  run(c, 0.2);
  expect(c.fraction).toBeGreaterThanOrEqual(f);
});

test("reset removes the arc at once, and a new dictation starts from nothing", () => {
  const arc = new ProgressArc();
  arc.report(DONE);
  arc.reset();
  expect(arc.active).toBe(false);
  arc.report(tr(0, 3));
  expect(arc.fraction).toBe(0);
});
