// @vitest-environment happy-dom
// Raphael's face when the person asked for less motion: nothing turns and nothing plays.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const GIF = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";
const PNG = "data:image/png;base64,iVBORw0KGgo=";
const STILL = "data:image/png;base64,U1RJTEw=";

let change: ((e: { matches: boolean }) => void) | null = null;
let reduce = true;

async function load() {
  vi.resetModules();
  return import("./avatar");
}

const flush = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

beforeEach(() => {
  change = null;
  reduce = true;
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("reduce") ? reduce : false,
    media: query,
    addEventListener: (_: string, fn: (e: { matches: boolean }) => void) => (change = fn),
    removeEventListener() {},
  }));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("with reduced motion", () => {
  test("the emblem stands still where it would turn, and turns again when the preference goes", async () => {
    const avatar = await load();
    expect(avatar.avatarHTML({ size: 132, loop: true })).toContain('src="/brand/app-emblem-light-128.webp"');
    expect(avatar.avatarHTML({ size: 26, loop: true })).toContain('src="/brand/app-emblem-dark-64.webp"');
    expect(avatar.avatarHTML({ size: 26, loop: true })).not.toContain("loop");
    const heard = vi.fn();
    avatar.onAvatarChange(heard);
    expect(change).not.toBeNull();
    change!({ matches: false });
    expect(heard).toHaveBeenCalledTimes(1);
    expect(avatar.avatarHTML({ size: 132, loop: true })).toContain("app-emblem-light-loop.webp");
    change!({ matches: true });
    expect(heard).toHaveBeenCalledTimes(2);
    expect(avatar.avatarHTML({ size: 132, loop: true })).not.toContain("loop");
  });

  test("a picture of the person's own that may move (GIF, WebP) shows its first frame, still; a PNG is itself", async () => {
    class FakeImage {
      src = "";
      naturalWidth = 40;
      naturalHeight = 20;
      decode = (): Promise<void> => Promise.resolve();
    }
    vi.stubGlobal("Image", FakeImage);
    const drawImage = vi.fn();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(STILL);
    const avatar = await load();
    const heard = vi.fn();
    avatar.onAvatarChange(heard);
    avatar.setAvatar({ url: GIF, crop: { x: 0.25, y: 0.5, zoom: 2 } });
    // until the still is made, nothing that could move is shown
    const first = avatar.avatarHTML({ size: 34 });
    expect(first).toContain('class="av av-custom"');
    expect(first).not.toContain(GIF);
    await flush();
    expect(drawImage).toHaveBeenCalledTimes(1);
    expect(heard).toHaveBeenCalledTimes(2); // the picture, then its still
    const still = avatar.avatarHTML({ size: 34 });
    expect(still).toContain(`src="${STILL}"`);
    expect(still).toContain("object-position:25% 50%");
    expect(still).not.toContain(GIF);
    // with motion again, the picture itself plays
    change!({ matches: false });
    expect(avatar.avatarHTML({ size: 34 })).toContain(`src="${GIF}"`);
    // a PNG never moves: it is shown as it is
    change!({ matches: true });
    avatar.setAvatar({ url: PNG, crop: { x: 0.5, y: 0.5, zoom: 1 } });
    expect(avatar.avatarHTML({ size: 34 })).toContain(`src="${PNG}"`);
  });
});
