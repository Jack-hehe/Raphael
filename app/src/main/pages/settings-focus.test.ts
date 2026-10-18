import { describe, expect, test, vi } from "vitest";
import { trapSettingsFocus } from "./settings";

describe("settings and window control focus loop", () => {
  const fixture = () => {
    const minimize = { focus: vi.fn() };
    const maximize = { focus: vi.fn() };
    const close = { focus: vi.fn() };
    const setting = { focus: vi.fn() };
    const finalSetting = { focus: vi.fn() };
    return { minimize, maximize, close, setting, finalSetting, items: [minimize, maximize, close, setting, finalSetting] };
  };

  test("Tab from the last setting wraps into window controls", () => {
    const app = fixture();
    const event = { shiftKey: false, preventDefault: vi.fn() };
    trapSettingsFocus(event, app.items, app.finalSetting);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(app.minimize.focus).toHaveBeenCalledOnce();
  });

  test("Shift Tab from the first window control wraps back into the settings panel", () => {
    const app = fixture();
    const event = { shiftKey: true, preventDefault: vi.fn() };
    trapSettingsFocus(event, app.items, app.minimize);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(app.finalSetting.focus).toHaveBeenCalledOnce();
  });

  test("interior controls and the panel heading retain native Tab traversal", () => {
    const app = fixture();
    for (const focused of [app.close, app.setting, null]) {
      const event = { shiftKey: false, preventDefault: vi.fn() };
      trapSettingsFocus(event, app.items, focused);
      expect(event.preventDefault).not.toHaveBeenCalled();
    }
    const empty = { shiftKey: false, preventDefault: vi.fn() };
    trapSettingsFocus(empty, [], null);
    expect(empty.preventDefault).not.toHaveBeenCalled();
  });
});
