// Focused-main-window fallback for keyboard input the global hook does not receive.
// Kept independent of pages so the first-run try box uses the same listener.
import { invoke } from "@tauri-apps/api/core";

export type WindowHotkey = "dictation_down" | "dictation_up" | "paste_last" | "cancel";
interface Bindings { dictation_keys: string[]; paste_last_keys: string[] }
export interface KeyInput {
  code: string;
  type: "keydown" | "keyup";
  repeat: boolean;
  altGraph: boolean;
}
interface HotkeyState {
  held: string[];
  swallowed: string[];
  dictation: string[];
}

const MODIFIERS = new Set([
  "altleft", "altright", "controlleft", "controlright",
  "shiftleft", "shiftright", "metaleft", "metaright",
]);

export const emptyHotkeyState = (): HotkeyState => ({ held: [], swallowed: [], dictation: [] });

// Settings records browser codes; device.json's default uses the alias V.
// Legacy handy-keys aliases beyond letters/digits and these modifiers are not supported here.
function codeName(code: string): string {
  const name = code.trim().toLowerCase();
  if (/^[a-z]$/.test(name)) return `key${name}`;
  if (/^\d$/.test(name)) return `digit${name}`;
  return name.replace(/^ctrl(left|right)$/, "control$1").replace(/^cmd(left|right)$/, "meta$1");
}

function matchBinding(bindings: string[], held: string[], code: string): string[] | undefined {
  return bindings.map((binding) => binding.split("+").map(codeName)).find((parts) =>
    parts.includes(code) && parts.every((part) => held.includes(part)) &&
    held.filter((part) => MODIFIERS.has(part)).every((part) => parts.includes(part)),
  );
}

/** Pure keyboard transition. The worker checks pauses, cancel enablement and hook duplicates again. */
export function reduceHotkey(previous: HotkeyState, event: KeyInput, bindings: Bindings, paused = false): {
  state: HotkeyState; actions: WindowHotkey[]; preventDefault: boolean;
} {
  if (paused) return { state: emptyHotkeyState(), actions: [], preventDefault: false };
  const code = codeName(event.code);
  const state: HotkeyState = {
    held: previous.held.filter((part) => part !== code),
    swallowed: [...previous.swallowed],
    dictation: [...previous.dictation],
  };
  const actions: WindowHotkey[] = [];
  let preventDefault = state.swallowed.includes(code);
  if (event.type === "keyup") {
    if (state.dictation.includes(code)) {
      actions.push("dictation_up");
      state.dictation = [];
    }
    state.swallowed = state.swallowed.filter((part) => part !== code);
  } else {
    state.held.push(code);
    // AltGr produces a synthetic left Ctrl before Right Alt on Windows.
    if (event.altGraph && state.held.includes("altright")) {
      state.held = state.held.filter((part) => part !== "controlleft");
    }
    // Swallow Alt before the rest of a configured chord arrives, including its release, so
    // an Alt-only tap or repeat cannot activate WebView2's menu handling.
    if ((code === "altright" || code === "altleft") &&
      [...bindings.dictation_keys, ...bindings.paste_last_keys].some((binding) =>
        binding.split("+").map(codeName).includes(code))) {
      if (!state.swallowed.includes(code)) state.swallowed.push(code);
      preventDefault = true;
    }
    if (!event.repeat && !previous.held.includes(code)) {
      if (code === "escape") {
        actions.push("cancel");
        // Preserve idle Escape (e.g. closing settings). Only the worker knows whether dictation
        // is active; it ignores Cancel while idle. Alt and paste keys are always suppressed.
      } else {
        const dictation = matchBinding(bindings.dictation_keys, state.held, code);
        const paste = matchBinding(bindings.paste_last_keys, state.held, code);
        if (dictation && state.dictation.length === 0) {
          state.dictation = dictation;
          actions.push("dictation_down");
        }
        if (paste) actions.push("paste_last");
        if (dictation || paste) {
          state.swallowed.push(code);
          preventDefault = true;
        }
      }
    }
  }
  return { state, actions, preventDefault };
}

let recorderPaused = false;
let pauseGeneration = 0;

/** Called before recorder IPC so capture listeners never steal the key being recorded. */
export function setWindowHotkeysPaused(paused: boolean): boolean {
  const previous = recorderPaused;
  recorderPaused = paused;
  pauseGeneration++;
  return previous;
}

/** Keep capture paused throughout recorder IPC, restoring it if the update fails. */
export async function withWindowHotkeyPause(paused: boolean, update: () => Promise<void>): Promise<void> {
  const previous = setWindowHotkeysPaused(true);
  try {
    await update();
  } catch (err) {
    setWindowHotkeysPaused(previous);
    throw err;
  }
  setWindowHotkeysPaused(paused);
}

/** Install once on the main window, before either normal pages or onboarding is mounted. */
export function installWindowHotkeys(
  bindings: () => Bindings,
  target: EventTarget = window,
  send: (kind: WindowHotkey) => Promise<unknown> = (kind) => invoke("window_hotkey", { kind }),
): () => void {
  let state = emptyHotkeyState();
  let generation = pauseGeneration;
  const forward = (action: WindowHotkey): void => {
    void send(action).catch((err) => console.warn("window hotkey could not be forwarded", err));
  };
  const handle = (raw: Event): void => {
    const event = raw as KeyboardEvent;
    if (generation !== pauseGeneration) {
      state = emptyHotkeyState();
      generation = pauseGeneration;
    }
    const result = reduceHotkey(state, {
      code: event.code, type: event.type as KeyInput["type"], repeat: event.repeat,
      altGraph: event.getModifierState("AltGraph"),
    }, bindings(), recorderPaused);
    state = result.state;
    if (result.preventDefault) event.preventDefault();
    result.actions.forEach(forward);
  };
  const blur = (): void => {
    // A hold that loses focus will not necessarily receive its DOM key-up.
    if (!recorderPaused && state.dictation.length > 0) forward("dictation_up");
    state = emptyHotkeyState();
  };
  const capture = { capture: true };
  target.addEventListener("keydown", handle, capture);
  target.addEventListener("keyup", handle, capture);
  target.addEventListener("blur", blur);
  return () => {
    target.removeEventListener("keydown", handle, capture);
    target.removeEventListener("keyup", handle, capture);
    target.removeEventListener("blur", blur);
  };
}
