// Recording a new shortcut: the keys are listened to on the whole document while the global hotkeys
// are paused, shown in the row's key caps as they are held, and saved when they are let go. The
// settings record the dictation keys with it and the 拉斐爾 page the key that asks Raphael.

import { t } from "./i18n";

import * as api from "./api";
import { describeError } from "./format";
import { KeyRecorder, bindingClash, bindingFromCodes, bindingLabels, keyLabel, validateCodes } from "./keys";
import type { AppContext } from "./types";
import { keys, toaster } from "./ui";

/** The shortcuts that can be recorded. */
export type HotkeyField = "dictation_keys" | "agent_keys";

/** The name of the other shortcut that already has `binding`, if one has: no two actions share their keys. */
export function shortcutTakenBy(app: Pick<AppContext, "device">, field: HotkeyField, binding: string): string | null {
  const d = app.device();
  const others: Array<readonly [string, readonly string[]]> = [];
  if (field !== "dictation_keys") others.push([t("home.020"), d.dictation_keys]);
  if (field !== "agent_keys") others.push([t("settings.agentKeys"), d.agent_keys ?? []]);
  others.push([t("home.021"), d.paste_last_keys]);
  return bindingClash(binding, others);
}

export interface HotkeyRecording {
  /** Starts recording `field`, showing the keys in `cell`. Nothing happens while one is under way. */
  start(field: HotkeyField, cell: HTMLElement): Promise<void>;
  /** Whether a recording is under way: Escape belongs to it then. */
  active(): boolean;
  /** Drops a recording that is under way: the page went away. */
  cancel(): Promise<void>;
}

/** `ended` is told when a recording is over, saved or not, so that the page draws its row again. */
export function createHotkeyRecording(app: AppContext, ended: () => void): HotkeyRecording {
  let recording: { recorder: KeyRecorder; field: HotkeyField; cell: HTMLElement; finishing: boolean } | null = null;

  function onKeyDown(e: KeyboardEvent): void {
    if (!recording || !e.code || e.code === "Unidentified") return;
    e.preventDefault();
    e.stopPropagation();
    if (recording.finishing) return;
    const result = recording.recorder.keyDown(e.code, { altGraph: e.getModifierState("AltGraph") });
    if (result.type === "cancel") void end();
    else if (result.type === "progress") recording.cell.innerHTML = keys(result.codes.map(keyLabel));
  }

  function onKeyUp(e: KeyboardEvent): void {
    if (!recording) return;
    e.preventDefault();
    e.stopPropagation();
    if (recording.finishing) return;
    const result = recording.recorder.keyUp(e.code);
    if (result?.type === "done") void finish(result.codes);
  }

  /** The window lost focus: the key-up events would not arrive, so the recording is dropped. */
  const onBlur = (): void => void end();

  /** Stops listening and lets the hotkeys work again. */
  async function end(): Promise<void> {
    if (!recording) return;
    recording = null;
    document.removeEventListener("keydown", onKeyDown, true);
    document.removeEventListener("keyup", onKeyUp, true);
    window.removeEventListener("blur", onBlur);
    try {
      await api.hotkeysPause(false);
    } catch (err) {
      toaster.info(describeError(err));
    }
    ended();
  }

  async function finish(codes: string[]): Promise<void> {
    if (!recording || recording.finishing) return;
    recording.finishing = true;
    const problem = validateCodes(codes);
    const field = recording.field;
    if (problem) {
      toaster.info(problem);
    } else {
      try {
        const binding = bindingFromCodes(codes);
        const taken = shortcutTakenBy(app, field, binding);
        if (taken) {
          toaster.info(t("settings.keyTaken", { p0: taken }));
        } else {
          await app.setDevice({ [field]: [binding] });
          toaster.info(t("settings.083", { p0: () => bindingLabels(binding).join(" + ") }));
        }
      } catch (err) {
        toaster.info(describeError(err));
      }
    }
    await end();
  }

  async function start(field: HotkeyField, cell: HTMLElement): Promise<void> {
    if (recording) return;
    try {
      await api.hotkeysPause(true);
    } catch (err) {
      toaster.info(describeError(err));
      return;
    }
    recording = { recorder: new KeyRecorder(), field, cell, finishing: false };
    cell.innerHTML = t("settings.084");
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("blur", onBlur);
  }

  return { start, active: () => recording !== null, cancel: end };
}
