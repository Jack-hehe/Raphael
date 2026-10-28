// The device settings as the window knows them (device_settings.rs keeps the real ones).

import type { DeviceSettings } from "./types";

/** Where the settings live: the two commands of the app. */
export interface DeviceSource {
  get(): Promise<DeviceSettings>;
  set(patch: Partial<DeviceSettings>): Promise<DeviceSettings>;
  /**
   * Takes one app off the always-allowed list, under the app's own lock on the settings, and
   * returns them all afterwards. A whole list computed here could put back an app that another
   * removal, still on its way, takes away.
   */
  removeComputerApp(exe: string): Promise<DeviceSettings>;
}

/**
 * Holds the device settings and keeps them right when reads and writes overlap. Every read and
 * every write takes a number, and a read is used only if no other read or write has started or
 * ended since: otherwise it may have seen the settings from before a change and would put them back.
 */
export class DeviceState {
  private current: DeviceSettings;
  private changes = 0;

  /** `changed` is called each time the settings may have changed, such as to redraw the theme. */
  constructor(
    initial: DeviceSettings,
    private readonly source: DeviceSource,
    private readonly changed: () => void,
  ) {
    this.current = initial;
  }

  /** The settings as they are now. */
  get(): DeviceSettings {
    return this.current;
  }

  /**
   * Reads the settings from the app again. Rejects if they cannot be read (the old ones stay). A
   * read that is overtaken by a newer read or by a write is dropped, and the answer is then the
   * settings as they are now.
   */
  async refresh(): Promise<DeviceSettings> {
    const mine = ++this.changes;
    const fresh = await this.source.get();
    if (mine === this.changes) {
      this.current = fresh;
      this.changed();
    }
    return this.current;
  }

  /** Changes some settings; rejects with the reason. */
  set(patch: Partial<DeviceSettings>): Promise<DeviceSettings> {
    return this.write(() => this.source.set(patch));
  }

  /** Takes the app `exe` off the always-allowed list; rejects with the reason. */
  removeComputerApp(exe: string): Promise<DeviceSettings> {
    return this.write(() => this.source.removeComputerApp(exe));
  }

  private async write(change: () => Promise<DeviceSettings>): Promise<DeviceSettings> {
    this.changes++;
    try {
      this.current = await change();
    } catch (err) {
      // The settings may have changed although the call failed (a file that could not be saved).
      this.current = await this.source.get().catch(() => this.current);
      throw err;
    } finally {
      // A read that started while the write ran may have seen either side of it.
      this.changes++;
      this.changed();
    }
    return this.current;
  }
}
