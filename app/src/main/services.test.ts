// @vitest-environment happy-dom
// 服務 in Settings › 拉斐爾: the clean list of capabilities, the switch of each, the dialog of a
// capability with the list of its services and the page of one service, and what its buttons and
// fields send. The core is faked; a key typed here must leave the field the moment it is sent, and
// be drawn nowhere.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";

const api = vi.hoisted(() => ({ servicesStatus: vi.fn(), servicesSetKey: vi.fn(), servicesClearKey: vi.fn(), ttsPreview: vi.fn() }));
vi.mock("./api", () => api);
vi.mock("./ui", async (original) => ({ ...(await original<typeof import("./ui")>()), toaster: { info: vi.fn() } }));

import {
  type Capability,
  type ServicesView,
  canTurnOn,
  createServices,
  emptyServicesView,
  isOn,
  isServiceUrl,
  isVoiceId,
  keyInputId,
  serviceRows,
  servicesHTML,
} from "./services";
import type { BrainSettings, ServicesStatus } from "./types";
import { ico, toaster } from "./ui";

interface Over {
  search?: string;
  read?: string;
  braveKey?: boolean;
  jinaKey?: boolean;
  urlSet?: boolean;
  fishKey?: boolean;
  voiceSet?: boolean;
  model?: string;
  sees?: boolean;
  enabled?: Partial<Record<Capability, boolean>>;
}

/** A status of the core: Brave chosen for the search, with no key; Jina for the pages; PDFs read here; Fish Audio with nothing set yet. */
function status(over: Over = {}): ServicesStatus {
  const on = (kind: Capability, fallback = true): boolean => over.enabled?.[kind] ?? fallback;
  return {
    search: {
      enabled: on("search"),
      service: over.search ?? "brave",
      options: [
        { id: "openrouter", needs_key: false, has_key: false, key_optional: false },
        { id: "brave", needs_key: true, has_key: over.braveKey ?? false, key_optional: false },
        { id: "tavily", needs_key: true, has_key: false, key_optional: false },
        { id: "exa", needs_key: true, has_key: false, key_optional: false },
        { id: "searxng", needs_key: false, has_key: false, key_optional: false, url_set: over.urlSet ?? false },
      ],
    },
    read: {
      enabled: on("read"),
      service: over.read ?? "jina",
      options: [
        { id: "builtin", needs_key: false, has_key: false, key_optional: false },
        { id: "jina", needs_key: false, has_key: over.jinaKey ?? false, key_optional: true },
        { id: "firecrawl", needs_key: true, has_key: false, key_optional: false },
        { id: "tavily", needs_key: true, has_key: false, key_optional: false },
      ],
    },
    pdf: {
      enabled: on("pdf"),
      service: "builtin",
      options: [
        { id: "builtin", needs_key: false, has_key: false, key_optional: false },
        { id: "openrouter", needs_key: false, has_key: false, key_optional: false },
      ],
    },
    voice: {
      enabled: on("voice", false),
      service: "fish",
      options: [{ id: "fish", needs_key: true, has_key: over.fishKey ?? false, key_optional: false }],
      fish_voice_set: over.voiceSet ?? false,
    },
    vision: { enabled: on("vision"), model: over.model ?? "", resolved_model: over.model || "openai/gpt-6-luna", supports_images: over.sees ?? true },
  };
}

test("every service row has its brand mark or a meaningful local icon, never an initial", () => {
  const brands = ["openrouter", "brave", "tavily", "exa", "searxng", "jina", "firecrawl", "fish"];
  const seen = new Set<string>();
  for (const kind of ["search", "read", "pdf", "voice", "vision"] as const) {
    const host = document.createElement("div");
    host.innerHTML = dialog(kind);
    for (const row of serviceRows(kind, status(), BRAIN)) {
      const picture = host.querySelector(`[data-service="${row.id}"] .ic`)!;
      expect(picture.querySelector("svg, img"), row.id).not.toBeNull();
      expect(picture.textContent, row.id).toBe("");
      if (brands.includes(row.id)) {
        expect(row.mark, row.id).toBe(row.id);
        seen.add(row.id);
      } else {
        const expected = ico(row.id === "builtin" ? "monitor" : row.id === "same" ? "sparkle" : "bot");
        expect(picture.querySelector("path")?.getAttribute("d"), row.id).toBe(/d="([^"]+)"/.exec(expected)![1]);
      }
    }
  }
  expect([...seen].sort()).toEqual(brands.sort());
});

const PICKER = '<span class="mp-slot" data-model-picker="vision"></span>';
const VOICE_ID = "0123456789abcdef0123456789abcdef";
const BRAIN = { agent_model: "openai/gpt-6-luna", searxng_url: "https://searx.example", tts_fish_voice: VOICE_ID } as BrainSettings;
const view = (over: Partial<ServicesView> = {}): ServicesView => ({ ...emptyServicesView(), status: status(), ...over });
const draw = (over: Partial<ServicesView> = {}, brain: BrainSettings | null = BRAIN): string => servicesHTML(view(over), PICKER, brain);
const dialog = (kind: Capability, service: string | null = null, over: Partial<ServicesView> = {}, brain: BrainSettings | null = BRAIN): string =>
  draw({ open: { kind, service }, ...over }, brain);

/** What a sighted reader sees: the text outside the tags. */
const visible = (html: string): string => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const buttons = (html: string): string[] => [...html.matchAll(/<button\b[^>]*>/g)].map((m) => m[0]);
const flush = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};
/** The part of the page from the dialog on. */
const inDialog = (html: string): string => html.slice(html.indexOf('<div class="cs-scrim"'));
/** The part of the page that is the section: before the dialog. */
const inSection = (html: string): string => (html.includes('<div class="cs-scrim"') ? html.slice(0, html.indexOf('<div class="cs-scrim"')) : html);

test("a service added by a newer core gets a plug instead of an initial", () => {
  const newer = status();
  newer.search.options.push({ id: "future-search", needs_key: false, has_key: false, key_optional: false });
  const host = document.createElement("div");
  host.innerHTML = dialog("search", null, { status: newer });
  const picture = host.querySelector('[data-service="future-search"] .ic')!;
  expect(picture.textContent).toBe("");
  expect(picture.querySelector("path")?.getAttribute("d")).toBe(/d="([^"]+)"/.exec(ico("plug"))![1]);
});

beforeEach(() => {
  document.body.innerHTML = "";
  for (const fn of Object.values(api)) fn.mockReset();
  vi.mocked(toaster.info).mockReset();
  // no movement is asked for: a dialog goes at once
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
});
afterEach(() => {
  setLocale("zh-TW");
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("the list", () => {
  const html = draw();
  const rows = (out: string): string[] => out.split('<div class="cs-item svc-item').slice(1);

  test("is titled 服務, with one rounded list of five rows in order, as 「連接」 draws its own", () => {
    expect(html).toMatch(/^<section class="sgroup svc" data-svc-section><h2>服務<\/h2><div class="cs-list" data-stagger="svc">/);
    expect(rows(html)).toHaveLength(5);
    const names = [...html.matchAll(/<span class="nm">(.*?)<\/span>/g)].map((m) => m[1]);
    expect(names).toEqual(["搜尋網路", "讀網頁", "看圖片", "讀 PDF", "拉斐爾的聲音"]);
  });

  test("a row is an icon, the name and a muted line, the service in use, a switch and a chevron; the row opens the dialog", () => {
    const [search] = rows(html);
    expect(search).toMatch(/^[^>]*data-svc="open" data-id="search">/);
    expect(search).toMatch(/<button type="button" class="btn cs-open" data-svc="open" data-id="search" data-k="svc-open:search" aria-haspopup="dialog" title="查看 搜尋網路">/);
    expect(search).toMatch(/<span class="ic svc-ic" aria-hidden="true"><svg/);
    expect(search).toContain('<span class="ds">查詢最新的資料</span>');
    expect(search).toContain('<span class="svc-now">Brave</span>');
    expect(search).toMatch(/class="tg" role="switch" aria-checked="true" aria-label="開啟 搜尋網路" data-svc="toggle" data-id="search" data-k="svc-toggle:search"/);
    // the chevron is a picture of where the row goes, not a second button
    expect(search).toMatch(/<span class="chev" aria-hidden="true"><svg/);
    expect(search.match(/<button/g)).toHaveLength(2);
  });

  test("says which service is in use for each, small and muted", () => {
    const now = [...html.matchAll(/<span class="svc-now">(.*?)<\/span>/g)].map((m) => m[1]);
    expect(now).toEqual(["Brave", "Jina", "與回答相同", "內建", "Fish Audio"]);
    expect(draw({ status: status({ model: "anthropic/claude-haiku-5.5" }) })).toContain('<span class="svc-now">claude-haiku-5.5</span>');
  });

  test("is clean: no dropdown, no field, no text button, no helper paragraph, and no key or address anywhere", () => {
    const section = inSection(draw({}, { ...BRAIN, searxng_url: "https://searx.example/secret" }));
    expect(section).not.toContain("<input");
    expect(section).not.toContain('class="sel');
    expect(section).not.toContain('class="btn"');
    expect(section).not.toMatch(/<p[ >]/);
    expect(section).not.toContain("secret");
    expect(section).not.toContain("data-model-picker");
    for (const tag of buttons(section)) expect(tag).toMatch(/class="(btn cs-open|tg)"/);
    expect(section).not.toContain('role="dialog"');
  });

  test("the switches show what is on: the core's word, and the settings' where the window has them", () => {
    const states = (out: string): string[] => [...out.matchAll(/aria-checked="(true|false)"/g)].map((m) => m[1]);
    expect(states(html)).toEqual(["true", "true", "true", "true", "false"]); // the voice is off until it is turned on
    expect(states(draw({ status: status({ enabled: { search: false, pdf: false, voice: true } }) }))).toEqual(["false", "true", "true", "false", "true"]);
    // the settings are the freshest: a switch just turned shows at once, before the status is read again
    expect(states(draw({}, { ...BRAIN, web_read_enabled: false, tts_enabled: true }))).toEqual(["true", "false", "true", "true", "true"]);
    expect(draw({ status: status({ enabled: { search: false } }) })).toContain('svc-item off" data-svc="open" data-id="search"');
  });

  test("while the core has not said, the rows are there, from the settings, and the switches wait", () => {
    const loading = servicesHTML(emptyServicesView(), PICKER, { ...BRAIN, web_search_service: "tavily", tts_enabled: true });
    expect(loading).toContain('<span class="svc-now">Tavily</span>');
    expect([...loading.matchAll(/class="tg"[^>]*>/g)].every((m) => m[0].includes(" disabled"))).toBe(true);
    expect(visible(servicesHTML({ ...emptyServicesView(), status: "failed" }, PICKER, null))).toContain("無法讀取服務設定");
  });

  test("is named in the three languages", () => {
    setLocale("en");
    expect(draw()).toContain("<h2>Services</h2>");
    expect([...draw().matchAll(/<span class="nm">(.*?)<\/span>/g)].map((m) => m[1])).toEqual(["Web search", "Read web pages", "Images", "PDFs", "Raphael&#39;s voice"]);
    expect(draw()).toContain('aria-label="Turn on Web search"');
    setLocale("ja");
    expect(draw()).toContain("<h2>サービス</h2>");
    expect(draw()).toContain('aria-label="ラファエルの声 をオンにする"');
  });

  test("never shows a key: whatever the status holds besides, no key is drawn", () => {
    const leaky = status({ braveKey: true }) as unknown as { search: { options: Record<string, unknown>[] } };
    leaky.search.options[1].key = "BSA-secret-key";
    const out = servicesHTML({ ...emptyServicesView(), status: leaky as unknown as ServicesStatus, open: { kind: "search", service: "brave" } }, PICKER, BRAIN);
    expect(out).not.toContain("BSA-secret-key");
  });
});

describe("what a capability stands on", () => {
  test("a service is usable when it has what it needs: a key, SearXNG's address, the voice's key and model", () => {
    const search = (over: Over) => serviceRows("search", status(over), BRAIN);
    expect(search({}).map((r) => [r.id, r.usable])).toEqual([["openrouter", true], ["brave", false], ["tavily", false], ["exa", false], ["searxng", false]]);
    expect(search({ braveKey: true, urlSet: true }).filter((r) => r.usable).map((r) => r.id)).toEqual(["openrouter", "brave", "searxng"]);
    expect(serviceRows("read", status(), BRAIN).find((r) => r.id === "jina")!.usable).toBe(true); // its key is optional
    const voice = (over: Over) => serviceRows("voice", status(over), BRAIN)[0].usable;
    expect([voice({}), voice({ fishKey: true }), voice({ voiceSet: true }), voice({ fishKey: true, voiceSet: true })]).toEqual([false, false, false, true]);
  });

  test("the vision has 「與回答相同」 and 「指定模型」, which is usable once a model is chosen and reads images", () => {
    expect(serviceRows("vision", status(), BRAIN).map((r) => [r.id, r.chosen, r.usable])).toEqual([["same", true, true], ["model", false, false]]);
    const chosen = serviceRows("vision", status({ model: "anthropic/claude-haiku-5.5" }), BRAIN);
    expect(chosen.map((r) => [r.id, r.chosen, r.usable])).toEqual([["same", false, true], ["model", true, true]]);
    expect(serviceRows("vision", status({ model: "x/blind", sees: false }), BRAIN)[1].usable).toBe(false);
  });

  test("the switch can turn on only what is usable", () => {
    expect(canTurnOn("search", status(), BRAIN)).toBe(false);
    expect(canTurnOn("search", status({ braveKey: true }), BRAIN)).toBe(true);
    expect(canTurnOn("voice", status({ fishKey: true }), BRAIN)).toBe(false);
    expect(canTurnOn("voice", status({ fishKey: true, voiceSet: true }), BRAIN)).toBe(true);
    expect(canTurnOn("pdf", status(), BRAIN)).toBe(true);
    expect(canTurnOn("search", null, BRAIN)).toBe(false);
  });

  test("on is the settings' word, else the core's, else the default (the voice is off)", () => {
    expect(isOn("search", null, null)).toBe(true);
    expect(isOn("voice", null, null)).toBe(false);
    expect(isOn("pdf", status({ enabled: { pdf: false } }), null)).toBe(false);
    expect(isOn("pdf", status({ enabled: { pdf: false } }), { ...BRAIN, pdf_enabled: true })).toBe(true);
  });
});

describe("the dialog of a capability", () => {
  test("opens over the list, which stays under it: a modal dialog named by the capability, that grows in once", () => {
    const html = dialog("search");
    expect(html).toContain("<section");
    expect(html).toContain('<div class="cs-scrim" data-svc="scrim" data-dialog="svc:search">');
    expect(html).toContain('<div class="cs-dialog" id="svc-dialog" role="dialog" aria-modal="true" aria-labelledby="svc-dlg-title" tabindex="-1" data-trap>');
    expect(inDialog(html)).toContain('<h2 class="cs-dlg-name" id="svc-dlg-title">搜尋網路</h2>');
    expect(draw()).not.toContain("cs-scrim");
  });

  test("has the icon and name, the same switch, and a borderless close button", () => {
    const d = inDialog(dialog("search"));
    expect(d).toContain('<div class="svc-view"><div class="cs-dlg-head"><span class="ic svc-ic xl" aria-hidden="true"><svg');
    expect(d).toMatch(/role="switch" aria-checked="true" aria-label="開啟 搜尋網路" data-svc="toggle" data-id="search" data-k="svc-dlg-toggle"/);
    expect(d).toMatch(/<button type="button" class="btn icon" aria-label="關閉" title="關閉" data-svc="close" data-k="svc-close"/);
  });

  test("lists the services under 「已安裝」, each a mark, a name, a note, a chevron; a check on the one in use only", () => {
    const d = inDialog(dialog("search"));
    expect(d).toContain('<h3 class="svc-h">已安裝</h3>');
    const rows = d.split('<div class="cs-item svc-srv"').slice(1);
    expect(rows.map((r) => /<span class="nm">(.*?)<\/span>/.exec(r)![1])).toEqual(["OpenRouter", "Brave", "Tavily", "Exa", "SearXNG"]);
    expect(rows.map((r) => /<span class="ds">(.*?)<\/span>/.exec(r)?.[1])).toEqual(["共用金鑰", "需要金鑰", "需要金鑰", "需要金鑰", "需要網址"]);
    expect(rows[0]).toMatch(/^ data-svc="service" data-service="openrouter">/);
    expect(rows[0]).toMatch(/<button type="button" class="btn cs-open" data-svc="service" data-service="openrouter" data-k="svc-svc:openrouter"/);
    expect(rows[0]).toContain('class="mono md logo-mono"');
    expect(rows[2]).toContain('class="mono md logo-art"');
    expect(rows[3]).toContain('class="mono md logo-art"'); // Exa has its own mark
    expect(rows.every((r) => /<span class="chev" aria-hidden="true"><svg/.test(r))).toBe(true);
    // the check is on Brave, the one in use
    expect(rows.map((r) => r.includes('role="img" aria-label="使用中"'))).toEqual([false, true, false, false, false]);
  });

  test("a note says 已設定 for a service that has its key, and 免金鑰 for Jina without one", () => {
    expect(inDialog(dialog("search", null, { status: status({ braveKey: true, urlSet: true }) }))).toMatch(/Brave<\/span><span class="ds">已設定<\/span>[\s\S]*SearXNG<\/span><span class="ds">已設定</);
    const read = inDialog(dialog("read"));
    expect(read).toMatch(/內建<\/span><span class="ds">本機</);
    expect(read).toMatch(/Jina<\/span><span class="ds">免金鑰</);
  });

  test("the pages, PDFs and voice list their own services", () => {
    const names = (kind: Capability): string[] => [...inDialog(dialog(kind)).matchAll(/class="cs-item svc-srv"[\s\S]*?<span class="nm">(.*?)<\/span>/g)].map((m) => m[1]);
    expect(names("read")).toEqual(["內建", "Jina", "Firecrawl", "Tavily"]);
    expect(names("pdf")).toEqual(["內建", "OpenRouter"]);
    expect(names("voice")).toEqual(["Fish Audio"]);
    expect(names("vision")).toEqual(["與回答相同", "指定模型"]);
    expect(inDialog(dialog("vision"))).toMatch(/與回答相同<\/span><span class="ds">gpt-6-luna<\/span>/);
  });

  test("is in the three languages", () => {
    setLocale("en");
    expect(inDialog(dialog("pdf"))).toContain("<h3 class=\"svc-h\">Installed</h3>");
    expect(inDialog(dialog("pdf"))).toContain("Built-in");
    setLocale("ja");
    expect(inDialog(dialog("pdf"))).toContain("インストール済み");
    expect(inDialog(dialog("pdf"))).toContain("内蔵");
  });

  test("while the core has not said, the dialog says so; a failure says so", () => {
    expect(visible(inDialog(dialog("search", null, { status: "loading" })))).toContain("讀取中");
    expect(visible(inDialog(dialog("search", null, { status: "failed" })))).toContain("無法讀取服務設定");
  });

  test("the way in and out of a page is drawn once, for the drawing that follows the change", () => {
    expect(inDialog(dialog("search", "brave", { slide: "in" }))).toContain('<div class="svc-view slide-in">');
    expect(inDialog(dialog("search", null, { slide: "out" }))).toContain('<div class="svc-view slide-out">');
    expect(inDialog(dialog("search", "brave"))).toContain('<div class="svc-view">');
  });
});

describe("the page of one service", () => {
  test("has a back chevron and a close button, both borderless, and the service's name", () => {
    const d = inDialog(dialog("search", "brave"));
    expect(d).toMatch(/<div class="cs-dlg-head sub"><button type="button" class="btn icon" aria-label="返回" title="返回" data-svc="back" data-k="svc-back">/);
    expect(d).toContain('<h2 class="cs-dlg-name" id="svc-dlg-title">Brave</h2>');
    expect(d).toMatch(/data-svc="close"/);
    expect(d).not.toContain("已安裝");
  });

  test("a service that needs a key and has none: a password field 「API 金鑰」 and a bordered 「儲存」", () => {
    const d = inDialog(dialog("search", "brave"));
    expect(d).toContain(`id="${keyInputId("brave")}" type="password" value="" placeholder="API 金鑰"`);
    expect(d).toContain('<button type="button" class="btn" data-svc="save" data-service="brave">儲存</button>');
  });

  test("a stored key: 「已設定」 in muted words and a bordered 「移除」, and no field", () => {
    const d = inDialog(dialog("search", "brave", { status: status({ braveKey: true }) }));
    expect(d).toContain('<div class="svc-line" data-svc-line="brave"><span class="val">已設定</span><button type="button" class="btn" data-svc="clear" data-service="brave">移除</button></div>');
    expect(d).not.toContain(keyInputId("brave"));
  });

  test("Jina's key is optional: its field says so", () => {
    expect(inDialog(dialog("read", "jina"))).toContain('placeholder="API 金鑰（選填）"');
    expect(inDialog(dialog("read", "jina", { status: status({ jinaKey: true }) }))).toContain('data-svc="clear" data-service="jina"');
  });

  test("a service that takes no key has no line; SearXNG has its address", () => {
    const keyless = inDialog(dialog("pdf", "openrouter"));
    expect(keyless).not.toContain("svc-line");
    const searx = inDialog(dialog("search", "searxng"));
    expect(searx).toContain('id="svc-url" type="text" value="https://searx.example"');
    expect(searx).toContain('data-svc="url" data-service="searxng"');
    expect(searx).not.toContain('type="password"');
  });

  test("Fish Audio: its key, then the voice model's field with 「儲存」, then 「使用」 and a bordered 「Try it」, text only", () => {
    const d = inDialog(dialog("voice", "fish"));
    expect(d).toContain(`id="${keyInputId("fish")}" type="password"`);
    expect(d).toContain(`id="svc-voice-id" type="text" value="${VOICE_ID}" placeholder="聲音模型 ID"`);
    expect(d.indexOf('data-svc-line="fish"')).toBeLessThan(d.indexOf('data-svc-line="voice"'));
    expect(d).toContain('<button type="button" class="btn" data-svc="try" data-service="fish" data-k="svc-try">Try it</button>');
    expect(d).toMatch(/<button type="button" class="btn" data-svc="use" data-service="fish" data-k="svc-use"[^>]*>使用<\/button>/);
    expect(d).not.toMatch(/data-svc="try"[^>]*><svg/);
  });

  test("Try it is English in every language; 「使用」 is Use in English", () => {
    for (const locale of ["en", "ja", "zh-TW"] as const) {
      setLocale(locale);
      expect(inDialog(dialog("voice", "fish"))).toContain(">Try it</button>");
    }
    setLocale("en");
    expect(inDialog(dialog("search", "brave"))).toContain(">Use</button>");
  });

  test("the voice model id is a setting, not a secret: it is drawn from the settings", () => {
    expect(inDialog(dialog("voice", "fish", {}, { ...BRAIN, tts_fish_voice: "" }))).toContain('id="svc-voice-id" type="text" value=""');
  });

  test("「使用」 is disabled while the service is in use (and the capability on) or cannot be used", () => {
    const use = (html: string): string => /<button[^>]*data-svc="use"[^>]*>/.exec(inDialog(html))![0];
    // Brave has no key: not usable
    expect(use(dialog("search", "brave"))).toContain(" disabled");
    // Brave with a key, in use and on: in use
    expect(use(dialog("search", "brave", { status: status({ braveKey: true }) }))).toContain(" disabled");
    // Brave with a key but the search is off: it can be used (turns it on)
    expect(use(dialog("search", "brave", { status: status({ braveKey: true, enabled: { search: false } }) }))).not.toContain(" disabled");
    // OpenRouter needs nothing: it can be chosen
    expect(use(dialog("search", "openrouter"))).not.toContain(" disabled");
    // while something is on its way nothing is pressed twice
    expect(use(dialog("search", "openrouter", { busy: "switch" }))).toContain(" disabled");
  });

  test("看圖片's pages: 「與回答相同」 has 「使用」; 「指定模型」 has the picker and no field", () => {
    const same = inDialog(dialog("vision", "same", { status: status({ model: "anthropic/claude-haiku-5.5" }) }));
    expect(same).toMatch(/data-svc="use" data-service="same"[^>]*>使用</);
    expect(same).not.toContain("data-model-picker");
    const model = inDialog(dialog("vision", "model"));
    expect(model).toContain(`<div class="svc-pick">${PICKER}</div>`);
    expect(model).not.toContain("<input");
    expect(model).toContain('<h2 class="cs-dlg-name" id="svc-dlg-title">指定模型</h2>');
  });

  test("only real buttons: a bordered text button or a borderless icon one", () => {
    for (const d of [dialog("search", "brave"), dialog("voice", "fish"), dialog("search", "searxng")]) {
      for (const tag of buttons(inDialog(d))) expect(tag).toMatch(/class="btn( icon)?"/);
    }
  });
});

describe("what the section does", () => {
  /** The panel as settings.ts holds it: the settings change as `changeSetting` is called, and the page is drawn from them. */
  function mount(settings: Partial<BrainSettings> = {}, changeSetting?: (key: keyof BrainSettings, value: string | boolean) => Promise<void>) {
    const host = document.createElement("div");
    document.body.append(host);
    const brain: BrainSettings = { ...BRAIN, ...settings };
    const change = vi.fn(async (key: keyof BrainSettings, value: string | boolean) => {
      if (changeSetting) await changeSetting(key, value);
      (brain as unknown as Record<string, unknown>)[key] = value;
    });
    const panel = createServices({
      visible: () => true,
      redraw: () => {
        // the panel's drawing, as settings.ts does it: the HTML anew (a field's draft is not kept here)
        host.innerHTML = servicesHTML(panel.view(), PICKER, brain);
      },
      brain: () => brain,
      changeSetting: change,
    });
    host.addEventListener("click", (e) => panel.click(e.target as Element));
    host.addEventListener("keydown", (e) => panel.key(e));
    return { host, panel, brain, changeSetting: change };
  }
  const press = (host: HTMLElement, selector: string): void => host.querySelector<HTMLElement>(selector)!.click();
  const dlg = (host: HTMLElement): HTMLElement | null => host.querySelector("#svc-dialog");
  const titleOf = (host: HTMLElement): string => host.querySelector("#svc-dlg-title")?.textContent ?? "";
  /** The dialog opened at `kind`, with the core's status read. */
  async function opened(kind: Capability, over: Over = {}, settings: Partial<BrainSettings> = {}) {
    api.servicesStatus.mockResolvedValue(status(over));
    const m = mount(settings);
    await m.panel.load();
    press(m.host, `[data-k="svc-open:${kind}"]`);
    return m;
  }

  test("reads the status and draws the list", async () => {
    api.servicesStatus.mockResolvedValue(status());
    const { host, panel } = mount();
    await panel.load();
    expect(host.querySelectorAll(".cs-item.svc-item")).toHaveLength(5);
    expect(dlg(host)).toBeNull();
  });

  test("the row opens the dialog, by its name or by its chevron or anywhere on it but the switch", async () => {
    api.servicesStatus.mockResolvedValue(status());
    const { host, panel } = mount();
    await panel.load();
    press(host, '[data-k="svc-open:read"]');
    expect(titleOf(host)).toBe("讀網頁");
    // a click on the row's chevron or its muted words opens it too
    const again = mount();
    await again.panel.load();
    again.host.querySelector<HTMLElement>('.cs-item[data-id="pdf"] .chev')!.click();
    expect(titleOf(again.host)).toBe("讀 PDF");
    const third = mount();
    await third.panel.load();
    third.host.querySelector<HTMLElement>('.cs-item[data-id="vision"] .svc-now')!.click();
    expect(titleOf(third.host)).toBe("看圖片");
  });

  test("a click on a service opens its page, the back chevron returns to the list with the focus on the service", async () => {
    const { host } = await opened("search");
    expect(host.querySelector(".svc-srv")).not.toBeNull();
    press(host, '[data-k="svc-svc:brave"]');
    expect(titleOf(host)).toBe("Brave");
    expect(host.querySelector(".svc-view")!.className).toBe("svc-view slide-in"); // in the one drawing that follows the click
    expect(host.querySelector(".svc-srv")).toBeNull();
    press(host, '[data-k="svc-back"]');
    expect(titleOf(host)).toBe("搜尋網路");
    expect(host.querySelectorAll(".svc-srv")).toHaveLength(5);
    expect(document.activeElement).toBe(host.querySelector('[data-k="svc-svc:brave"]') ?? document.body);
  });

  test("the page of a service slides in once: the drawing that follows the click has the movement", async () => {
    api.servicesStatus.mockResolvedValue(status());
    const drawn: string[] = [];
    const host = document.createElement("div");
    document.body.append(host);
    const panel = createServices({
      visible: () => true,
      redraw: () => {
        drawn.push(servicesHTML(panel.view(), PICKER, BRAIN));
        host.innerHTML = drawn[drawn.length - 1]!;
      },
      brain: () => BRAIN,
      changeSetting: vi.fn(),
    });
    host.addEventListener("click", (e) => panel.click(e.target as Element));
    await panel.load();
    press(host, '[data-k="svc-open:search"]');
    press(host, '[data-k="svc-svc:brave"]');
    expect(drawn[drawn.length - 1]).toContain("svc-view slide-in");
    press(host, '[data-k="svc-back"]');
    expect(drawn[drawn.length - 1]).toContain("svc-view slide-out");
    press(host, '[data-k="svc-svc:brave"]');
    host.innerHTML = servicesHTML(panel.view(), PICKER, BRAIN);
    expect(host.innerHTML).not.toContain("slide-");
  });

  test("× closes the dialog and the focus goes back to the row", async () => {
    const { host } = await opened("search");
    press(host, '[data-svc="close"]');
    expect(dlg(host)).toBeNull();
    expect(document.activeElement).toBe(host.querySelector('[data-k="svc-open:search"]') ?? document.body);
  });

  test("Esc closes it, from the list and from the page of a service", async () => {
    const { host } = await opened("search");
    host.querySelector("#svc-dialog")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    expect(dlg(host)).toBeNull();
    press(host, '[data-k="svc-open:read"]');
    press(host, '[data-k="svc-svc:jina"]');
    host.querySelector("#svc-dialog")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    expect(dlg(host)).toBeNull();
    // with nothing open Esc is left to the settings
    const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
    host.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  test("a click on the dimmed part closes it; a click inside, on no control, does not", async () => {
    const { host } = await opened("search");
    host.querySelector<HTMLElement>(".svc-h")!.click();
    expect(dlg(host)).not.toBeNull();
    host.querySelector<HTMLElement>(".cs-scrim")!.click();
    expect(dlg(host)).toBeNull();
  });

  test("it goes with a movement when movement is allowed: the way out is played, then the dialog is gone", async () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    vi.useFakeTimers();
    api.servicesStatus.mockResolvedValue(status());
    const { host, panel } = mount();
    await panel.load();
    press(host, '[data-k="svc-open:search"]');
    press(host, '[data-svc="close"]');
    expect(host.querySelector(".cs-scrim")!.classList.contains("leaving")).toBe(true);
    press(host, '[data-svc="close"]'); // a second close waits for the first
    await vi.advanceTimersByTimeAsync(150);
    expect(dlg(host)).toBeNull();
  });

  test("the switch turns a capability off and on again, sending the setting, and reads the status again", async () => {
    api.servicesStatus.mockResolvedValue(status({ read: "builtin" }));
    const { host, panel, changeSetting } = mount();
    await panel.load();
    press(host, '[data-k="svc-toggle:read"]');
    await flush();
    expect(changeSetting).toHaveBeenLastCalledWith("web_read_enabled", false);
    expect(host.querySelector('[data-k="svc-toggle:read"]')!.getAttribute("aria-checked")).toBe("false");
    expect(dlg(host)).toBeNull(); // the switch does not open the dialog
    press(host, '[data-k="svc-toggle:read"]');
    await flush();
    expect(changeSetting).toHaveBeenLastCalledWith("web_read_enabled", true);
    expect(host.querySelector('[data-k="svc-toggle:read"]')!.getAttribute("aria-checked")).toBe("true");
    expect(api.servicesStatus).toHaveBeenCalledTimes(3);
  });

  test("every switch is its own setting", async () => {
    api.servicesStatus.mockResolvedValue(status({ search: "openrouter", braveKey: true }));
    const { host, panel, changeSetting } = mount();
    await panel.load();
    for (const kind of ["search", "read", "vision", "pdf"] as const) {
      press(host, `[data-k="svc-toggle:${kind}"]`);
      await flush();
    }
    expect(changeSetting.mock.calls.map((c) => c[0])).toEqual(["web_search_enabled", "web_read_enabled", "vision_enabled", "pdf_enabled"]);
    expect(changeSetting.mock.calls.every((c) => c[1] === false)).toBe(true);
  });

  test("the switch of a capability that cannot be used yet opens the dialog on that service's settings instead", async () => {
    api.servicesStatus.mockResolvedValue(status({ enabled: { search: false } })); // Brave chosen, no key
    const { host, panel, changeSetting } = mount();
    await panel.load();
    press(host, '[data-k="svc-toggle:search"]');
    await flush();
    expect(changeSetting).not.toHaveBeenCalled();
    expect(titleOf(host)).toBe("Brave");
    expect(host.querySelector(`#${keyInputId("brave")}`)).not.toBeNull();
    // the voice: Fish Audio has no key and no voice model yet
    press(host, '[data-svc="close"]');
    press(host, '[data-k="svc-toggle:voice"]');
    expect(titleOf(host)).toBe("Fish Audio");
    expect(changeSetting).not.toHaveBeenCalled();
  });

  test("the switch in the dialog does the same, in the dialog", async () => {
    const { host, changeSetting } = await opened("search", { enabled: { search: false } });
    press(host, '[data-k="svc-dlg-toggle"]');
    await flush();
    expect(titleOf(host)).toBe("Brave"); // not usable: the page of the service in use
    expect(changeSetting).not.toHaveBeenCalled();
    const usable = await opened("pdf", { enabled: { pdf: false } });
    press(usable.host, '[data-k="svc-dlg-toggle"]');
    await flush();
    expect(usable.changeSetting).toHaveBeenCalledWith("pdf_enabled", true);
    expect(titleOf(usable.host)).toBe("讀 PDF"); // it stays in the dialog
  });

  test("「使用」 makes the service the one in use and turns the capability on", async () => {
    const { host, changeSetting } = await opened("search", { enabled: { search: false } });
    api.servicesStatus.mockResolvedValue(status({ search: "openrouter", enabled: { search: true } }));
    press(host, '[data-k="svc-svc:openrouter"]');
    press(host, '[data-k="svc-use"]');
    await flush();
    expect(changeSetting.mock.calls).toEqual([["web_search_service", "openrouter"], ["web_search_enabled", true]]);
  });

  test("「使用」 on a service that is chosen but off only turns it on; on one that is in use it does nothing", async () => {
    const { host, changeSetting } = await opened("search", { search: "brave", braveKey: true, enabled: { search: false } });
    press(host, '[data-k="svc-svc:brave"]');
    press(host, '[data-k="svc-use"]');
    await flush();
    expect(changeSetting.mock.calls).toEqual([["web_search_enabled", true]]);
    api.servicesStatus.mockResolvedValue(status({ braveKey: true }));
    await flush();
    changeSetting.mockClear();
    press(host, '[data-k="svc-use"]'); // now in use and on: the button is disabled
    await flush();
    expect(changeSetting).not.toHaveBeenCalled();
    expect(host.querySelector('[data-k="svc-use"]')!.hasAttribute("disabled")).toBe(true);
  });

  test("「使用」 cannot be pressed on a service that cannot be used", async () => {
    const { host, changeSetting } = await opened("search");
    press(host, '[data-k="svc-svc:tavily"]');
    expect(host.querySelector('[data-k="svc-use"]')!.hasAttribute("disabled")).toBe(true);
    press(host, '[data-k="svc-use"]');
    await flush();
    expect(changeSetting).not.toHaveBeenCalled();
  });

  test("看圖片: 「與回答相同」 is the empty model, and the picker's choice is the vision_model setting", async () => {
    const { host, panel, changeSetting } = await opened("vision", { model: "anthropic/claude-haiku-5.5" });
    press(host, '[data-k="svc-svc:same"]');
    press(host, '[data-k="svc-use"]');
    await flush();
    expect(changeSetting).toHaveBeenCalledWith("vision_model", "");
    await panel.chooseVision("google/gemini-4-flash");
    await panel.chooseVision("");
    expect(changeSetting.mock.calls.filter((c) => c[0] === "vision_model")).toEqual([["vision_model", ""], ["vision_model", "google/gemini-4-flash"], ["vision_model", ""]]);
  });

  test("「儲存」 sends the key to the vault and empties the field at once, before the core answers", async () => {
    const { host, panel } = await opened("search");
    press(host, '[data-k="svc-svc:brave"]');
    let answer!: (value: { ok: boolean }) => void;
    api.servicesSetKey.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const field = host.querySelector<HTMLInputElement>(`#${keyInputId("brave")}`)!;
    field.value = "  BSA-typed-key  ";
    press(host, '[data-svc="save"][data-service="brave"]');
    expect(api.servicesSetKey).toHaveBeenCalledWith("brave", "BSA-typed-key");
    expect(field.value).toBe("");
    // the page holds the key nowhere while it is on its way
    expect(host.innerHTML).not.toContain("BSA-typed-key");
    expect(JSON.stringify(panel.view())).not.toContain("BSA-typed-key");
    api.servicesStatus.mockResolvedValue(status({ braveKey: true }));
    answer({ ok: true });
    await flush();
    expect(host.querySelector('[data-svc-line="brave"]')!.textContent).toBe("已設定移除");
    expect(host.innerHTML).not.toContain("BSA-typed-key");
    expect(toaster.info).toHaveBeenCalledWith("已存進 Windows 認證管理員");
    expect(titleOf(host)).toBe("Brave"); // it stays on the page of the service
  });

  test("Enter in the field sends it too; nothing typed, or more than the core takes, sends nothing", async () => {
    const { host } = await opened("search");
    press(host, '[data-k="svc-svc:brave"]');
    api.servicesSetKey.mockResolvedValue({ ok: true });
    const field = (): HTMLInputElement => host.querySelector<HTMLInputElement>(`#${keyInputId("brave")}`)!;
    field().value = "   ";
    field().dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    field().value = "k".repeat(513);
    field().dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(api.servicesSetKey).not.toHaveBeenCalled();
    expect(field().value).toBe(""); // even a key that is not sent leaves the field
    field().value = "jina-or-brave";
    field().dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(api.servicesSetKey).toHaveBeenCalledWith("brave", "jina-or-brave");
  });

  test("a key the core refuses is said, and the field stays empty", async () => {
    const { host } = await opened("search");
    press(host, '[data-k="svc-svc:brave"]');
    api.servicesSetKey.mockRejectedValue("the key is too long");
    host.querySelector<HTMLInputElement>(`#${keyInputId("brave")}`)!.value = "BSA-bad";
    press(host, '[data-svc="save"]');
    await flush();
    expect(toaster.info).toHaveBeenCalled();
    expect(host.querySelector<HTMLInputElement>(`#${keyInputId("brave")}`)!.value).toBe("");
    expect(host.innerHTML).not.toContain("BSA-bad");
  });

  test("「移除」 deletes the key from the vault", async () => {
    const { host } = await opened("search", { braveKey: true });
    press(host, '[data-k="svc-svc:brave"]');
    api.servicesClearKey.mockResolvedValue({ ok: true });
    api.servicesStatus.mockResolvedValue(status());
    press(host, '[data-svc="clear"][data-service="brave"]');
    await flush();
    expect(api.servicesClearKey).toHaveBeenCalledWith("brave");
    expect(host.querySelector(`#${keyInputId("brave")}`)).not.toBeNull();
    expect(toaster.info).toHaveBeenCalledWith("已移除 Brave 的金鑰");
  });

  test("SearXNG's address is saved with settings.set, and only an http(s) one", async () => {
    const { host, changeSetting } = await opened("search", { search: "searxng" });
    press(host, '[data-k="svc-svc:searxng"]');
    const field = host.querySelector<HTMLInputElement>("#svc-url")!;
    field.value = "ftp://searx.local";
    press(host, '[data-svc="url"]');
    await flush();
    expect(changeSetting).not.toHaveBeenCalled();
    expect(toaster.info).toHaveBeenCalledWith("網址要以 http:// 或 https:// 開頭");
    host.querySelector<HTMLInputElement>("#svc-url")!.value = " http://192.168.1.5:8080 ";
    press(host, '[data-svc="url"]');
    await flush();
    expect(changeSetting).toHaveBeenCalledWith("searxng_url", "http://192.168.1.5:8080");
    expect(api.servicesSetKey).not.toHaveBeenCalled();
  });

  test("the key of Fish Audio goes to the vault like the others", async () => {
    const { host } = await opened("voice");
    press(host, '[data-k="svc-svc:fish"]');
    api.servicesSetKey.mockResolvedValue({ ok: true });
    host.querySelector<HTMLInputElement>(`#${keyInputId("fish")}`)!.value = "fish-typed-key";
    press(host, '[data-svc="save"][data-service="fish"]');
    expect(api.servicesSetKey).toHaveBeenCalledWith("fish", "fish-typed-key");
    await flush();
    expect(host.innerHTML).not.toContain("fish-typed-key");
  });

  test("the voice model id is saved with settings.set tts_fish_voice, and only 32 hexadecimal characters or none", async () => {
    const { host, changeSetting } = await opened("voice");
    press(host, '[data-k="svc-svc:fish"]');
    const field = (): HTMLInputElement => host.querySelector<HTMLInputElement>("#svc-voice-id")!;
    field().value = "not-an-id";
    press(host, '[data-svc="voice"]');
    await flush();
    expect(changeSetting).not.toHaveBeenCalled();
    expect(toaster.info).toHaveBeenCalledWith("聲音模型 ID 是 32 個十六進位字元");
    field().value = `  ${VOICE_ID.toUpperCase()}  `;
    field().dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await flush();
    expect(changeSetting).toHaveBeenCalledWith("tts_fish_voice", VOICE_ID.toUpperCase());
    field().value = "";
    press(host, '[data-svc="voice"]');
    await flush();
    expect(changeSetting).toHaveBeenLastCalledWith("tts_fish_voice", "");
  });

  test("「Try it」 saves the id, then has Raphael say the sample line of the window's language", async () => {
    const { host, changeSetting } = await opened("voice", { fishKey: true, voiceSet: true });
    press(host, '[data-k="svc-svc:fish"]');
    api.ttsPreview.mockResolvedValue({ ok: true });
    press(host, '[data-svc="try"]');
    await flush();
    expect(changeSetting).toHaveBeenCalledWith("tts_fish_voice", VOICE_ID);
    expect(api.ttsPreview).toHaveBeenCalledWith("告。主人，請下達指示。");
    expect(toaster.info).not.toHaveBeenCalled();
    setLocale("en");
    press(host, '[data-svc="try"]');
    await flush();
    expect(api.ttsPreview).toHaveBeenLastCalledWith("Notice. Master, please give your instructions.");
    setLocale("ja");
    press(host, '[data-svc="try"]');
    await flush();
    expect(api.ttsPreview).toHaveBeenLastCalledWith("告。マスター、ご指示をお願いします。");
  });

  test("「Try it」 with a bad id previews nothing", async () => {
    const { host } = await opened("voice");
    press(host, '[data-k="svc-svc:fish"]');
    host.querySelector<HTMLInputElement>("#svc-voice-id")!.value = "zzz";
    press(host, '[data-svc="try"]');
    await flush();
    expect(api.ttsPreview).not.toHaveBeenCalled();
  });

  test.each([
    ["not_configured", "先填入聲音模型 ID"],
    ["missing_secret", "先儲存 API 金鑰"],
    ["auth", "Fish Audio 不接受這把金鑰"],
    ["network", "無法發聲"],
    [undefined, "無法發聲"],
  ])("a voice that could not be played (%s) is said in a few words", async (kind, words) => {
    const { host } = await opened("voice");
    press(host, '[data-k="svc-svc:fish"]');
    api.ttsPreview.mockResolvedValue({ ok: false, kind });
    press(host, '[data-svc="try"]');
    await flush();
    expect(toaster.info).toHaveBeenCalledWith(words);
  });

  test("a call that fails is told by the error", async () => {
    const { host } = await opened("voice");
    press(host, '[data-k="svc-svc:fish"]');
    api.ttsPreview.mockRejectedValue("the core is not running");
    press(host, '[data-svc="try"]');
    await flush();
    expect(toaster.info).toHaveBeenCalled();
  });

  test("reset drops the dialog, and a status that comes in late is ignored", async () => {
    const { host, panel } = await opened("search");
    panel.reset();
    expect(panel.view().open).toBeNull();
    let late!: (value: ServicesStatus) => void;
    api.servicesStatus.mockReturnValue(new Promise((resolve) => (late = resolve)));
    const reading = panel.load();
    panel.reset();
    late(status({ search: "tavily" }));
    await reading;
    expect(host.innerHTML).toContain("Brave");
  });

  test("an address is blank or http(s), and a voice id is blank or 32 hexadecimal characters", () => {
    expect(isServiceUrl("")).toBe(true);
    expect(isServiceUrl("https://searx.example/search")).toBe(true);
    expect(isServiceUrl("http://localhost:8888")).toBe(true);
    expect(isServiceUrl("searx.example")).toBe(false);
    expect(isServiceUrl("javascript:alert(1)")).toBe(false);
    expect(isServiceUrl("file:///C:/x")).toBe(false);
    expect(isVoiceId("")).toBe(true);
    expect(isVoiceId(` ${VOICE_ID} `)).toBe(true);
    expect(isVoiceId(VOICE_ID.slice(1))).toBe(false);
    expect(isVoiceId(`${VOICE_ID}0`)).toBe(false);
    expect(isVoiceId("g".repeat(32))).toBe(false);
  });
});
