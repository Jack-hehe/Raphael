/// <reference types="node" />
import { afterEach, describe, expect, test } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { setLocale } from "./i18n";
import { PAUSE_MEDIA_KEY, pauseMediaRow, pauseMediaToggle } from "./pause-media";

afterEach(() => setLocale("zh-TW"));

const read = (...parts: string[]): string => readFileSync(path.join(import.meta.dirname, ...parts), "utf8").replace(/\r\n/g, "\n");

describe("the row in the settings", () => {
  test("is a switch that shows whether the media is paused while Raphael listens", () => {
    const on = pauseMediaRow(true);
    expect(on).toContain('role="switch"');
    expect(on).toContain('aria-checked="true"');
    expect(on).toContain(`data-k="${PAUSE_MEDIA_KEY}"`);
    expect(pauseMediaRow(false)).toContain('aria-checked="false"');
  });

  test("is called 說話時暫停播放中的音樂和影片, Pause media while I speak and 話している間は再生中の音楽や動画を一時停止", () => {
    const names = { "zh-TW": "說話時暫停播放中的音樂和影片", en: "Pause media while I speak", ja: "話している間は再生中の音楽や動画を一時停止" } as const;
    for (const locale of ["zh-TW", "en", "ja"] as const) {
      setLocale(locale);
      const html = pauseMediaRow(true);
      // the name is the label of the switch and the title of the row
      expect(html).toContain(`aria-label="${names[locale]}"`);
      expect(html).toContain(`<div class="t">${names[locale]}</div>`);
      expect(html).toMatch(/<div class="d">[^<]+<\/div>/);
    }
  });

  test("tells nothing in one language that it tells in another", () => {
    setLocale("en");
    const english = pauseMediaRow(true);
    setLocale("zh-TW");
    const chinese = pauseMediaRow(true);
    expect(english).not.toMatch(/[㐀-鿿぀-ヿ]/);
    expect(chinese).not.toMatch(/Pause media/);
  });
});

describe("a click on the switch", () => {
  test("turns the setting off when it is on, and on when it is off, and changes nothing else", () => {
    expect(pauseMediaToggle({ pause_media: true })).toEqual({ pause_media: false });
    expect(pauseMediaToggle({ pause_media: false })).toEqual({ pause_media: true });
  });
});

describe("the settings page", () => {
  const settings = read("pages", "settings.ts");

  test("shows the row in the dictation tab, in its audio section", () => {
    const dictation = settings.slice(settings.indexOf("function dictationHTML()"), settings.indexOf("function agentHTML()"));
    const audio = dictation.slice(dictation.indexOf('section("mic"'), dictation.indexOf('section("wave"'));
    expect(audio).toContain("pauseMediaRow(d.pause_media)");
    // and nowhere else
    expect(settings.match(/pauseMediaRow\(/g)).toHaveLength(1);
  });

  test("changes the setting on a click, through the device settings like the other switches", () => {
    expect(settings).toMatch(/closest\(`\.tg\[data-k="\$\{PAUSE_MEDIA_KEY\}"\]`\)\) \{\n\s+void changeDevice\(pauseMediaToggle\(app\.device\(\)\)\);/);
  });
});

describe("the default", () => {
  test("is on in the window and in device_settings.rs, which hold the same one", () => {
    expect(read("main.ts")).toMatch(/DEFAULT_DEVICE[^]*?pause_media: true,/);
    const defaults = read("..", "..", "src-tauri", "src", "device_settings.rs");
    expect(defaults.slice(defaults.indexOf("impl Default for DeviceSettings"))).toMatch(/pause_media: true,/);
  });
});
