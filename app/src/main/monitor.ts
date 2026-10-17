// The switch for the microphone's loudness readings (`audio.monitor`). The calls go one after the
// other, so that "on" then "off" reach the core in that order whatever each call's speed.

export class MonitorSwitch {
  private on = false;
  private calls: Promise<unknown> = Promise.resolve();

  /** `call` turns the readings on or off in the core. */
  constructor(private readonly call: (on: boolean) => Promise<unknown>) {}

  /** Wants the readings on or off. Nothing is sent if they already are. */
  set(want: boolean): void {
    if (want === this.on) return;
    this.on = want;
    this.calls = this.calls
      .then(() => this.call(want))
      .catch(() => {
        // The core is not there; the next `set(true)` tries again.
        if (want) this.on = false;
      });
  }
}
