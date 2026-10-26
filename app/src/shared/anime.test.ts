import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";
import { PREFIXES, plainReplies, replyLine, userLine } from "./anime";

describe("plainReplies", () => {
  test("undoes replyLine in every language, so the window shows Raphael's words plainly", () => {
    for (const [prefix, text] of [["答", "台北現在 24 度。"], ["解", "東京は晴れです。"], ["Answer", "It is 24 degrees."], ["告", "正在查看行程。"]])
      expect(plainReplies([replyLine(prefix, text)])).toEqual([text]);
  });

  test("a reply the core split into messages loses only its opening and its closing mark", () => {
    expect(plainReplies(["《答。第一段", "第二段", "第三段》"])).toEqual(["第一段", "第二段", "第三段"]);
    expect(plainReplies(["《了。已建立。》", "《答。好的》"])).toEqual(["已建立。", "好的"]);
  });

  test("plain words, a title in 《》 and an unknown head stay exactly as they were", () => {
    const kept = ["推薦你看《三體》", "《三體》是一部小說。", "《Hello》"];
    expect(plainReplies(kept)).toEqual(kept);
  });

  test("lists the prefixes of the core's persona", () => {
    const persona = readFileSync(resolve(import.meta.dirname, "../../../core/src/raphael_core/agent/persona.py"), "utf8");
    const core = [...persona.matchAll(/^\s*"[\w-]+": Prefixes\(([^)]*)\),$/gm)].flatMap(([, list]) => [...list.matchAll(/"([^"]+)"/g)].map(([, prefix]) => prefix));
    expect(core.length).toBeGreaterThan(0);
    expect(new Set(PREFIXES)).toEqual(new Set(core));
  });
});

describe("replyLine", () => {
  test("Chinese and Japanese prefixes are followed by a full stop", () => {
    expect(replyLine("答", "台北現在 24 度。")).toBe("《答。台北現在 24 度。》");
    expect(replyLine("了", "已開啟。")).toBe("《了。已開啟。》");
    expect(replyLine("解", "東京は晴れです。")).toBe("《解。東京は晴れです。》");
  });

  test("a prefix in letters is followed by a period and a space", () => {
    expect(replyLine("Answer", "It is 24 degrees.")).toBe("《Answer. It is 24 degrees.》");
    expect(replyLine("Question", "Copy this to the clipboard?")).toBe("《Question. Copy this to the clipboard?》");
  });

  test("a prefix that brings its own stop does not get a second one", () => {
    expect(replyLine("答。", "好的")).toBe("《答。好的》");
    expect(replyLine("Notice.", "Done")).toBe("《Notice. Done》");
  });

  test("a reply without text is just the prefix, and a reply without a prefix is just the text", () => {
    expect(replyLine("了", "")).toBe("《了。》");
    expect(replyLine("Understood", "")).toBe("《Understood.》");
    expect(replyLine("", "Hello")).toBe("《Hello》");
  });

  test("surrounding spaces are not kept", () => {
    expect(replyLine(" 答 ", "  好  ")).toBe("《答。好》");
  });
});

describe("userLine", () => {
  test("quotes the words of the person", () => {
    expect(userLine("今天天氣如何")).toBe("「今天天氣如何」");
    expect(userLine("  what time is it ")).toBe("「what time is it」");
  });
});
