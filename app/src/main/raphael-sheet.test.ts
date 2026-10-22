import { afterEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";

vi.mock("./api", () => ({}));

import { emptyMessagingView } from "./messaging";
import { type FocusStop, type SheetView, sheetBodyHTML, sheetFrameHTML, sheetKey, trapFocus } from "./raphael-sheet";
import type { DeviceSettings } from "./types";

afterEach(() => setLocale("zh-TW"));

const device = { agent_keys: ["AltRight+Space"], dictation_keys: ["AltRight"], paste_last_keys: [] } as unknown as DeviceSettings;
const view = (over: Partial<SheetView> = {}): SheetView => ({
  device, altgr: false, city: "台北", cityDraft: null, cityError: false,
  messaging: { ...emptyMessagingView(), status: { connected: false, enabled: false, running: false, error: null, bot: null, allowed: [], pending: [] } },
  ...over,
});

/** What a sighted reader sees: the text outside the tags, without what only a screen reader hears. */
const visible = (html: string): string =>
  html.replace(/<span class="sr">.*?<\/span>/g, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

describe("the sheet", () => {
  test("is a modal dialog named for Raphael, titled in one word, closed until opened, with a named ✕", () => {
    const html = sheetFrameHTML();
    expect(html).toContain('role="dialog" aria-modal="true" aria-label="拉斐爾的設定" data-open="false"');
    expect(html).toContain('<h2 id="ra-sheet-title" tabindex="-1">'); // where the focus goes when it opens
    expect(html).toContain("<span>設定</span></h2>");
    expect(html).toContain('aria-label="關閉" title="關閉" data-act="sheet-close"');
  });

  test("holds Raphael's key, the weather city and Telegram, in that order", () => {
    const html = sheetBodyHTML(view());
    const at = (needle: string): number => html.indexOf(needle);
    expect(at('data-row="agent-hotkey"')).toBeGreaterThan(-1);
    expect(at('id="agent-city"')).toBeGreaterThan(at('data-row="agent-hotkey"'));
    expect(at("data-msg-section")).toBeGreaterThan(at('id="agent-city"'));
  });

  test("the key is caps and a pencil named for what it does; the city a field; few words", () => {
    const html = sheetBodyHTML(view());
    expect(html).toContain("<kbd>右 Alt</kbd>");
    expect(html).toContain('aria-label="變更快捷鍵" title="變更快捷鍵" data-act="rec-agent"');
    expect(html).toContain('id="agent-city" type="text" value="台北"');
    expect(html).toContain('aria-label="天氣的城市"');
    // no explanation under a row: the two names and the caps, then Telegram's own few words
    expect(visible(html).startsWith("問拉斐爾 右 Alt + Space 天氣 Telegram")).toBe(true);
  });

  test("no key yet is a dash that says so", () => {
    const html = sheetBodyHTML(view({ device: { ...device, agent_keys: [] } }));
    expect(html).toContain('<span class="ra-none" aria-label="尚未設定">—</span>');
  });

  test("Right Alt on an AltGr keyboard is warned about, and only then", () => {
    expect(sheetBodyHTML(view({ altgr: true }))).toContain("AltGr");
    expect(sheetBodyHTML(view())).not.toContain("AltGr");
    expect(sheetBodyHTML(view({ altgr: true, device: { ...device, agent_keys: ["ControlLeft+Space"] } }))).not.toContain("AltGr");
  });

  test("an empty city is an error in words; an unknown city waits", () => {
    const empty = sheetBodyHTML(view({ cityDraft: " ", cityError: true }));
    expect(empty).toContain('role="alert"');
    expect(empty).toContain('aria-invalid="true"');
    expect(sheetBodyHTML(view({ city: null }))).toMatch(/id="agent-city"[^>]*disabled>/);
  });

  test("every icon-only control is named in its label and tooltip, in each language", () => {
    for (const locale of ["zh-TW", "en", "ja"] as const) {
      setLocale(locale);
      const html = sheetFrameHTML() + sheetBodyHTML(view());
      for (const tag of html.match(/<button[^>]*class="btn icon[^>]*>/g) ?? []) {
        expect(tag).toMatch(/aria-label="[^"]+"/);
        expect(tag).toMatch(/title="[^"]+"/);
      }
      expect(html).not.toMatch(/agent\.|msg\.|settings\./);
    }
  });
});

describe("its keys", () => {
  test("Escape closes it, except while a shortcut is being recorded; Tab stays inside", () => {
    expect(sheetKey("Escape", false)).toBe("close");
    expect(sheetKey("Escape", true)).toBeNull(); // the recording's own: it cancels the recording
    expect(sheetKey("Tab", false)).toBe("trap");
    expect(sheetKey("a", false)).toBeNull();
  });

  test("Tab from the last stop goes to the first, Shift+Tab from the first to the last", () => {
    const stops: FocusStop[] = [{ focus() {} }, { focus() {} }, { focus() {} }];
    expect(trapFocus(false, stops, stops[2])).toBe(stops[0]);
    expect(trapFocus(true, stops, stops[0])).toBe(stops[2]);
    expect(trapFocus(false, stops, stops[1])).toBeNull(); // inside: the browser moves it
    expect(trapFocus(true, stops, null)).toBe(stops[2]); // the focus was on the title
    expect(trapFocus(false, stops, null)).toBe(stops[0]);
    expect(trapFocus(false, [], null)).toBeNull();
  });
});
