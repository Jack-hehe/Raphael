// @vitest-environment happy-dom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { installMock } from "./mock-ipc.js";
import { scenes } from "./scenes/index.mjs";

const fixture = JSON.parse(readFileSync(resolve(import.meta.dirname, "fixture.json"), "utf8"));
describe("real app entry with the shared look fixture", () => {
  let state;
  afterAll(() => window.happyDOM.abort());
  beforeAll(async () => {
    document.documentElement.innerHTML = readFileSync(resolve(import.meta.dirname, "../../index.html"), "utf8").replace(/<script[\s\S]*?<\/script>/g, "");
    HTMLElement.prototype.scrollTo = () => {};
    HTMLElement.prototype.scrollIntoView = () => {};
    state = installMock(fixture);
    await import("../../src/main/main.ts");
    await vi.waitFor(() => expect(document.querySelector('.page-home:not([hidden]) .ho-hero')).not.toBeNull());
  });
  it("boots and navigates all primary destinations and settings without unmocked calls", async () => {
    expect(document.querySelector('#nav .nav')?.dataset.go).toBe("home");
    document.querySelector('#nav .nav[data-go="agent"]').click();
    await vi.waitFor(() => expect(document.querySelector(".page-agent:not([hidden]) .conversation-trigger")).not.toBeNull());
    expect(document.body.textContent).toContain("這週的研究與安排");
    // A reply wait must not select the earlier, collapsed work messages.
    await vi.waitFor(() => {
      const reply = document.querySelector(scenes.find(scene => scene.name === "chat").steps[0].selector);
      expect(reply?.querySelector(".message-text")?.textContent).toContain("這週的研究有三個重點");
      expect(reply?.closest("[hidden]")).toBeNull();
    });
    for (const destination of ["home", "team", "dictation", "agent"]) {
      document.querySelector(`#nav .nav[data-go="${destination}"]`).click();
      await vi.waitFor(() => expect(document.querySelector(`#nav .nav[data-go="${destination}"]`).getAttribute("aria-current"), destination).toBe("page"));
    }
    document.querySelector('#nav .nav[data-go="home"]').click();
    await vi.waitFor(() => expect(document.querySelector('.ho-today [data-home-go="calendar"]')).not.toBeNull());
    expect(document.querySelector('#nav .nav[data-go="automations"]')).toBeNull();
    document.querySelector('.ho-today [data-home-go="calendar"]').click();
    await vi.waitFor(() => expect(document.querySelector('.au-page:not([hidden]) .au-calendar-scroll')).not.toBeNull());
    expect(document.querySelector('#nav .nav[aria-current="page"]').dataset.go).toBe("home");
    document.querySelector('#nav .nav[data-go="settings"]').click();
    await vi.waitFor(() => expect(document.querySelector('.modal-scrim [data-pane="general"]')).not.toBeNull());
    for (const pane of ["general", "appearance", "dictation", "agent", "connections", "messaging", "models", "data", "about"]) {
      document.querySelector(`.modal-scrim [data-pane="${pane}"]`).click();
      await vi.waitFor(() => expect(document.querySelector(`.modal-scrim [data-pane="${pane}"][aria-current="page"]`), pane).not.toBeNull());
    }
    expect(state.errors).toEqual([]);
  });
  it("exercises the usage member expansion with the shared fixture", async () => {
    document.querySelector('.modal-scrim [data-pane="models"]').click();
    await vi.waitFor(() => expect(document.querySelector('[data-models-tab="usage"]')).not.toBeNull());
    document.querySelector('[data-models-tab="usage"]').click();
    await vi.waitFor(() => expect(document.querySelectorAll(".run-members .run-bars > li")).toHaveLength(5));
    const expand = document.querySelector('[data-run-act="members"]');
    expect(expand).not.toBeNull();
    expand.click();
    expect(document.querySelectorAll(".run-members .run-bars > li")).toHaveLength(6);
  });
});
