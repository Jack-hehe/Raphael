// @vitest-environment happy-dom
// Raphael's face on the 拉斐爾 page is his emblem exactly as it is at the bottom right: bare, with no
// background, border, ring, disc, shadow or circle crop, in the member column, the pill, the details
// panel and the thread. (The design page once drew a gold disc behind it by a selector that matched
// the emblem by accident; the owner does not want it.) These read the style sheets for any rule that
// could reach the emblem's element or what it is drawn into, and look at the HTML that draws it.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";
import { columnHTML } from "./member-column";
import { faceHTML, raphaelOf } from "./members";
import { exchangeHTML } from "./timeline";

const MAIN = __dirname;
const sheets = readdirSync(MAIN)
  .filter((name) => name.endsWith(".css"))
  .map((name) => ({ name, css: readFileSync(join(MAIN, name), "utf8").replace(/\/\*[\s\S]*?\*\//g, "") }));

/** Every selector with its declarations, at any depth of @media. */
const rules = sheets.flatMap(({ name, css }) =>
  [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].flatMap(([, selectors, body]) =>
    selectors.trim().startsWith("@") ? [] : selectors.split(",").map((selector) => ({ name, selector: selector.trim().replace(/\s+/g, " "), body })),
  ),
);

/** The last compound of a selector: what the rule styles (pseudo-elements kept, they draw on it too). */
const subject = (selector: string): string => selector.split(/\s+|>|\+|~/).filter(Boolean).pop() ?? "";

/**
 * Whether a rule styles the element of Raphael's face, or the one it is drawn into on this page:
 * `.av` itself (not a member's `.mb-av`, nor the person's own picture, `.av-custom`, which is cut in
 * a circle by design), the pill's face, the panel's face button, Raphael's place in the column,
 * the face of a turn and of the card.
 */
function reachesEmblem(selector: string): boolean {
  if (/\.av-custom|\.mb-av\b|\.mb-av-/.test(selector)) return false;
  const last = subject(selector);
  return /\.av(?![\w-])|\.mb-pill-face|\.sys-face|\.mb-avbtn|\.ra-face|\.ask-face|\.nav-face/.test(last);
}

/** A declaration that would draw something around or behind the emblem. */
function framing(body: string): string[] {
  return body
    .split(";")
    .map((d) => d.trim())
    .filter((d) => /^(background|background-color|background-image|border|border-(top|right|bottom|left)|border-color|border-width|border-style|box-shadow|outline|border-radius|filter)\s*:/.test(d))
    .filter((d) => !/:\s*(transparent|none|0|0px)\s*$/.test(d));
}

describe("Raphael's emblem stands bare", () => {
  test("no style sheet gives the emblem, or what it is drawn into, a background, an edge, a shadow or a round crop", () => {
    const hits = rules.filter((r) => reachesEmblem(r.selector));
    // the rules that size and place it are found, so the reading works
    expect(hits.some((r) => r.name === "avatar.css" && /^\.av$/.test(r.selector))).toBe(true);
    expect(hits.some((r) => r.name === "members.css" && /mb-row > \.av/.test(r.selector))).toBe(true);
    const bad = hits.flatMap((r) => framing(r.body).map((d) => `${r.name}: ${r.selector} { ${d} }`));
    expect(bad).toEqual([]);
  });

  test("Raphael's face is the emblem with none of a member's classes, wherever the page draws it", () => {
    const raphael = raphaelOf(null);
    for (const size of [22, 26, 30, 34, 64, 96]) {
      const html = faceHTML(raphael, size, { cls: "mb-pill-face" });
      expect(html).toMatch(/^<span class="av mb-pill-face" style="--av:\d+px" aria-hidden="true"><img class="em-l"/);
      expect(html).not.toMatch(/mb-av|border|background|radius/);
    }
    const column = document.createElement("div");
    column.innerHTML = columnHTML({ list: null, current: 0, previews: new Map(), query: "", folded: new Set(), fresh: new Set() });
    const face = column.querySelector(".mb-chief > .av")!;
    expect(face.className).toBe("av");
    expect(face.getAttribute("style")).toBe("--av:40px");
  });

  test("on the line of an exchange with him, too", () => {
    const line = document.createElement("ol");
    const turn = { id: 5, created_at_ms: 0, question: "問", route: "exchange", prefix: "", text: "答", detail: null, status: "done", source: "member", member_id: 3, peer_id: 0 };
    line.innerHTML = exchangeHTML(turn, "拉斐爾", false, faceHTML(raphaelOf(null), 18, { cls: "sys-face" }));
    const face = line.querySelector(".sys-x > .av")!;
    expect(face.className).toBe("av sys-face");
    expect(face.querySelector("img.em-l")).not.toBeNull();
  });
});
