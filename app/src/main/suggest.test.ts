import { describe, expect, it } from "vitest";
import { dismissMessage, suggestionsHTML, visibleSuggestions } from "./suggest";

describe("suggestionsHTML", () => {
  it("gives each word an add button and a quiet dismiss button", () => {
    const html = suggestionsHTML(["拉斐爾", "Tauri"]);
    expect(html).toContain("從最近的聽寫找到：");
    expect(html.match(/data-act="suggest"/g)).toHaveLength(2);
    expect(html.match(/data-act="dismiss"/g)).toHaveLength(2);
    expect(html).toContain('data-act="dismiss" data-word="拉斐爾"');
    expect(html).toContain('aria-label="不要再建議「拉斐爾」"');
    expect(html).toContain('data-act="suggest" data-word="Tauri"');
  });

  it("escapes the word everywhere it appears", () => {
    const html = suggestionsHTML([`<img src=x onerror="alert(1)">'`]);
    expect(html).not.toContain("<img");
    expect(html).not.toContain(`onerror="`);
    expect(html).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&#39;");
    // in the add button's text, both data-word attributes and the dismiss label
    expect(html.match(/&lt;img/g)).toHaveLength(4);
  });

  it("is empty without words", () => {
    expect(suggestionsHTML([])).toBe("");
  });
});

describe("visibleSuggestions", () => {
  it("leaves out the words being dismissed", () => {
    expect(visibleSuggestions(["甲", "乙", "丙"], new Set(["乙"]))).toEqual(["甲", "丙"]);
    expect(visibleSuggestions(["甲"], new Set())).toEqual(["甲"]);
  });
});

describe("dismissMessage", () => {
  it("names the word", () => {
    expect(dismissMessage("拉斐爾")).toBe("已略過「拉斐爾」");
  });
});
