// How the OpenRouter key field behaves, on the settings page and in the first-run guide.
//
// The key is sent only by a deliberate act: Enter in the field, or the paste button. It is never
// sent because the field was left (`change` comes on blur): pressing 取消, 上一步 or 先跳過 after
// typing a replacement must not store it. Leaving the field instead clears what was typed, so a
// key that is abandoned does not stay in the page.

export type KeyFieldEvent =
  | { type: "keydown"; key: string; isComposing: boolean }
  | {
      type: "focusout";
      /** Focus moved to the paste button, which is about to send what was typed. */
      toPasteButton: boolean;
      /** The page still has focus. If not, the window was switched away from. */
      documentHasFocus: boolean;
    };

export type KeyFieldAction = "submit" | "clear" | "none";

export function keyFieldAction(event: KeyFieldEvent): KeyFieldAction {
  if (event.type === "keydown") {
    // Enter that ends an IME composition only picks a candidate.
    return event.key === "Enter" && !event.isComposing ? "submit" : "none";
  }
  // The key may be on its way from a browser: switching programs keeps what was typed.
  return event.toPasteButton || !event.documentHasFocus ? "none" : "clear";
}

export interface KeyFieldOptions {
  /** The id of the key input. */
  inputId: string;
  /** Selects the paste button (and what is inside it). */
  pasteSelector: string;
  /** Called with what was typed when Enter is pressed. */
  submit(value: string): void;
  /** Whether the page has focus; the document's own answer unless a test says otherwise. */
  hasFocus?: () => boolean;
}

/** Wires the key field inside `host` as above. Registers no `change` listener on purpose. */
export function wireKeyField(host: HTMLElement, options: KeyFieldOptions): void {
  const hasFocus = options.hasFocus ?? (() => document.hasFocus());

  host.addEventListener("keydown", (e) => {
    const input = e.target as HTMLInputElement | null;
    if (input?.id !== options.inputId) return;
    if (keyFieldAction({ type: "keydown", key: e.key, isComposing: e.isComposing }) === "submit") {
      e.preventDefault();
      options.submit(input.value);
    }
  });

  // No redraw here: the click that moved the focus (on 取消, say) must still find its button.
  host.addEventListener("focusout", (e) => {
    const input = e.target as HTMLInputElement | null;
    if (input?.id !== options.inputId) return;
    const to = e.relatedTarget as Element | null;
    const action = keyFieldAction({
      type: "focusout",
      toPasteButton: !!to?.closest?.(options.pasteSelector),
      documentHasFocus: hasFocus(),
    });
    if (action === "clear") input.value = "";
  });
}
