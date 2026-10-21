import { describe, expect, test } from "vitest";
import { replyLine, userLine } from "./anime";

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
