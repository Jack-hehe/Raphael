// The key caps of the lessons: the keys of a shortcut, drawn large, and lit while they are down.
// What is down comes from two places: the keys the window itself sees (while it has the focus), and
// the `Hotkey` events of the app (while the keyboard hook has them, or when the window forwarded
// them), which say that a whole shortcut is down. The rules are pure; the lighting of the page is
// one small function.

import { bindingLabels, canonicalKey } from "../keys";
import { esc } from "../ui";

/** The keys of a binding, as the names the keys of the window have (`AltRight+Space` is `altright`, `space`). */
export const bindingParts = (binding: string): string[] => binding.split("+").map(canonicalKey);

export interface HookDown {
  /** The dictation key is down, as the app heard it. */
  dictation: boolean;
  /** The question key is down. */
  agent: boolean;
}

/** Which keys are lit: those the window sees down, and the keys of a shortcut that the app heard go down. */
export function litKeys(held: ReadonlySet<string>, hook: HookDown, dictation: readonly string[], agent: readonly string[]): Set<string> {
  const lit = new Set(held);
  if (hook.dictation) for (const part of dictation) lit.add(part);
  if (hook.agent) for (const part of agent) lit.add(part);
  return lit;
}

/** Whether every key of the shortcut is down. */
export const allDown = (held: ReadonlySet<string>, parts: readonly string[]): boolean => parts.length > 0 && parts.every((part) => held.has(part));

/** The hook state after an event of the app. */
export function hookAfter(hook: HookDown, kind: string): HookDown {
  switch (kind) {
    case "dictation_down":
      return { ...hook, dictation: true };
    case "dictation_up":
      return { ...hook, dictation: false };
    case "agent_down":
      return { ...hook, agent: true };
    case "agent_up":
      return { ...hook, agent: false };
    default:
      return hook;
  }
}

/** The caps of one shortcut: 「右 Alt」 + 「空白鍵」, each lit by its own name. */
export function capsHTML(binding: string, label = ""): string {
  const parts = bindingParts(binding);
  const names = bindingLabels(binding);
  const caps = parts.map((part, i) => `<kbd class="ob-cap" data-cap="${esc(part)}">${esc(names[i])}</kbd>`).join(`<span class="ob-plus" aria-hidden="true">+</span>`);
  return `<span class="ob-caps"${label ? ` role="img" aria-label="${esc(label)}"` : ""}>${caps}</span>`;
}

/** Lights the caps inside `root` that are in `lit`, and puts the others out. */
export function paintCaps(root: ParentNode, lit: ReadonlySet<string>): void {
  root.querySelectorAll<HTMLElement>("[data-cap]").forEach((cap) => cap.classList.toggle("lit", lit.has(cap.dataset.cap ?? "")));
}
