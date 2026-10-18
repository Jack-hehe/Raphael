import { describe, expect, test } from "vitest";
import { menuPlacement } from "./ui";

describe("popover placement", () => {
  const viewport = { width: 720, height: 480, headerBottom: 32 };

  test("a short menu stays below and right aligned with its trigger", () => {
    expect(menuPlacement({ top: 80, bottom: 112, right: 600 }, { width: 240, height: 120 }, viewport))
      .toEqual({ left: 360, top: 116, maxHeight: 356 });
  });

  test("an upward menu is clamped below the custom header and scrolls instead of covering it", () => {
    const placement = menuPlacement({ top: 300, bottom: 332, right: 600 }, { width: 320, height: 400 }, viewport);
    expect(placement).toEqual({ left: 280, top: 40, maxHeight: 256 });
    expect(placement.top + placement.maxHeight).toBe(296);
  });

  test("a tall menu uses the larger space below its trigger while retaining the bottom gap", () => {
    const placement = menuPlacement({ top: 100, bottom: 132, right: 600 }, { width: 320, height: 600 }, viewport);
    expect(placement).toEqual({ left: 280, top: 136, maxHeight: 336 });
    expect(placement.top + placement.maxHeight).toBe(472);
  });

  test("a menu that fits above stays beside the trigger rather than snapping to the header", () => {
    expect(menuPlacement({ top: 400, bottom: 432, right: 710 }, { width: 200, height: 100 }, viewport))
      .toEqual({ left: 510, top: 296, maxHeight: 356 });
  });

  test("horizontal overflow is clamped while a browser without custom chrome keeps its normal top gap", () => {
    expect(menuPlacement({ top: 100, bottom: 132, right: 900 }, { width: 320, height: 600 }, { ...viewport, headerBottom: 0 }))
      .toEqual({ left: 392, top: 136, maxHeight: 336 });
    expect(menuPlacement({ top: 300, bottom: 332, right: 180 }, { width: 320, height: 400 }, { ...viewport, headerBottom: 0 }))
      .toEqual({ left: 8, top: 8, maxHeight: 288 });
  });
});
