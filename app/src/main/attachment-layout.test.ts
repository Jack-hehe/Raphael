// @vitest-environment happy-dom
import { expect, test } from "vitest";
import { sentFileHTML } from "./attachments";
import { pendingTurnHTML } from "./timeline";

const image = { id: `${"a".repeat(32)}.png`, name: "photo.png", mime: "image/png", size: 2048 };
const video = { id: `${"b".repeat(32)}.mp4`, name: "movie.mp4", mime: "video/mp4", size: 4096 };
const audio = { id: `${"c".repeat(32)}.wav`, name: "voice.wav", mime: "audio/wav", size: 4096 };
const file = { id: `${"d".repeat(32)}.pdf`, name: "report.pdf", mime: "application/pdf", size: 2048 };
const parse = (html: string) => { const root = document.createElement("div"); root.innerHTML = html; return root; };

test("owner media precedes the separate text bubble, with visual media in a grid", () => {
  const root = parse(pendingTurnHTML("Caption", "app", { face: () => "" }, [file, image, audio, video]));
  const owner = root.querySelector(".owner-content")!;
  expect(owner).not.toBeNull();
  expect(owner.querySelector(".bubble .me-files")).toBeNull();
  expect([...owner.children].map(node => node.className)).toEqual(["me-files", "bubble"]);
  expect(owner.querySelectorAll(".attachment-grid > .me-file")).toHaveLength(2);
  expect(owner.querySelector(".bubble")!.textContent).toBe("Caption");
  expect(parse(pendingTurnHTML("", "app", { face: () => "" }, [image])).querySelector(".bubble")).toBeNull();
});

test("pictures and video have no visible filename, size, or separate information card", () => {
  for (const attachment of [image, video]) {
    const root = parse(sentFileHTML(attachment));
    expect(root.querySelector(".attachment-info,.attachment-name,.me-file-t")).toBeNull();
    expect(root.textContent).toBe("");
  }
  expect(parse(sentFileHTML(video)).querySelector("video[controls]")).not.toBeNull();
});

test("audio has one pill with controls; documents open from their whole card", () => {
  const sound = parse(sentFileHTML(audio));
  expect(sound.querySelector(".attachment-audio [data-audio-toggle]")).not.toBeNull();
  expect(sound.querySelector('input[type="range"]')).not.toBeNull();
  expect(sound.querySelector("[data-audio-time]")).not.toBeNull();
  expect(sound.querySelector("audio[controls]")).toBeNull();
  expect(sound.querySelectorAll(".attachment-name")).toHaveLength(1);
  const document = parse(sentFileHTML(file));
  expect(document.querySelector("button.attachment-file[data-act='attachment-open']")).not.toBeNull();
  expect(document.textContent).toContain("2 KB · PDF");
});
