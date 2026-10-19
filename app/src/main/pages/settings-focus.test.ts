import { describe, expect, test, vi } from "vitest";
import { trapSettingsFocus } from "./settings";

describe("settings and window control focus loop", () => {
  // The panel comes first in the document and the window controls last.
  const fixture = () => {
    const heading = { focus: vi.fn() };
    const setting = { focus: vi.fn() };
    const finalSetting = { focus: vi.fn() };
    const minimize = { focus: vi.fn() };
    const maximize = { focus: vi.fn() };
    const close = { focus: vi.fn() };
    return { heading, setting, finalSetting, minimize, maximize, close, items: [setting, finalSetting, minimize, maximize, close] };
  };

  test("Tab from the last window control wraps into the settings panel", () => {
    const app = fixture();
    const event = { shiftKey: false, preventDefault: vi.fn() };
    trapSettingsFocus(event, app.items, app.close);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(app.setting.focus).toHaveBeenCalledOnce();
  });

  test("Shift Tab from the first setting wraps back to the last window control", () => {
    const app = fixture();
    const event = { shiftKey: true, preventDefault: vi.fn() };
    trapSettingsFocus(event, app.items, app.setting);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(app.close.focus).toHaveBeenCalledOnce();
  });

  test("Shift Tab from the panel heading, which opens with the focus, also wraps to the last window control", () => {
    const app = fixture();
    const event = { shiftKey: true, preventDefault: vi.fn() };
    trapSettingsFocus(event, app.items, app.heading, app.heading);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(app.close.focus).toHaveBeenCalledOnce();
  });

  test("interior controls and the panel heading retain native Tab traversal", () => {
    const app = fixture();
    for (const focused of [app.finalSetting, app.minimize, app.maximize, null]) {
      const event = { shiftKey: false, preventDefault: vi.fn() };
      trapSettingsFocus(event, app.items, focused, app.heading);
      expect(event.preventDefault).not.toHaveBeenCalled();
    }
    for (const focused of [app.finalSetting, app.minimize, app.close, null]) {
      const event = { shiftKey: true, preventDefault: vi.fn() };
      trapSettingsFocus(event, app.items, focused, app.heading);
      expect(event.preventDefault).not.toHaveBeenCalled();
    }
    const empty = { shiftKey: false, preventDefault: vi.fn() };
    trapSettingsFocus(empty, [], null);
    expect(empty.preventDefault).not.toHaveBeenCalled();
  });
});
