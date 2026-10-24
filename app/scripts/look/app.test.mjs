// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { installMock } from "./mock-ipc.js";
import { scenes } from "./scenes/index.mjs";

const fixture = JSON.parse(readFileSync(resolve(import.meta.dirname, "fixture.json"), "utf8"));
const flush = () => new Promise(resolve => setTimeout(resolve, 50));
describe("real app entry with the shared look fixture", () => {
  afterAll(() => window.happyDOM.abort());
  it("boots and navigates all primary destinations and settings without unmocked calls", async () => {
    document.documentElement.innerHTML = readFileSync(resolve(import.meta.dirname, "../../index.html"), "utf8").replace(/<script[\s\S]*?<\/script>/g, "");
    HTMLElement.prototype.scrollTo = () => {};
    HTMLElement.prototype.scrollIntoView = () => {};
    const state = installMock(fixture);
    await import("../../src/main/main.ts");
    await flush();
    expect(document.querySelector(".page-agent:not([hidden]) .conversation-trigger")).not.toBeNull();
    expect(document.body.textContent).toContain("這週的研究與安排");
    // A reply wait must not select the earlier, collapsed work messages.
    const reply = document.querySelector(scenes.find(scene => scene.name === "chat").steps[0].selector);
    expect(reply?.querySelector(".message-text")?.textContent).toContain("這週的研究有三個重點");
    expect(reply?.closest("[hidden]")).toBeNull();
    for (const destination of ["automations", "team", "dictation", "agent"]) {
      document.querySelector(`#nav .nav[data-go="${destination}"]`).click();
      await flush();
      expect(document.querySelector(`#nav .nav[data-go="${destination}"]`).getAttribute("aria-current"), destination).toBe("page");
    }
    document.querySelector('#nav .nav[data-go="settings"]').click();
    await flush();
    for (const pane of ["general", "appearance", "dictation", "agent", "connections", "messaging", "models", "data", "about"]) {
      document.querySelector(`.modal-scrim [data-pane="${pane}"]`).click();
      await flush();
      expect(document.querySelector(`.modal-scrim [data-pane="${pane}"][aria-current="page"]`), pane).not.toBeNull();
    }
    expect(state.errors).toEqual([]);
  });
  it("exercises the usage member expansion with the shared fixture", async () => {
    document.querySelector('.modal-scrim [data-pane="models"]').click();
    await flush();
    document.querySelector('[data-models-tab="usage"]').click();
    await flush();
    expect(document.querySelectorAll(".run-members .run-bars > li")).toHaveLength(5);
    const expand = document.querySelector('[data-run-act="members"]');
    expect(expand).not.toBeNull();
    expand.click();
    expect(document.querySelectorAll(".run-members .run-bars > li")).toHaveLength(6);
  });
});
