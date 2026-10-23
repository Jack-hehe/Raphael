// @vitest-environment happy-dom
// 服務 in Settings › 拉斐爾: what it draws from `services.status`, and what its buttons and fields send.
// The core is faked; a key typed here must leave the field the moment it is sent, and be drawn nowhere.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";

const api = vi.hoisted(() => ({ servicesStatus: vi.fn(), servicesSetKey: vi.fn(), servicesClearKey: vi.fn() }));
vi.mock("./api", () => api);
vi.mock("./ui", async (original) => ({ ...(await original<typeof import("./ui")>()), toaster: { info: vi.fn() } }));

import { type ServicesView, createServices, emptyServicesView, isServiceUrl, keyInputId, servicesHTML } from "./services";
import type { BrainSettings, ServicesStatus } from "./types";
import { toaster } from "./ui";

/** A status of the core: Brave chosen for the search, with no key; Jina for the pages; PDFs read here. */
function status(over: { search?: string; read?: string; braveKey?: boolean; jinaKey?: boolean; urlSet?: boolean } = {}): ServicesStatus {
  return {
    search: {
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
      service: over.read ?? "jina",
      options: [
        { id: "builtin", needs_key: false, has_key: false, key_optional: false },
        { id: "jina", needs_key: false, has_key: over.jinaKey ?? false, key_optional: true },
        { id: "firecrawl", needs_key: true, has_key: false, key_optional: false },
        { id: "tavily", needs_key: true, has_key: false, key_optional: false },
      ],
    },
    pdf: {
      service: "builtin",
      options: [
        { id: "builtin", needs_key: false, has_key: false, key_optional: false },
        { id: "openrouter", needs_key: false, has_key: false, key_optional: false },
      ],
    },
    vision: { model: "", resolved_model: "openai/gpt-6-luna", supports_images: true },
  };
}

const PICKER = '<span class="mp-slot" data-model-picker="vision"></span>';
const BRAIN = { searxng_url: "https://searx.example" } as BrainSettings;
const draw = (over: Partial<ServicesView> = {}, brain: BrainSettings | null = BRAIN): string =>
  servicesHTML({ ...emptyServicesView(), status: status(), ...over }, PICKER, brain);

/** What a sighted reader sees: the text outside the tags. */
const visible = (html: string): string => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const buttons = (html: string): string[] => [...html.matchAll(/<button\b[^>]*>/g)].map((m) => m[0]);
const flush = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

beforeEach(() => {
  document.body.innerHTML = "";
  for (const fn of Object.values(api)) fn.mockReset();
  vi.mocked(toaster.info).mockReset();
});
afterEach(() => setLocale("zh-TW"));

describe("the section", () => {
  test("is titled 服務, with four rows, name left and control right, in that order", () => {
    const html = draw();
    expect(html).toMatch(/^<section class="sgroup svc" data-svc-section><h2>服務<\/h2><div class="slist">/);
    const names = [...html.matchAll(/<div class="t">(.*?)<\/div>/g)].map((m) => m[1]);
    expect(names).toEqual(["搜尋網路", "讀網頁", "看圖片", "讀 PDF"]);
    expect(html).toContain('<div class="ctl"><button type="button" class="sel"');
    expect(html).toContain(PICKER);
    // the dropdowns show what is chosen, by its name
    expect(html).toMatch(/data-k="svc-search"[^>]*><span>Brave<\/span>/);
    expect(html).toMatch(/data-k="svc-read"[^>]*><span>Jina<\/span>/);
    expect(html).toMatch(/data-k="svc-pdf"[^>]*><span>內建<\/span>/);
  });

  test("the built-in services are named in the window's language", () => {
    setLocale("en");
    expect(draw()).toMatch(/data-k="svc-pdf"[^>]*><span>Built-in<\/span>/);
    expect(draw()).toContain("<h2>Services</h2>");
    setLocale("ja");
    expect(draw()).toMatch(/data-k="svc-pdf"[^>]*><span>内蔵<\/span>/);
    expect(draw()).toContain("<h2>サービス</h2>");
  });

  test("a service that needs a key and has none: a password field 「API 金鑰」 and a bordered 「儲存」 under its row", () => {
    const html = draw();
    const line = /<div class="svc-line" data-svc-line="brave">.*?<\/div>(?=<div class="row)/.exec(html)![0];
    expect(line).toContain(`id="${keyInputId("brave")}" type="password" value="" placeholder="API 金鑰"`);
    expect(line).toContain('<button type="button" class="btn" data-svc="save" data-service="brave">儲存</button>');
  });

  test("a stored key: 「已設定」 in muted words and a bordered 「移除」, and no field", () => {
    const html = draw({ status: status({ braveKey: true }) });
    expect(html).toContain('<div class="svc-line" data-svc-line="brave"><span class="val">已設定</span><button type="button" class="btn" data-svc="clear" data-service="brave">移除</button></div>');
    expect(html).not.toContain(keyInputId("brave"));
  });

  test("Jina's key is optional: the same line, its field saying so", () => {
    expect(draw()).toContain('placeholder="API 金鑰（選填）"');
    expect(draw({ status: status({ jinaKey: true }) })).toContain('data-svc="clear" data-service="jina"');
  });

  test("a service that takes no key has no line; SearXNG has its address there", () => {
    const keyless = draw({ status: status({ search: "openrouter", read: "builtin" }) });
    expect(keyless).not.toContain("svc-line");
    const searx = draw({ status: status({ search: "searxng", read: "builtin" }) });
    expect(searx).toContain('<div class="svc-line" data-svc-line="searxng">');
    expect(searx).toContain('id="svc-url" type="text" value="https://searx.example"');
    expect(searx).toContain('data-svc="url" data-service="searxng"');
    expect(searx).not.toContain('type="password"');
  });

  test("minimal: no helper paragraph, and every action a real button with a border", () => {
    const html = draw();
    expect(html).not.toContain('class="d"');
    expect(html).not.toMatch(/<p[s>]/);
    for (const tag of buttons(html)) expect(tag).toMatch(/class="(btn|sel)"/);
  });

  test("while the core has not said, the dropdowns show the settings, closed; a failure says so", () => {
    const html = servicesHTML({ status: "loading", busy: null }, PICKER, { web_search_service: "tavily", web_read_service: "builtin", pdf_service: "openrouter" } as BrainSettings);
    expect(html).toMatch(/data-k="svc-search" disabled><span>Tavily<\/span>/);
    expect(html).toMatch(/data-k="svc-pdf" disabled><span>OpenRouter<\/span>/);
    expect(html).not.toContain("svc-line");
    expect(visible(servicesHTML({ status: "failed", busy: null }, PICKER, null))).toContain("無法讀取服務設定");
  });

  test("never shows a key: whatever the status holds besides, no key is drawn", () => {
    const leaky = status({ braveKey: true }) as unknown as { search: { options: Record<string, unknown>[] } };
    leaky.search.options[1].key = "BSA-secret-key";
    const html = servicesHTML({ status: leaky as unknown as ServicesStatus, busy: null }, PICKER, BRAIN);
    expect(html).not.toContain("BSA-secret-key");
  });
});

describe("what the section does", () => {
  function mount(changeSetting = vi.fn().mockResolvedValue(undefined)) {
    const host = document.createElement("div");
    document.body.append(host);
    const panel = createServices({
      visible: () => true,
      redraw: () => {
        // the panel's drawing, as settings.ts does it: the HTML anew (a field's draft is not kept here)
        host.innerHTML = servicesHTML(panel.view(), PICKER, BRAIN);
      },
      changeSetting,
    });
    host.addEventListener("click", (e) => panel.click(e.target as Element));
    host.addEventListener("keydown", (e) => panel.key(e));
    return { host, panel, changeSetting };
  }

  test("reads the status, and a dropdown offers the core's services and saves the one chosen", async () => {
    api.servicesStatus.mockResolvedValue(status());
    const { host, panel, changeSetting } = mount();
    await panel.load();
    expect(host.querySelector('[data-svc-line="brave"]')).not.toBeNull();
    const menu = panel.menuFor("svc-search")!;
    expect(menu.choices.map((c) => c.label)).toEqual(["OpenRouter", "Brave", "Tavily", "Exa", "SearXNG"]);
    expect(menu.current).toBe("brave");
    expect(panel.menuFor("svc-read")!.choices.map((c) => c.label)).toEqual(["內建", "Jina", "Firecrawl", "Tavily"]);
    expect(panel.menuFor("svc-pdf")!.choices.map((c) => c.label)).toEqual(["內建", "OpenRouter"]);
    expect(panel.menuFor("mic")).toBeNull();
    api.servicesStatus.mockResolvedValue(status({ search: "tavily" }));
    menu.pick("tavily");
    await flush();
    expect(changeSetting).toHaveBeenCalledWith("web_search_service", "tavily");
    expect(api.servicesStatus).toHaveBeenCalledTimes(2); // read again: the new service's key
    // the one chosen already, or one the core did not offer, sends nothing
    panel.menuFor("svc-search")!.pick("tavily");
    panel.menuFor("svc-search")!.pick("google");
    await flush();
    expect(changeSetting).toHaveBeenCalledTimes(1);
  });

  test("「儲存」 sends the key to the vault and empties the field at once, before the core answers", async () => {
    api.servicesStatus.mockResolvedValue(status());
    let answer!: (value: { ok: boolean }) => void;
    api.servicesSetKey.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const { host, panel } = mount();
    await panel.load();
    const field = host.querySelector<HTMLInputElement>(`#${keyInputId("brave")}`)!;
    field.value = "  BSA-typed-key  ";
    host.querySelector<HTMLElement>('[data-svc="save"][data-service="brave"]')!.click();
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
  });

  test("Enter in the field sends it too; nothing typed, or more than the core takes, sends nothing", async () => {
    api.servicesStatus.mockResolvedValue(status());
    api.servicesSetKey.mockResolvedValue({ ok: true });
    const { host, panel } = mount();
    await panel.load();
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
    api.servicesStatus.mockResolvedValue(status());
    api.servicesSetKey.mockRejectedValue("the key is too long");
    const { host, panel } = mount();
    await panel.load();
    host.querySelector<HTMLInputElement>(`#${keyInputId("brave")}`)!.value = "BSA-bad";
    host.querySelector<HTMLElement>('[data-svc="save"]')!.click();
    await flush();
    expect(toaster.info).toHaveBeenCalled();
    expect(host.querySelector<HTMLInputElement>(`#${keyInputId("brave")}`)!.value).toBe("");
    expect(host.innerHTML).not.toContain("BSA-bad");
  });

  test("「移除」 deletes the key from the vault", async () => {
    api.servicesStatus.mockResolvedValue(status({ braveKey: true }));
    api.servicesClearKey.mockResolvedValue({ ok: true });
    const { host, panel } = mount();
    await panel.load();
    api.servicesStatus.mockResolvedValue(status());
    host.querySelector<HTMLElement>('[data-svc="clear"][data-service="brave"]')!.click();
    await flush();
    expect(api.servicesClearKey).toHaveBeenCalledWith("brave");
    expect(host.querySelector(`#${keyInputId("brave")}`)).not.toBeNull();
  });

  test("SearXNG's address is saved with settings.set, and only an http(s) one", async () => {
    api.servicesStatus.mockResolvedValue(status({ search: "searxng" }));
    const { host, panel, changeSetting } = mount();
    await panel.load();
    const field = host.querySelector<HTMLInputElement>("#svc-url")!;
    field.value = "ftp://searx.local";
    host.querySelector<HTMLElement>('[data-svc="url"]')!.click();
    await flush();
    expect(changeSetting).not.toHaveBeenCalled();
    expect(toaster.info).toHaveBeenCalledWith("網址要以 http:// 或 https:// 開頭");
    host.querySelector<HTMLInputElement>("#svc-url")!.value = " http://192.168.1.5:8080 ";
    host.querySelector<HTMLElement>('[data-svc="url"]')!.click();
    await flush();
    expect(changeSetting).toHaveBeenCalledWith("searxng_url", "http://192.168.1.5:8080");
    expect(api.servicesSetKey).not.toHaveBeenCalled();
  });

  test("看圖片's choice is the vision_model setting; 「與回答相同」 is the empty one", async () => {
    api.servicesStatus.mockResolvedValue(status());
    const { panel, changeSetting } = mount();
    await panel.chooseVision("google/gemini-4-flash");
    await panel.chooseVision("");
    expect(changeSetting.mock.calls).toEqual([["vision_model", "google/gemini-4-flash"], ["vision_model", ""]]);
  });

  test("an address is blank or http(s)", () => {
    expect(isServiceUrl("")).toBe(true);
    expect(isServiceUrl("https://searx.example/search")).toBe(true);
    expect(isServiceUrl("http://localhost:8888")).toBe(true);
    expect(isServiceUrl("searx.example")).toBe(false);
    expect(isServiceUrl("javascript:alert(1)")).toBe(false);
    expect(isServiceUrl("file:///C:/x")).toBe(false);
  });
});
