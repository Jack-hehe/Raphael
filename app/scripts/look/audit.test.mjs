import { Window } from "happy-dom";
import { describe, expect, it } from "vitest";
import { auditDocument, failedChecks } from "./audit.mjs";

describe("rendered look checks", () => {
  it("rejects known English interface labels while preserving user content and brands", () => {
    const window = new Window();
    window.document.body.innerHTML = '<button>Save</button><button>Raphael</button><p class="message-text">Save</p><button hidden>Cancel</button>';
    for (const element of window.document.querySelectorAll('*')) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 110, bottom: 40, width: 100, height: 30 });
    const result = auditDocument(window.document, { width: 1120, height: 900 }, [], ['Save', 'Cancel', 'Raphael']);
    expect(result.productRules.map(item => item.rule)).toEqual(['R-UI-14']);
  });
  const productAudit = html => {
    const window = new Window();
    window.document.body.innerHTML = html;
    for (const element of window.document.querySelectorAll('*')) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 110, bottom: 40, width: 100, height: 30 });
    return { window, audit: () => auditDocument(window.document, { width: 1120, height: 900 }, []) };
  };
  it("rejects duplicate custom and native tooltips", () => {
    const { audit } = productAudit('<button data-tooltip="Save" title="Save">Save</button><button data-tooltip="Open"><svg><title>Open</title></svg></button><button title="Native">Native</button>');
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-26', 'R-UI-26']);
  });
  it("rejects visible row destruction but permits overflow menus", () => {
    const { audit } = productAudit('<div class="mp-resource"><button class="danger">Delete</button></div><div role="menu"><div class="mp-resource"><button class="danger">Delete</button></div></div><div class="mp-resource" hidden><button class="danger">Delete</button></div>');
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-11']);
  });
  it("checks text frames and bare icon borders", () => {
    const { audit } = productAudit('<button class="btn" style="border: none">Save</button><button class="btn icon" style="border: 1px solid red">X</button><button class="btn" style="border: 1px solid red">Cancel</button><button class="btn brand" style="border:none">Raphael</button>');
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-18', 'R-UI-18']);
  });
  it("checks rendered primary and destructive action positions", () => {
    const { window, audit } = productAudit('<div class="action-row"><button class="pri">Save</button><span class="action-destructive"><button>Delete</button></span></div>');
    const primary = window.document.querySelector('.pri');
    primary.getBoundingClientRect = () => ({ left: 0, top: 10, right: 50, bottom: 40, width: 50, height: 30 });
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-27', 'R-UI-27']);
  });
  it("distinguishes date choices and navigation from ordinary text actions", () => {
    const { audit } = productAudit('<div class="au-month-grid"><button class="btn" style="border:0">25</button></div><button class="btn mb-panel-back" style="border:0">Members</button><button class="btn mc-source" style="border:0">Provider</button><button class="btn cs-main" style="border:0">Notion service details</button><button class="btn conversation-new" style="border:0">New conversation</button>');
    expect(audit().productRules.map(item => item.text)).toEqual(['New conversation']);
  });
  it("rejects non-finite durations in visible work summaries", () => {
    const window = new Window();
    window.document.body.innerHTML = '<button class="work-toggle">已處理 NaN 秒</button><div hidden><span class="step-ms">Infinity</span></div>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    const result = { ...auditDocument(window.document, { width: 1120, height: 900 }, []), runtimeErrors: [] };
    expect(result.invalidDurations).toHaveLength(1);
    expect(failedChecks(result)).toBe(true);
  });
  it("reports visible raw translation keys and native controls, ignoring hidden pages", () => {
    const window = new Window();
    window.document.body.innerHTML = '<p>members.missingLabel</p><select><option>One</option></select><input type="time"><div hidden><select></select><p>settings.hidden</p></div>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    const result = auditDocument(window.document, { width: 1120, height: 900 }, ["members", "settings"]);
    expect(result.missingI18nKeys.map(item => item.key)).toEqual(["members.missingLabel"]);
    expect(result.nativeControls.map(item => item.tag)).toEqual(["select", "input"]);
  });
  it("fails horizontal overflow and reports controls outside the viewport", () => {
    const window = new Window();
    window.document.body.innerHTML = '<button id="outside">Save</button>';
    const button = window.document.querySelector("button");
    button.getBoundingClientRect = () => ({ left: 1100, top: 10, right: 1200, bottom: 40, width: 100, height: 30 });
    Object.defineProperty(window.document.documentElement, "scrollWidth", { value: 1200 });
    const result = { ...auditDocument(window.document, { width: 1120, height: 900 }, []), runtimeErrors: [] };
    expect(result.outsideViewport.map(item => item.id)).toEqual(["outside"]);
    expect(result.horizontalOverflow.document).toBe(true);
    expect(failedChecks(result)).toBe(true);
  });
  it("excludes content deliberately outside a scrolling container", () => {
    const window = new Window();
    window.document.body.innerHTML = '<div style="overflow-y: auto"><button>Older item</button></div>';
    const container = window.document.querySelector("div"), button = window.document.querySelector("button");
    container.getBoundingClientRect = () => ({ left: 10, top: 10, right: 300, bottom: 100, width: 290, height: 90 });
    button.getBoundingClientRect = () => ({ left: 10, top: 910, right: 200, bottom: 940, width: 190, height: 30 });
    expect(auditDocument(window.document, { width: 1120, height: 900 }, []).outsideViewport).toEqual([]);
  });
  it("does not call a native input visible when it is outside its scrollport", () => {
    const window = new Window();
    window.document.body.innerHTML = '<div style="overflow-y: auto"><input type="time" aria-label="settings.offscreen"></div>';
    const container = window.document.querySelector("div"), input = window.document.querySelector("input");
    container.getBoundingClientRect = () => ({ left: 10, top: 10, right: 300, bottom: 100, width: 290, height: 90 });
    input.getBoundingClientRect = () => ({ left: 10, top: 200, right: 200, bottom: 230, width: 190, height: 30 });
    const result = auditDocument(window.document, { width: 1120, height: 900 }, ["settings"]);
    expect(result.nativeControls).toEqual([]);
    expect(result.missingI18nKeys).toEqual([]);
  });
});
