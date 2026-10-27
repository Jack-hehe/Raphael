import { Window } from "happy-dom";
import { describe, expect, it, vi } from "vitest";
import { auditDocument, failedChecks } from "./audit.mjs";
import { checkRendered } from "./checks.mjs";
import { scenes } from "./scenes/index.mjs";
import { LATIN_NAMES } from "./runner.mjs";

describe("rendered look checks", () => {
  it("rejects known English interface labels while preserving user content and brands", () => {
    const window = new Window();
    window.document.body.innerHTML = '<button>Save</button><button>Raphael</button><p class="message-text">Save</p><button hidden>Cancel</button>';
    for (const element of window.document.querySelectorAll('*')) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 110, bottom: 40, width: 100, height: 30 });
    const result = auditDocument(window.document, { width: 1120, height: 900 }, [], null, ['Save', 'Cancel', 'Raphael']);
    expect(result.productRules.map(item => item.rule)).toEqual(['R-UI-14']);
  });
  const productAudit = html => {
    const window = new Window();
    window.document.body.innerHTML = html;
    for (const element of window.document.querySelectorAll('*')) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 110, bottom: 40, width: 100, height: 30 });
    return { window, audit: () => auditDocument(window.document, { width: 1120, height: 900 }, []) };
  };
  it("checks visible primary calendar choices without counting the hidden responsive copy", () => {
    const window = new Window();
    window.document.body.innerHTML = '<aside hidden><button data-drag-member="0"></button><button data-drag-member="1"></button></aside><div><button data-drag-member="1"></button><button data-drag-member="0"></button></div>';
    for (const element of window.document.querySelectorAll('div button')) element.getBoundingClientRect = () => ({ width: 100, height: 30 });
    window.__look = { data: { primaryId: 1, members: [{ id: 0 }, { id: 1 }] }, calls: [] };
    vi.stubGlobal('document', window.document); vi.stubGlobal('window', window);
    try {
      expect(checkRendered('primary-calendar').members).toEqual([1, 0]);
      window.document.querySelector('div').innerHTML = '<button data-drag-member="0"></button><button data-drag-member="1"></button>';
      for (const element of window.document.querySelectorAll('div button')) element.getBoundingClientRect = () => ({ width: 100, height: 30 });
      expect(() => checkRendered('primary-calendar')).toThrow(/exchange/);
    } finally { vi.unstubAllGlobals(); }
  });
  it("rejects a small centered team board at full screen", () => {
    const window = new Window();
    window.document.body.innerHTML = '<div class="team-board-scroll"><div class="team-group"></div><div class="team-group"></div></div>';
    const rect = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height });
    window.document.querySelector('.team-board-scroll').getBoundingClientRect = () => rect(232, 100, 1688, 894);
    const groups = window.document.querySelectorAll('.team-group');
    groups[0].getBoundingClientRect = () => rect(780, 273, 278, 334);
    groups[1].getBoundingClientRect = () => rect(1082, 273, 278, 198);
    vi.stubGlobal('document', window.document); vi.stubGlobal('innerWidth', 1920);
    try {
      expect(() => checkRendered('team-board-space')).toThrow(/R-UI-01/);
      groups[0].getBoundingClientRect = () => rect(300, 340, 742, 400);
      groups[1].getBoundingClientRect = () => rect(1090, 340, 742, 400);
      expect(checkRendered('team-board-space').widthShare).toBeGreaterThanOrEqual(0.8);
      groups[0].getBoundingClientRect = () => rect(300, 340, 742, 120);
      groups[1].getBoundingClientRect = () => rect(1090, 340, 742, 120);
      expect(checkRendered('team-board-space').widthShare).toBeGreaterThanOrEqual(0.8);
      vi.stubGlobal('innerWidth', 1120);
      expect(checkRendered('team-board-space')).toEqual({ check: 'team-board-space', wide: false });
    } finally { vi.unstubAllGlobals(); }
  });
  it("rejects a stretched team panel and raw CLI labels while accepting content-sized panels", () => {
    const window = new Window();
    window.document.body.innerHTML = '<section class="team-group" style="padding:12px"><header style="margin-bottom:12px"></header><div class="team-group-members"><article class="team-member"><span class="team-model">Haiku / Claude Code</span></article></div></section>';
    const group = window.document.querySelector('.team-group'), header = group.querySelector('header'), members = group.querySelector('.team-group-members');
    Object.defineProperty(header, 'offsetHeight', { value: 30 }); Object.defineProperty(members, 'offsetHeight', { value: 140 });
    let height = 206; Object.defineProperty(group, 'offsetHeight', { get: () => height });
    vi.stubGlobal('document', window.document); vi.stubGlobal('getComputedStyle', window.getComputedStyle.bind(window));
    try {
      expect(checkRendered('team-content-size').panels[0].height).toBe(206);
      height = 900; expect(() => checkRendered('team-content-size')).toThrow(/own content/);
      height = 206; members.querySelector('span').textContent = 'cli:claude:haiku'; expect(() => checkRendered('team-content-size')).toThrow(/raw CLI/);
      members.querySelector('span').textContent = 'GPT-6.1 Sol?effort=high'; expect(() => checkRendered('team-content-size')).toThrow(/raw CLI/);
    } finally { vi.unstubAllGlobals(); }
  });
  it("rejects a tall single-column team group and more than three columns", () => {
    const window = new Window();
    window.document.body.innerHTML = '<section class="team-group" style="--team-card-width:220px"><div class="team-group-members" style="display:grid;column-gap:12px"></div></section>';
    const members = window.document.querySelector('.team-group-members');
    members.innerHTML = '<article></article>'.repeat(8);
    let width = 684, columns = 3;
    Object.defineProperty(members, 'clientWidth', { get: () => width });
    [...members.children].forEach((card, index) => Object.defineProperty(card, 'offsetTop', { get: () => Math.floor(index / columns) * 120 }));
    vi.stubGlobal('document', window.document); vi.stubGlobal('getComputedStyle', window.getComputedStyle.bind(window));
    const draw = () => { members.style.gridTemplateColumns = Array(columns).fill('220px').join(' '); return checkRendered('team-member-columns'); };
    try {
      expect(draw().panels[0].columns).toBe(3);
      columns = 1; expect(draw).toThrow(/all available columns/);
      columns = 4; expect(draw).toThrow(/at most three/);
      columns = 2; width = 452; expect(draw().panels[0].columns).toBe(2);
      columns = 1; width = 220; expect(draw().panels[0].columns).toBe(1);
    } finally { vi.unstubAllGlobals(); }
  });
  it("rejects mismatched adjacent import heights and schedule widths (R-UI-17)", () => {
    const { window, audit } = productAudit('<div class="mp-add"><input><button class="btn" style="border:1px solid gray">Import</button></div><label class="au-field"><button class="sel">End</button><input class="mb-fin"></label>');
    const input = window.document.querySelector('.mp-add input');
    input.getBoundingClientRect = () => ({ left: 10, top: 10, right: 110, bottom: 54, width: 100, height: 44 });
    const count = window.document.querySelector('.mb-fin');
    count.getBoundingClientRect = () => ({ left: 10, top: 10, right: 128, bottom: 40, width: 118, height: 30 });
    expect(audit().productRules.filter(item => item.rule === 'R-UI-17')).toHaveLength(2);
    input.getBoundingClientRect = count.getBoundingClientRect = () => ({ left: 10, top: 10, right: 110, bottom: 40, width: 100, height: 30 });
    expect(audit().productRules).toEqual([]);
  });
  it("rejects three-column choice grids and accepts two columns (R-UI-30)", () => {
    const { window, audit } = productAudit('<div class="skin-grid"><div></div><div></div><div></div></div>');
    expect(audit().productRules.map(item => item.rule)).toContain('R-UI-30');
    window.document.querySelector('.skin-grid').lastElementChild.getBoundingClientRect = () => ({ left: 10, top: 50, right: 110, bottom: 80, width: 100, height: 30 });
    expect(audit().productRules).toEqual([]);
  });
  it("rejects icon surfaces, incorrect glyph sizing and off-center icons (R-UI-16/19)", () => {
    const { window, audit } = productAudit('<button class="btn icon" style="border:0;background:gray"><svg class="i"></svg></button>');
    const button = window.document.querySelector('button'), glyph = button.firstElementChild;
    glyph.getBoundingClientRect = () => ({ left: 10, top: 10, right: 24, bottom: 24, width: 14, height: 14 });
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-16', 'R-UI-19', 'R-UI-19']);
    button.style.background = 'transparent';
    glyph.getBoundingClientRect = () => ({ left: 52, top: 17, right: 68, bottom: 33, width: 16, height: 16 });
    expect(audit().productRules).toEqual([]);
  });
  it("measures glyphs in a zoomed board at their unzoomed size (R-UI-19)", () => {
    const { window, audit } = productAudit('<div class="team-board" style="zoom:1.1"><button class="btn icon" style="border:0;background:transparent"><svg class="i"></svg></button></div>');
    const glyph = window.document.querySelector('svg');
    glyph.getBoundingClientRect = () => ({ left: 51.2, top: 16.2, right: 68.8, bottom: 33.8, width: 17.6, height: 17.6 });
    expect(audit().productRules).toEqual([]);
    glyph.getBoundingClientRect = () => ({ left: 52.3, top: 17.3, right: 67.7, bottom: 32.7, width: 15.4, height: 15.4 });
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-19']);
  });
  it.each([0.5, 0.8, 1.1, 1.5])("measures transformed canvas glyphs at scale %s without allowing wrong sizes (R-UI-19)", scale => {
    const { window, audit } = productAudit(`<div class="team-board" style="transform:matrix(${scale},0,0,${scale},60,-40)"><button class="btn icon" style="border:0;background:transparent"><svg class="i"></svg></button></div>`);
    const glyph = window.document.querySelector('svg');
    const rect = size => ({ left: 60 - size / 2, top: 25 - size / 2, right: 60 + size / 2, bottom: 25 + size / 2, width: size, height: size });
    glyph.getBoundingClientRect = () => rect(16 * scale);
    expect(audit().productRules).toEqual([]);
    glyph.getBoundingClientRect = () => rect(14 * scale);
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-19']);
    window.document.querySelector('.team-board').style.transform = `translate(60px, -40px) scale(${scale})`;
    glyph.getBoundingClientRect = () => rect(16 * scale);
    expect(audit().productRules).toEqual([]);
  });
  it("rejects low-contrast selected dates, including hover overrides (R-UI-22)", () => {
    const { window, audit } = productAudit('<div class="au-month-grid"><button class="btn" aria-pressed="true" style="border:0;color:rgb(30,30,30);background:rgb(40,40,40)">28</button></div>');
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-22']);
    window.document.querySelector('button').style.color = 'white';
    expect(audit().productRules).toEqual([]);
  });
  it("rejects repeated settings subtitles and wrapped shortcut rows (R-UI-02/17)", () => {
    const { window, audit } = productAudit('<div class="modal-body"><p class="purpose">Settings description</p></div><div class="page-overview"><div class="kc-row"><span class="kc-name">Ask</span><span class="kc-keys">Right Alt + Space</span></div></div>');
    window.document.querySelector('.kc-keys').getBoundingClientRect = () => ({ left: 10, top: 45, right: 110, bottom: 75, width: 100, height: 30 });
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-02', 'R-UI-17']);
  });
  it("keeps a gap between the model source scrollport and its footer action (R-UI-17)", () => {
    const { window, audit } = productAudit('<aside><div class="mc-source-list"></div><button class="btn" style="border:1px solid gray">Connect</button></aside>');
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-17']);
    window.document.querySelector('button').getBoundingClientRect = () => ({ left: 10, top: 48, right: 110, bottom: 78, width: 100, height: 30 });
    expect(audit().productRules).toEqual([]);
  });
  it("rejects a clipped selected model source (R-UI-01)", () => {
    const { window, audit } = productAudit('<div class="mc-source-list"><button class="btn mc-source" aria-pressed="true">OpenRouter</button></div>');
    const selected = window.document.querySelector('button');
    selected.getBoundingClientRect = () => ({ left: 10, top: 30, right: 110, bottom: 60, width: 100, height: 30 });
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-01']);
    selected.getBoundingClientRect = () => ({ left: 10, top: 10, right: 110, bottom: 40, width: 100, height: 30 });
    expect(audit().productRules).toEqual([]);
  });
  it("rejects small onboarding headlines and narrow primary actions (R-UI-37)", () => {
    const { window, audit } = productAudit('<div class="ob-col"><h2 style="font-size:20px">Welcome</h2></div><div class="ob-actions"><button class="btn pri" style="border:1px solid gray">Start</button></div>');
    window.document.querySelector('.ob-actions').getBoundingClientRect = () => ({ left: 10, top: 10, right: 410, bottom: 40, width: 400, height: 30 });
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-37', 'R-UI-37']);
    window.document.querySelector('h2').style.fontSize = '28px';
    window.document.querySelector('button').getBoundingClientRect = () => ({ left: 10, top: 10, right: 410, bottom: 40, width: 400, height: 30 });
    expect(audit().productRules).toEqual([]);
  });
  it("requires a vertical onboarding ability checklist (R-UI-35)", () => {
    const { window, audit } = productAudit('<div class="ob-abilities"><button class="btn" style="border:1px solid gray">Dictate</button><button class="btn" style="border:1px solid gray">Ask</button></div>');
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-35']);
    window.document.querySelector('.ob-abilities').lastElementChild.getBoundingClientRect = () => ({ left: 10, top: 50, right: 110, bottom: 80, width: 100, height: 30 });
    expect(audit().productRules).toEqual([]);
  });
  it("rejects duplicate custom and native tooltips", () => {
    const { audit } = productAudit('<button data-tooltip="Save" title="Save">Save</button><button data-tooltip="Open"><svg><title>Open</title></svg></button><button title="Native">Native</button>');
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-26', 'R-UI-26']);
  });
  it("rejects visible row destruction but permits overflow menus", () => {
    const { audit } = productAudit('<div class="mp-resource"><button class="danger">Delete</button></div><div role="menu"><div class="mp-resource"><button class="danger">Delete</button></div></div><div class="mp-resource" hidden><button class="danger">Delete</button></div>');
    expect(audit().productRules.map(item => item.rule)).toEqual(['R-UI-11']);
  });
  it("rejects a ⋯ that a right click on its row does not reach (R-UI-44)", () => {
    const { window, audit } = productAudit('<div class="row"><button class="btn icon" data-overflow aria-label="更多"></button></div>');
    expect(audit().productRules).toEqual([]);
    const orphan = { tag: "button", id: "", class: "btn icon", text: "更多" };
    const result = auditDocument(window.document, { width: 1120, height: 900 }, [], null, [], [orphan]);
    expect(result.productRules.map(item => item.rule)).toEqual(["R-UI-44"]);
    expect(failedChecks({ ...result, runtimeErrors: [] })).toBe(true);
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
  it.each(scenes.filter(scene => scene.name.endsWith("composer-learn")))("keeps $name correction data distinct from untranslated UI", scene => {
    const window = new Window(), { before, after } = scene.fixture.dictationText;
    expect(scene.steps.find(step => step.action === "dictation-paste").text).toBe(before);
    expect(scene.steps.find(step => step.action === "type").text).toBe(after);
    window.document.body.innerHTML = `<p>${after}</p><p>OpenMausBot</p><button>Message</button>`;
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    const allowed = Object.values(scene.fixture.dictationText).flatMap(text => text.match(/[A-Za-z]{2,}/g) ?? []).map(word => word.toLowerCase());
    expect(auditDocument(window.document, { width: 1120, height: 900 }, [], allowed).mixedLanguage.map(item => item.word)).toEqual(["Message"]);
  });
  it.each(["# Heading", "## Heading", "Text\n  ### Heading", "- [ ] Pending", "**bold**", "```js\ncode\n```"])("rejects visible raw Markdown: %s", text => {
    const window = new Window();
    const paragraph = window.document.createElement("p");
    paragraph.textContent = text; window.document.body.append(paragraph);
    paragraph.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    const result = { ...auditDocument(window.document, { width: 1120, height: 900 }), runtimeErrors: [] };
    expect(result.rawMarkdown).toHaveLength(1);
    expect(result.rawMarkdown[0].text).toBe(text);
    expect(failedChecks(result)).toBe(true);
  });
  it("ignores rendered formatting, ordinary hashes, hidden text and Markdown outside a scrollport", () => {
    const window = new Window();
    window.document.body.innerHTML = '<h3>Conclusion</h3><p><strong>Owner</strong>: next step, issue #123, color #fff</p><div hidden>### Hidden</div><div style="overflow-y:auto"><p id="offscreen">**older**</p></div>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    window.document.querySelector("#offscreen").getBoundingClientRect = () => ({ left: 10, top: 200, right: 100, bottom: 230, width: 90, height: 30 });
    expect(auditDocument(window.document, { width: 1120, height: 900 }).rawMarkdown).toEqual([]);
  });
  it("permits Markdown inside an editor field but still rejects it in the text beside the field", () => {
    const window = new Window();
    window.document.body.innerHTML = '<textarea id="skill-text">---\nname: weekly\n---\n# Weekly\n\n- [ ] one\n**bold**</textarea><p>## Heading</p>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    expect(auditDocument(window.document, { width: 1120, height: 900 }).rawMarkdown.map(item => item.text)).toEqual(["## Heading"]);
  });
  it("does not translate user-entered textarea data but still audits its UI attributes", () => {
    const window = new Window();
    window.document.body.innerHTML = '<textarea placeholder="Message">C:/Work/Team notes</textarea><textarea aria-label="名稱">User supplied text</textarea>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    expect(auditDocument(window.document, { width: 1120, height: 900 }, [], []).mixedLanguage.map(item => item.word)).toEqual(["Message"]);
  });
  it("allows complete product names without allowing their ordinary English words", () => {
    const window = new Window();
    window.document.body.innerHTML = '<p>Claude Code</p><p>claude code</p><p>Code required</p><p>Claude Coder</p><p>KEY=value</p><p>value required</p>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    expect(auditDocument(window.document, { width: 1120, height: 900 }, [], ["claude", "claude code", "key=value"]).mixedLanguage.map(item => item.word)).toEqual(["Code", "required", "Coder", "value"]);
  });
  it("allows the exact external Composio menu path while rejecting English settings labels", () => {
    const window = new Window();
    window.document.body.innerHTML = '<p>Composio「Settings → Project Settings → API Keys」</p><button>Settings</button><button>API Keys</button>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    expect(auditDocument(window.document, { width: 1120, height: 900 }, [], LATIN_NAMES.map(name => name.toLowerCase())).mixedLanguage.map(item => item.word)).toEqual(["Settings", "Keys"]);
  });
  it("allows the exact command-template token while rejecting input used as English prose", () => {
    const window = new Window();
    window.document.body.innerHTML = '<textarea placeholder="指令範本；以 {input} 代入後續文字"></textarea><p>input required</p>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    expect(auditDocument(window.document, { width: 1120, height: 900 }, [], []).mixedLanguage.map(item => item.word)).toEqual(["input", "required"]);
  });
  it("allows protocol names while still rejecting English prose beside technical examples", () => {
    const window = new Window();
    window.document.body.innerHTML = '<textarea placeholder="Authorization=Bearer ..."></textarea><textarea placeholder=\'{"mcpServers": {}}\'></textarea><p>Send request</p>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    const result = auditDocument(window.document, { width: 1120, height: 900 }, [], ["authorization", "bearer", "mcpservers"]);
    expect(result.mixedLanguage.map(item => item.word)).toEqual(["Send", "request"]);
  });
  it("rejects interface copy left in English in a Chinese window, allowing data and product names", () => {
    const window = new Window();
    window.document.body.innerHTML = '<textarea placeholder="Message 紫苑"></textarea><button aria-label="問拉斐爾">Google Calendar</button><p>Finance 已建立</p><a href="#">研究訪談摘要.pdf</a>';
    for (const element of window.document.querySelectorAll("*")) element.getBoundingClientRect = () => ({ left: 10, top: 10, right: 100, bottom: 40, width: 90, height: 30 });
    const result = { ...auditDocument(window.document, { width: 1120, height: 900 }, [], ["google", "calendar", "finance"]), runtimeErrors: [] };
    expect(result.mixedLanguage.map(item => item.word)).toEqual(["Message"]);
    expect(failedChecks(result)).toBe(true);
    expect(auditDocument(window.document, { width: 1120, height: 900 }, []).mixedLanguage).toEqual([]);
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


it("rejects divergent store add classes, row markup and card styling", () => {
  const browser = new Window();
  const row = `<div class="cs-card" style="display:flex;border-radius:0px;border-bottom:1px solid;border-left:0px solid"><button class="btn cs-main" data-conn="open-store"><span class="ic"></span><span class="tx"><span class="nm">App</span><span class="sub">Summary</span></span></button><button class="btn sm" data-conn="join">Add</button></div>`;
  browser.document.body.innerHTML = row + row;
  const previous = { document: globalThis.document, window: globalThis.window };
  globalThis.document = browser.document; globalThis.window = browser;
  try {
    expect(checkRendered("plugins-store-rows")).toEqual({ check: "plugins-store-rows", rows: 2, addClass: "btn sm" });
    const second = browser.document.querySelectorAll('.cs-card')[1];
    const add = second.querySelector('[data-conn="join"]');
    add.classList.add('pri');
    expect(() => checkRendered("plugins-store-rows")).toThrow("same outlined class list");
    add.classList.remove('pri');
    const main = second.querySelector('.cs-main');
    const oldMain = browser.document.createElement('div'); oldMain.className = 'cs-main';
    oldMain.innerHTML = main.innerHTML; main.replaceWith(oldMain);
    expect(() => checkRendered("plugins-store-rows")).toThrow("same detail button");
    oldMain.replaceWith(main); second.style.display = 'grid';
    expect(() => checkRendered("plugins-store-rows")).toThrow("same compact row and divider");
  } finally { globalThis.document = previous.document; globalThis.window = previous.window; browser.happyDOM.abort(); }
});
