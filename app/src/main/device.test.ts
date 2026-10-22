import { describe, expect, test, vi } from "vitest";
import { DeviceState, type DeviceSource } from "./device";
import type { DeviceSettings } from "./types";

const base: DeviceSettings = {
  dictation_keys: ["AltRight"],
  dictation_mode: "toggle",
  paste_last_keys: ["AltRight+V"],
  agent_keys: ["AltRight+Space"],
  microphone: null,
  emblem_size: 150,
  subtitles: "on",
  appearance: "system",
  ui_language: "zh-TW",
  autostart: true,
  onboarded: false,
  computer_apps: [],
};

/** A promise that is settled by hand, so a test decides which call answers first. */
function later<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function state(source: Partial<DeviceSource>, initial = base) {
  const changed = vi.fn();
  const full: DeviceSource = {
    get: () => Promise.resolve(initial),
    set: () => Promise.reject(new Error("no set")),
    ...source,
  };
  return { s: new DeviceState(initial, full, changed), changed };
}

describe("DeviceState", () => {
  test("persists the UI language and restores it in a newly opened window", async () => {
    let saved = { ...base };
    const source: DeviceSource = {
      get: async () => ({ ...saved }),
      set: async (patch) => (saved = { ...saved, ...patch }),
    };
    const first = state(source);
    await first.s.set({ ui_language: "ja" });
    const restarted = state(source);
    await restarted.s.refresh();
    expect(restarted.s.get().ui_language).toBe("ja");
    expect(restarted.changed).toHaveBeenCalledOnce();
  });
  test("starts with what it is given", () => {
    const { s } = state({});
    expect(s.get()).toEqual(base);
  });

  test("refresh reads the settings again and tells the window", async () => {
    const fresh = { ...base, emblem_size: 194 as const };
    const { s, changed } = state({ get: () => Promise.resolve(fresh) });
    await s.refresh();
    expect(s.get()).toEqual(fresh);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  test("a failed refresh keeps what it has and says why", async () => {
    const { s, changed } = state({ get: () => Promise.reject(new Error("down")) });
    await expect(s.refresh()).rejects.toThrow("down");
    expect(s.get()).toEqual(base);
    expect(changed).not.toHaveBeenCalled();
  });

  test("a refresh that a newer refresh has overtaken is dropped", async () => {
    const first = later<DeviceSettings>();
    const second = later<DeviceSettings>();
    const answers = [first.promise, second.promise];
    const { s } = state({ get: () => answers.shift()! });
    const a = s.refresh();
    const b = s.refresh();
    second.resolve({ ...base, appearance: "dark" });
    await b;
    first.resolve({ ...base, appearance: "light" }); // the older read answers last
    await a;
    expect(s.get().appearance).toBe("dark");
  });

  test("a refresh that a write has overtaken is dropped", async () => {
    const read = later<DeviceSettings>();
    const { s } = state({
      get: () => read.promise,
      set: () => Promise.resolve({ ...base, appearance: "dark" }),
    });
    const refreshing = s.refresh();
    await s.set({ appearance: "dark" });
    read.resolve(base); // read before the write, answered after it
    await refreshing;
    expect(s.get().appearance).toBe("dark");
  });

  test("a refresh that starts during a write is dropped too", async () => {
    const write = later<DeviceSettings>();
    const read = later<DeviceSettings>();
    const { s } = state({ get: () => read.promise, set: () => write.promise });
    const writing = s.set({ emblem_size: 120 });
    const refreshing = s.refresh();
    write.resolve({ ...base, emblem_size: 120 });
    await writing;
    read.resolve(base); // may have been read before the write was applied
    await refreshing;
    expect(s.get().emblem_size).toBe(120);
  });

  test("a write returns and keeps what the app answers", async () => {
    const saved = { ...base, subtitles: "off" as const };
    const set = vi.fn(() => Promise.resolve(saved));
    const { s, changed } = state({ set });
    await expect(s.set({ subtitles: "off" })).resolves.toEqual(saved);
    expect(set).toHaveBeenCalledWith({ subtitles: "off" });
    expect(s.get()).toEqual(saved);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  test("a failed write rejects, and the settings are read again", async () => {
    // The file may have changed although the call failed (it could not be saved).
    const now = { ...base, emblem_size: 194 as const };
    const { s, changed } = state({
      set: () => Promise.reject(new Error("cannot save")),
      get: () => Promise.resolve(now),
    });
    await expect(s.set({ emblem_size: 194 })).rejects.toThrow("cannot save");
    expect(s.get()).toEqual(now);
    expect(changed).toHaveBeenCalledTimes(1);
  });

  test("a failed write whose re-read fails keeps what it had", async () => {
    const { s } = state({
      set: () => Promise.reject(new Error("cannot save")),
      get: () => Promise.reject(new Error("down")),
    });
    await expect(s.set({ emblem_size: 194 })).rejects.toThrow("cannot save");
    expect(s.get()).toEqual(base);
  });
});
