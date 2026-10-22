import { expect, test, vi } from "vitest";

vi.mock("../api", () => ({}));

import { openRaphaelSettings } from "./agent";

test("the page's settings button opens the Settings panel on the 拉斐爾 tab", () => {
  const go = vi.fn();
  openRaphaelSettings({ go });
  expect(go).toHaveBeenCalledWith("settings", "agent");
});
