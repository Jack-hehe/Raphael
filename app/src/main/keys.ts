// Hotkeys on the settings page: what they are called, which ones are offered, and recording a new
// one. Keys are `KeyboardEvent.code` names (`AltRight`, `Space`, `KeyV`); a binding is such names
// joined by `+`, in the spelling `hotkey::parse_binding` takes. The Rust side checks a binding
// again (`device_settings::validate_binding`), with the same rules.

import { t } from "./i18n";

const LABELS: Record<string, string> = {
  get AltRight() { return t("keys.001"); },
  get AltLeft() { return t("keys.002"); },
  ControlLeft: "Ctrl",
  ControlRight: "Ctrl",
  ShiftLeft: "Shift",
  ShiftRight: "Shift",
  MetaLeft: "Win",
  MetaRight: "Win",
  Space: "Space",
  Escape: "Esc",
  Enter: "Enter",
  Tab: "Tab",
  Backspace: "Backspace",
  Delete: "Delete",
  Insert: "Insert",
  Home: "Home",
  End: "End",
  PageUp: "PageUp",
  PageDown: "PageDown",
  ArrowUp: "↑",
  ArrowDown: "↓",
  ArrowLeft: "←",
  ArrowRight: "→",
  Backquote: "`",
  Minus: "-",
  Equal: "=",
  BracketLeft: "[",
  BracketRight: "]",
  Backslash: "\\",
  Semicolon: ";",
  Quote: "'",
  Comma: ",",
  Period: ".",
  Slash: "/",
};

/** What a key is called on the page: `AltRight` is 「右 Alt」, `KeyV` is 「V」. */
export function keyLabel(part: string): string {
  const known = LABELS[part];
  if (known !== undefined) return known;
  const letter = /^Key([A-Z])$/.exec(part);
  if (letter) return letter[1];
  const digit = /^Digit(\d)$/.exec(part);
  if (digit) return digit[1];
  const pad = /^Numpad(.+)$/.exec(part);
  if (pad) return `Num ${pad[1]}`;
  // The settings file spells the default paste-last key "AltRight+V".
  if (part.length === 1) return part.toUpperCase();
  return part;
}

/** The labels of the keys of a binding: `AltRight+V` is 「右 Alt」, 「V」. */
export function bindingLabels(binding: string): string[] {
  return binding.split("+").map((part) => keyLabel(part.trim()));
}

/**
 * The name two spellings of a key share: the settings file writes the paste key `AltRight+V`, the
 * recorder `AltRight+KeyV`, and the core's `Ctrl` is `Control`.
 */
export function canonicalKey(part: string): string {
  const name = part.trim().toLowerCase();
  if (/^[a-z]$/.test(name)) return `key${name}`;
  if (/^\d$/.test(name)) return `digit${name}`;
  return name.replace(/^ctrl(left|right)$/, "control$1").replace(/^cmd(left|right)$/, "meta$1");
}

/** Whether two bindings are the same keys, in whatever order and spelling. */
export function sameBinding(a: string, b: string): boolean {
  const canonical = (binding: string): string => binding.split("+").map(canonicalKey).sort().join("+");
  return canonical(a) === canonical(b);
}

/**
 * The name of the first action in `taken` that already uses `binding`, or null. Each action has the
 * name to tell the person and the bindings it uses.
 */
export function bindingClash(binding: string, taken: ReadonlyArray<readonly [name: string, bindings: readonly string[]]>): string | null {
  for (const [name, bindings] of taken) if (bindings.some((other) => sameBinding(binding, other))) return name;
  return null;
}

// ---------- which combinations are offered ----------

const MODIFIERS = new Set([
  "AltLeft",
  "AltRight",
  "ControlLeft",
  "ControlRight",
  "ShiftLeft",
  "ShiftRight",
  "MetaLeft",
  "MetaRight",
]);
const SHIFT = new Set(["ShiftLeft", "ShiftRight"]);
const FUNCTION_KEY = /^F([1-9]|1\d|2[0-4])$/;

/**
 * Why the keys `codes` cannot be a hotkey, or null if they can. A hotkey must not get in the way of
 * typing: a key without a modifier is only a hotkey if it is a function key; Shift is no modifier
 * for this, since Shift+V is a capital V; a modifier on its own must be Right Alt, never Left Alt,
 * Shift, Ctrl or Win alone; and Esc cancels a dictation, so it is never a hotkey.
 */
export function validateCodes(codes: string[]): string | null {
  const modifiers = codes.filter((code) => MODIFIERS.has(code));
  const keys = codes.filter((code) => !MODIFIERS.has(code));
  if (keys.length > 1) return t("keys.003");
  if (keys[0] === "Escape") return t("keys.004");
  if (keys.length === 0) {
    const aloneRightAlt = modifiers.length === 1 && modifiers[0] === "AltRight";
    return aloneRightAlt ? null : t("keys.005");
  }
  if (FUNCTION_KEY.test(keys[0])) return null;
  if (modifiers.some((code) => !SHIFT.has(code))) return null;
  return t("keys.006");
}

/** The binding for the keys `codes`: the modifiers, then the key, whatever order they were pressed in. */
export function bindingFromCodes(codes: string[]): string {
  const modifiers = codes.filter((code) => MODIFIERS.has(code));
  const keys = codes.filter((code) => !MODIFIERS.has(code));
  return [...modifiers, ...keys].join("+");
}

// ---------- recording ----------

export type RecorderResult =
  /** Keys are down: this is what has been pressed so far. */
  | { type: "progress"; codes: string[] }
  /** Every key is up again: this is the combination. */
  | { type: "done"; codes: string[] }
  /** Esc was pressed with nothing held. */
  | { type: "cancel" };

/**
 * Records one combination: everything that is pressed until all keys are released again. Feed it
 * the `keydown` and `keyup` events of the page.
 */
export class KeyRecorder {
  private recorded: string[] = [];
  private held = new Set<string>();

  /**
   * `altGraph`: the event says AltGr is down (`getModifierState("AltGraph")`). On a keyboard
   * layout with AltGr, Windows presses the left Ctrl first and then Right Alt; that Ctrl was not
   * pressed by the user and is left out.
   */
  keyDown(code: string, options: { altGraph?: boolean } = {}): RecorderResult {
    if (code === "Escape" && this.held.size === 0 && this.recorded.length === 0) {
      return { type: "cancel" };
    }
    if (code === "AltRight" && options.altGraph) {
      this.recorded = this.recorded.filter((known) => known !== "ControlLeft");
      this.held.delete("ControlLeft");
    }
    this.held.add(code);
    if (!this.recorded.includes(code)) this.recorded.push(code);
    return { type: "progress", codes: [...this.recorded] };
  }

  /** Null for a key that was not pressed during the recording (the Enter that pressed the button). */
  keyUp(code: string): RecorderResult | null {
    if (!this.held.delete(code)) return null;
    if (this.held.size > 0) return { type: "progress", codes: [...this.recorded] };
    return { type: "done", codes: [...this.recorded] };
  }
}
