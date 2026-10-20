import { afterEach, describe, expect, test } from "vitest";
import { buildGrid, dateOfTarget, heatAction, heatmapSVG, openableDates, stepTo } from "./heatmap";
import { setLocale } from "./i18n";

afterEach(() => setLocale("zh-TW"));

/** `n` days up to 2026-10-14 (a Wednesday), oldest first, with words from `words(k)` for day k from today. */
function days(n: number, words: (back: number) => number = () => 0) {
  const out = [];
  for (let k = n - 1; k >= 0; k--) {
    const d = new Date(Date.UTC(2026, 9, 14, 12) - k * 86_400_000);
    const w = words(k);
    out.push({ date: d.toISOString().slice(0, 10), dictations: w > 0 ? 1 : 0, words: w });
  }
  return out;
}

/** Dictations on the 8th, 10th and 13th of October 2026; none on the other days. */
const grid = () => buildGrid(days(21, (k) => ({ 1: 40, 4: 120, 6: 15 })[k] ?? 0), 3);

/** The tags of the cells that are buttons. */
const buttons = (svg: string): string[] => svg.match(/<rect [^>]*data-date="[^"]*"[^>]*>/g) ?? [];

describe("the days that can be opened", () => {
  test("only days with a dictation are buttons, with the date, a name and a place in the tab order", () => {
    const svg = heatmapSVG(grid());
    expect(buttons(svg).map((tag) => tag.match(/data-date="([^"]*)"/)![1])).toEqual(["2026-10-08", "2026-10-10", "2026-10-13"]);
    for (const tag of buttons(svg)) expect(tag).toContain('role="button"');
    expect(svg).toContain('aria-label="10 月 13 日：40 字，開啟當天的聽寫紀錄"');
  });

  test("future days, days without data and days with nothing said have no button", () => {
    const svg = heatmapSVG(grid());
    for (const state of ["future", "nodata", "h0"]) {
      const cells = svg.match(new RegExp(`<rect class="${state}"[^>]*>`, "g")) ?? [];
      for (const tag of cells) expect(tag).not.toContain("data-date");
    }
    expect(svg).toContain('class="future"');
  });

  test("the grid is a group with a summary label, not an image whose cells could not be reached", () => {
    const svg = heatmapSVG(grid());
    expect(svg).toContain('role="group"');
    expect(svg).not.toContain('role="img"');
    expect(svg).toContain('aria-label="9 月 27 日到10 月 17 日，有 3 天聽寫，共 175 字"');
  });

  test("one tab stop: the newest day, or the one given if it can be opened", () => {
    const tabbable = (svg: string) => buttons(svg).filter((tag) => tag.includes('tabindex="0"')).map((tag) => tag.match(/data-date="([^"]*)"/)![1]);
    expect(tabbable(heatmapSVG(grid()))).toEqual(["2026-10-13"]);
    expect(tabbable(heatmapSVG(grid(), "2026-10-08"))).toEqual(["2026-10-08"]);
    expect(tabbable(heatmapSVG(grid(), "2026-10-09"))).toEqual(["2026-10-13"]); // not a day with a dictation
    expect(tabbable(heatmapSVG(grid(), "2025-01-01"))).toEqual(["2026-10-13"]); // not in view
    expect(buttons(heatmapSVG(grid(), "2026-10-08")).filter((tag) => tag.includes('tabindex="-1"'))).toHaveLength(2);
  });

  test("a grid without a dictation has no button and no tab stop", () => {
    const svg = heatmapSVG(buildGrid(days(14), 2));
    expect(buttons(svg)).toEqual([]);
    expect(svg).not.toContain("tabindex");
  });

  test("the names follow the language of the window", () => {
    setLocale("en");
    expect(heatmapSVG(grid())).toContain('aria-label="10/13: 40 characters. Open that day&#39;s history"');
    setLocale("ja");
    expect(heatmapSVG(grid())).toContain('aria-label="10月13日：40文字。その日の履歴を開く"');
  });

  test("the days come oldest first, the order of the cells", () => {
    expect(openableDates(grid())).toEqual(["2026-10-08", "2026-10-10", "2026-10-13"]);
  });

  test("a title for the mouse stays on every known day", () => {
    expect(heatmapSVG(grid())).toContain("<title>10 月 13 日：40 字</title>");
  });
});

/** An element that is a cell of the grid, or inside one, or not. */
const cell = (date: string) => {
  const found = { getAttribute: (name: string) => (name === "data-date" ? date : null) };
  return { closest: (selector: string) => (selector === ".heat [data-date]" ? found : null) } as unknown as Element;
};
const elsewhere = { closest: () => null } as unknown as Element;

describe("what a click or a key does", () => {
  const dates = ["2026-10-08", "2026-10-10", "2026-10-13"];

  test("the date of the cell that is aimed at, if it is one", () => {
    expect(dateOfTarget(cell("2026-10-10"))).toBe("2026-10-10");
    expect(dateOfTarget(elsewhere)).toBeNull();
    expect(dateOfTarget(null)).toBeNull();
  });

  test("a click on a day opens it", () => {
    expect(heatAction({ type: "click", target: cell("2026-10-10") }, dates)).toEqual({ open: "2026-10-10" });
  });

  test("Enter and Space on a day open it", () => {
    for (const key of ["Enter", " "]) {
      expect(heatAction({ type: "keydown", key, target: cell("2026-10-13") }, dates)).toEqual({ open: "2026-10-13" });
    }
  });

  test("the arrow keys move between the days that can be opened", () => {
    const from = cell("2026-10-10");
    expect(heatAction({ type: "keydown", key: "ArrowLeft", target: from }, dates)).toEqual({ focus: "2026-10-08" });
    expect(heatAction({ type: "keydown", key: "ArrowUp", target: from }, dates)).toEqual({ focus: "2026-10-08" });
    expect(heatAction({ type: "keydown", key: "ArrowRight", target: from }, dates)).toEqual({ focus: "2026-10-13" });
    expect(heatAction({ type: "keydown", key: "ArrowDown", target: from }, dates)).toEqual({ focus: "2026-10-13" });
    expect(heatAction({ type: "keydown", key: "Home", target: cell("2026-10-13") }, dates)).toEqual({ focus: "2026-10-08" });
    expect(heatAction({ type: "keydown", key: "End", target: cell("2026-10-08") }, dates)).toEqual({ focus: "2026-10-13" });
  });

  test("nowhere to go past the ends, and other keys do nothing", () => {
    expect(heatAction({ type: "keydown", key: "ArrowLeft", target: cell("2026-10-08") }, dates)).toBeNull();
    expect(heatAction({ type: "keydown", key: "ArrowRight", target: cell("2026-10-13") }, dates)).toBeNull();
    expect(heatAction({ type: "keydown", key: "Home", target: cell("2026-10-08") }, dates)).toBeNull();
    for (const key of ["Tab", "Escape", "a", "Shift"]) {
      expect(heatAction({ type: "keydown", key, target: cell("2026-10-10") }, dates)).toBeNull();
    }
  });

  test("nothing happens for anything else in the page", () => {
    expect(heatAction({ type: "click", target: elsewhere }, dates)).toBeNull();
    expect(heatAction({ type: "keydown", key: "Enter", target: elsewhere }, dates)).toBeNull();
    expect(heatAction({ type: "click", target: null }, dates)).toBeNull();
  });
});

describe("stepTo", () => {
  const dates = ["a", "b", "c", "d"];

  test("neighbours, ends and the ways out", () => {
    expect(stepTo(dates, "b", "ArrowRight")).toBe("c");
    expect(stepTo(dates, "b", "ArrowLeft")).toBe("a");
    expect(stepTo(dates, "b", "End")).toBe("d");
    expect(stepTo(dates, "c", "Home")).toBe("a");
    expect(stepTo(dates, "a", "ArrowLeft")).toBeNull();
    expect(stepTo(dates, "d", "ArrowDown")).toBeNull();
    expect(stepTo(dates, "zz", "ArrowRight")).toBeNull();
    expect(stepTo([], "a", "End")).toBeNull();
    expect(stepTo(dates, "b", "PageDown")).toBeNull();
  });
});
