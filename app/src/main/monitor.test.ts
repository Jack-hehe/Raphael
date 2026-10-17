import { describe, expect, test } from "vitest";
import { MonitorSwitch } from "./monitor";

const settle = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

describe("MonitorSwitch", () => {
  test("turns the microphone readings on and off, in order", async () => {
    const calls: boolean[] = [];
    const monitor = new MonitorSwitch(async (on) => void calls.push(on));
    monitor.set(true);
    monitor.set(false);
    monitor.set(true);
    await settle();
    expect(calls).toEqual([true, false, true]);
  });

  test("says nothing when nothing changes", async () => {
    const calls: boolean[] = [];
    const monitor = new MonitorSwitch(async (on) => void calls.push(on));
    monitor.set(false);
    monitor.set(true);
    monitor.set(true);
    await settle();
    expect(calls).toEqual([true]);
  });

  test("a call waits for the one before it", async () => {
    const order: string[] = [];
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const monitor = new MonitorSwitch(async (on) => {
      order.push(`start ${on}`);
      if (on) await gate;
      order.push(`end ${on}`);
    });
    monitor.set(true);
    monitor.set(false);
    await settle();
    expect(order).toEqual(["start true"]);
    release();
    await settle();
    expect(order).toEqual(["start true", "end true", "start false", "end false"]);
  });

  test("when turning on fails, the next try is made", async () => {
    let fail = true;
    const calls: boolean[] = [];
    const monitor = new MonitorSwitch(async (on) => {
      calls.push(on);
      if (fail) throw new Error("no core");
    });
    monitor.set(true);
    await settle();
    fail = false;
    monitor.set(true);
    await settle();
    expect(calls).toEqual([true, true]);
  });

  test("when turning off fails nothing is retried by itself", async () => {
    const calls: boolean[] = [];
    const monitor = new MonitorSwitch(async (on) => {
      calls.push(on);
      if (!on) throw new Error("no core");
    });
    monitor.set(true);
    monitor.set(false);
    await settle();
    monitor.set(false);
    await settle();
    expect(calls).toEqual([true, false]);
  });
});
