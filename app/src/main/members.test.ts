// The members as data (members.ts): the column's sections and search, a row's preview, how a
// routine's schedule is said, the places a group looks at, and the faces.
import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import {
  cleanName,
  faceHTML,
  initialOf,
  isMemberNameTaken,
  isPictureUrl,
  memberMatches,
  memberPlaceholder,
  memberSections,
  parseSource,
  previewOf,
  raphaelOf,
  scheduleText,
  sourceLabel,
} from "./members";
import type { Member, MembersList } from "./types";

afterEach(() => setLocale("zh-TW"));

const PNG = "data:image/png;base64,iVBORw0KGgo=";

function member(id: number, more: Partial<Member> = {}): Member {
  return { id, name: `成員${id}`, role: "", instructions: "", avatar: "", group_id: null, model: "", tools: null, position: id, created: 0, unread: false, waiting: false, preview: "", last_at: null, ...more };
}

const LIST: MembersList = {
  members: [
    member(0, { name: "拉斐爾" }),
    member(1, { name: "進度官", role: "Raphael 專案", group_id: 10 }),
    member(2, { name: "文書", role: "資料整理", group_id: 10, waiting: true, preview: "移動 12 個檔案" }),
    member(3, { name: "日文老師", role: "N2 嚴格版", group_id: 20 }),
    member(4, { name: "行程管家", role: "信箱與行事曆", group_id: null, unread: true }),
    member(5, { name: "孤兒", group_id: 99 }),
  ],
  groups: [
    { id: 20, name: "學習", position: 1, sources: [] },
    { id: 10, name: "專案", position: 0, sources: [{ kind: "github", ref: "example/app" }] },
    { id: 30, name: "空的", position: 2, sources: [] },
  ],
};

describe("the column's sections", () => {
  test("the groups in their order, an empty one too, then 未分組 with the members whose group is gone; never Raphael", () => {
    const sections = memberSections(LIST);
    expect(sections.map((s) => s.group?.name ?? null)).toEqual(["專案", "學習", "空的", null]);
    expect(sections.map((s) => s.members.map((m) => m.id))).toEqual([[1, 2], [3], [], [4, 5]]);
  });

  test("a search keeps the members whose name or role holds the words, and only the sections that keep one", () => {
    expect(memberSections(LIST, "專案").map((s) => [s.group?.name ?? null, s.members.map((m) => m.id)])).toEqual([["專案", [1]]]);
    expect(memberSections(LIST, "老師").map((s) => s.members.map((m) => m.id))).toEqual([[3]]);
    expect(memberSections(LIST, "ｎ２").map((s) => s.members.map((m) => m.id))).toEqual([[3]]); // width and case folded
    expect(memberSections(LIST, "沒有這個人")).toEqual([]);
    expect(memberMatches(member(9, { name: "A" }), "  ")).toBe(true);
  });

  test("未分組 is left out when nobody is in it", () => {
    const list = { ...LIST, members: LIST.members.filter((m) => m.group_id !== null && m.group_id !== 99) };
    expect(memberSections(list).map((s) => s.group?.name ?? null)).toEqual(["專案", "學習", "空的"]);
  });

  test("Raphael is the list's member 0, or his name before there is a list", () => {
    expect(raphaelOf(LIST).name).toBe("拉斐爾");
    expect(raphaelOf(null)).toMatchObject({ id: 0, name: "拉斐爾" });
  });

  test("collapses inner whitespace in names and reserves Raphael's name in every language", () => {
    expect(cleanName("  日文   老師  ")).toBe("日文 老師");
    expect(isMemberNameTaken("日文   老師", ["日文 老師"])).toBe(true);
    expect(isMemberNameTaken("拉斐爾", [])).toBe(true);
    expect(isMemberNameTaken("raphael", [])).toBe(true);
    expect(isMemberNameTaken("ラファエル", [])).toBe(true);
    expect(isMemberNameTaken("進度官", ["日文 老師"])).toBe(false);
  });
});

describe("a row's preview", () => {
  test("a question that waits is 「等你處理：…」 in gold, with the last line of the thread (what is asked)", () => {
    expect(previewOf(LIST.members[2], LIST.members[2].preview)).toEqual({ text: "等你處理：移動 12 個檔案", waiting: true });
    expect(previewOf(member(7, { waiting: true }), " ")).toEqual({ text: "等你處理", waiting: true });
  });

  test("otherwise the last message, on one line", () => {
    expect(previewOf(member(1), "明天\n10:00 有會議")).toEqual({ text: "明天 10:00 有會議", waiting: false });
    expect(previewOf(member(1), undefined)).toEqual({ text: "", waiting: false });
  });
});

describe("the faces", () => {
  test("Raphael's is his bare emblem, never a member's face, a circle or a placeholder", () => {
    const html = faceHTML(raphaelOf(LIST), 64);
    expect(html).toContain('src="/brand/app-emblem-light-128.webp"');
    expect(html).not.toMatch(/mb-av|letter|拉/);
  });

  test("a member's picture is a data URL of an image, anything else the first character of its name", () => {
    expect(faceHTML(member(1, { name: "進度官", avatar_url: PNG }), 34)).toContain(`<img src="${PNG}"`);
    for (const url of [undefined, "file:///C:/x.png", "https://x/y.png", "data:text/html;base64,AAAA"]) {
      const html = faceHTML(member(1, { name: "進度官", avatar_url: url }), 34);
      expect(html).toContain('class="av mb-av mb-av-letter"');
      expect(html).toContain(">進</span>");
      expect(html).not.toContain("<img");
    }
    expect(isPictureUrl(PNG)).toBe(true);
    expect(initialOf("  ")).toBe("");
    expect(initialOf("😀 Bot")).toBe("😀");
  });

  test("a name goes into the page escaped", () => {
    expect(faceHTML(member(1, { name: "<b>x" }), 34)).toContain("&lt;");
  });
});

describe("the composer of a member's thread", () => {
  test("says Message and the name, in English in every language", () => {
    for (const locale of ["zh-TW", "en", "ja"] as const) {
      setLocale(locale);
      expect(memberPlaceholder("進度官")).toBe("Message 進度官");
    }
  });
});

describe("a routine's schedule", () => {
  test("is said as a day, the days of a week or an interval", () => {
    expect(scheduleText({ kind: "daily", time: "09:00" })).toBe("每天 09:00");
    expect(scheduleText({ kind: "weekly", days: [0], time: "20:00" })).toBe("每週日 20:00");
    expect(scheduleText({ kind: "weekly", days: [3, 1, 1], time: "08:30" })).toBe("每週一、週三 08:30");
    expect(scheduleText({ kind: "every", hours: 3 })).toBe("每 3 小時");
    expect(scheduleText({ kind: "every", hours: 1 })).toBe("每小時");
    setLocale("en");
    expect(scheduleText({ kind: "weekly", days: [0], time: "20:00" })).toBe("Every Sunday at 20:00");
  });
});

describe("a group's places", () => {
  test("a GitHub repo by owner/repo or its address, a Notion page by its address or id; nothing else", () => {
    expect(parseSource("example/app")).toEqual({ kind: "github", ref: "example/app" });
    expect(parseSource("https://github.com/example/app.git")).toEqual({ kind: "github", ref: "example/app" });
    expect(parseSource("github.com/example/app/pulls")).toEqual({ kind: "github", ref: "example/app" });
    const page = "https://www.notion.so/Roadmap-0123456789abcdef0123456789abcdef";
    expect(parseSource(page)).toEqual({ kind: "notion", ref: page });
    expect(parseSource("0123456789abcdef0123456789abcdef")).toEqual({ kind: "notion", ref: "0123456789abcdef0123456789abcdef" });
    for (const text of ["", "  ", "hello", "a b/c", "../etc", "https://example.com/x"]) expect(parseSource(text), text).toBeNull();
  });

  test("are shown by their repo or the words of the page's address", () => {
    expect(sourceLabel({ kind: "github", ref: "example/app" })).toBe("example/app");
    expect(sourceLabel({ kind: "notion", ref: "https://www.notion.so/My-Roadmap-0123456789abcdef0123456789abcdef" })).toBe("My Roadmap");
    expect(sourceLabel({ kind: "notion", ref: "0123456789abcdef0123456789abcdef" })).toBe("Notion");
  });
});
