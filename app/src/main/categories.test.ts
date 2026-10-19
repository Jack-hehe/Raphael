import { describe, expect, test } from "vitest";
import {
  CATEGORY_IDS,
  HALF_FROM,
  LEAD_FROM,
  appLabel,
  categoryOf,
  insightOf,
  percentages,
  personalize,
  programKey,
} from "./categories";
import type { AppUsage } from "./types";

const row = (app: string, words: number, dictations = 1): AppUsage => ({ app, words, dictations });

describe("which kind a program is", () => {
  test("the programs named in the brief", () => {
    expect(categoryOf("ChatGPT.exe")).toBe("ai");
    expect(categoryOf("Claude.exe")).toBe("ai");
    expect(categoryOf("Code.exe")).toBe("dev");
    expect(categoryOf("Cursor.exe")).toBe("dev");
    expect(categoryOf("WindowsTerminal.exe")).toBe("dev");
    expect(categoryOf("WINWORD.EXE")).toBe("docs");
    expect(categoryOf("Notion.exe")).toBe("docs");
    expect(categoryOf("LINE.exe")).toBe("chat");
    expect(categoryOf("Discord.exe")).toBe("chat");
    expect(categoryOf("Slack.exe")).toBe("chat");
    expect(categoryOf("ms-teams.exe")).toBe("chat");
    expect(categoryOf("Teams.exe")).toBe("chat");
    expect(categoryOf("chrome.exe")).toBe("web");
    expect(categoryOf("msedge.exe")).toBe("web");
    expect(categoryOf("firefox.exe")).toBe("web");
  });

  test("case, extension, spaces and a path do not matter", () => {
    expect(categoryOf("code")).toBe("dev");
    expect(categoryOf("CODE.EXE")).toBe("dev");
    expect(categoryOf("Code - Insiders.exe")).toBe("dev");
    expect(categoryOf("LM Studio.exe")).toBe("ai");
    expect(categoryOf("C:\\Program Files\\Mozilla Firefox\\firefox.exe")).toBe("web");
    expect(categoryOf("/usr/bin/chrome")).toBe("web");
    expect(programKey("  Sublime_Text.exe ")).toBe("sublimetext");
  });

  test("Notepad is a document and Notepad++ is for code", () => {
    expect(categoryOf("notepad.exe")).toBe("docs");
    expect(categoryOf("notepad++.exe")).toBe("dev");
  });

  test("what is not known, and the history not knowing, is other", () => {
    expect(categoryOf("Figma.exe")).toBe("other");
    expect(categoryOf("unknown")).toBe("other");
    expect(categoryOf("")).toBe("other");
  });
});

describe("how a program is called", () => {
  test("known names, and the process name made readable", () => {
    expect(appLabel("Code.exe")).toBe("VS Code");
    expect(appLabel("WINWORD.EXE")).toBe("Word");
    expect(appLabel("msedge.exe")).toBe("Edge");
    expect(appLabel("WindowsTerminal.exe")).toBe("Windows Terminal");
    expect(appLabel("slack.exe")).toBe("Slack");
    expect(appLabel("Figma.exe")).toBe("Figma");
    expect(appLabel("obsidian")).toBe("Obsidian");
    expect(appLabel("C:\\Tools\\Mystery.exe")).toBe("Mystery");
  });
});

describe("whole percentages", () => {
  test("they add up to 100", () => {
    expect(percentages([1, 1, 1])).toEqual([34, 33, 33]);
    expect(percentages([34, 28, 14, 12, 7, 5])).toEqual([34, 28, 14, 12, 7, 5]);
    expect(percentages([1, 2, 3, 4, 5, 6, 7])).toEqual([4, 7, 11, 14, 18, 21, 25]);
    for (const weights of [[1, 1, 1], [5, 5, 5, 5, 5, 5, 3], [0.2, 0.3, 0.5], [1, 0, 0, 0, 0, 1]]) {
      expect(percentages(weights).reduce((a, b) => a + b, 0)).toBe(100);
    }
  });

  test("nothing counted is zeros", () => {
    expect(percentages([0, 0, 0])).toEqual([0, 0, 0]);
    expect(percentages([])).toEqual([]);
  });

  test("a single kind is all of it", () => {
    expect(percentages([0, 7, 0])).toEqual([0, 100, 0]);
  });
});

describe("personalize", () => {
  const rows = [
    row("Claude.exe", 5000),
    row("ChatGPT.exe", 3000),
    row("Code.exe", 4000),
    row("WindowsTerminal.exe", 1000),
    row("WINWORD.EXE", 1000),
    row("chrome.exe", 500),
    row("unknown", 500),
  ];

  test("shares by words, every kind in the order of the chart", () => {
    const p = personalize(rows)!;
    expect(p.measure).toBe("words");
    expect(p.categories.map((c) => c.id)).toEqual([...CATEGORY_IDS]);
    expect(p.categories.map((c) => c.weight)).toEqual([8000, 5000, 1000, 0, 500, 500]);
    // 53.3, 33.3, 6.7, 0, 3.3 and 3.3: the two spare points go to the biggest remainders
    expect(p.categories.map((c) => c.percent)).toEqual([54, 33, 7, 0, 3, 3]);
    expect(p.categories.reduce((sum, c) => sum + c.percent, 0)).toBe(100);
  });

  test("each kind names its two busiest programs, never the history not knowing", () => {
    const p = personalize(rows)!;
    const by = Object.fromEntries(p.categories.map((c) => [c.id, c.apps]));
    expect(by.ai).toEqual(["Claude", "ChatGPT"]);
    expect(by.dev).toEqual(["VS Code", "Windows Terminal"]);
    expect(by.docs).toEqual(["Word"]);
    expect(by.chat).toEqual([]);
    expect(by.web).toEqual(["Chrome"]);
    expect(by.other).toEqual([]);
  });

  test("programs of the same kind add up, and the same program under two spellings is one", () => {
    const p = personalize([row("Code.exe", 300), row("code", 200), row("Cursor.exe", 400), row("Windsurf.exe", 50)])!;
    const dev = p.categories.find((c) => c.id === "dev")!;
    expect(dev.weight).toBe(950);
    expect(dev.apps).toEqual(["VS Code", "Cursor"]);
  });

  test("a core that counted no words falls back to the dictations", () => {
    const p = personalize([row("Code.exe", 0, 8), row("Slack.exe", 0, 2)])!;
    expect(p.measure).toBe("dictations");
    expect(p.categories.find((c) => c.id === "dev")!.percent).toBe(80);
    expect(p.categories.find((c) => c.id === "chat")!.percent).toBe(20);
  });

  test("nothing to show: an older core, an empty list, empty rows, nonsense", () => {
    expect(personalize(undefined)).toBeNull();
    expect(personalize([])).toBeNull();
    expect(personalize([row("Code.exe", 0, 0)])).toBeNull();
    expect(personalize([{ app: "Code.exe", words: Number.NaN, dictations: -3 }])).toBeNull();
    expect(personalize("nope" as unknown as AppUsage[])).toBeNull();
  });

  test("a row that is not a row is skipped", () => {
    const p = personalize([null as unknown as AppUsage, { words: 5 } as unknown as AppUsage, row("Code.exe", 10)])!;
    expect(p.categories.find((c) => c.id === "dev")!.percent).toBe(100);
  });
});

describe("what Raphael says", () => {
  const shares = (percent: Record<string, number>) => {
    const rows = Object.entries(percent).map(([app, words]) => row(app, words));
    return personalize(rows)!;
  };

  test("more than half in one kind", () => {
    expect(insightOf(shares({ "Claude.exe": 60, "Code.exe": 40 }))).toEqual({ kind: "half", id: "ai", percent: 60 });
    expect(insightOf(shares({ "Claude.exe": HALF_FROM, "Code.exe": 100 - HALF_FROM }))).toMatchObject({ kind: "half" });
  });

  test("most, but not half", () => {
    expect(insightOf(shares({ "Code.exe": 40, "Claude.exe": 30, "Slack.exe": 30 }))).toEqual({ kind: "most", id: "dev", percent: 40 });
    expect(insightOf(shares({ "Code.exe": LEAD_FROM, "Claude.exe": 33, "Slack.exe": 32 }))).toMatchObject({ kind: "most", id: "dev" });
  });

  test("spread when no kind leads", () => {
    expect(insightOf(shares({ "Code.exe": 20, "Claude.exe": 20, "Slack.exe": 20, "chrome.exe": 20, "WINWORD.EXE": 20 }))).toEqual({ kind: "spread" });
    expect(insightOf(shares({ "Code.exe": LEAD_FROM - 1, "Claude.exe": 33, "Slack.exe": 33 }))).toEqual({ kind: "spread" });
  });

  test("programs nobody knows never lead", () => {
    expect(insightOf(shares({ "Figma.exe": 70, "Code.exe": 30 }))).toEqual({ kind: "spread" });
    expect(insightOf(shares({ "Figma.exe": 50, "Code.exe": 50 }))).toEqual({ kind: "half", id: "dev", percent: 50 });
  });
});
