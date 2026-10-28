// Which way focus last moved: by the keyboard, or under the pointer.
//
// A focus ring is for someone who moves through the window with Tab and cannot otherwise see where
// they are. Under the pointer it is noise: a click, or typing into a field, already shows where the
// focus is, by the press itself and by the caret. Chromium's own :focus-visible does not draw that
// line well enough for this app. It treats any key, Alt among them, as keyboard use, so a button
// clicked after Right Alt (dictation) or after in-app voice mode shows a ring, and a text field
// matches :focus-visible on every click.
//
// So the rings in the stylesheets are drawn only under `:root[data-keys]`. This module sets that
// attribute on <html> when Tab is pressed, the one key that moves focus from one control to the
// next, and takes it away again on any press of the pointer. Arrow keys inside a menu or a group
// move focus too, but those places show the focused item by its fill, which is never gated.

/** The attribute on <html> under which the stylesheets draw their focus rings. */
export const KEYS_ATTRIBUTE = "data-keys";

/**
 * Follows the keyboard and the pointer on `doc` and marks its root element while Tab moved focus
 * last. The listeners are on the capture phase, so that a handler which stops an event further
 * down (a menu that takes Tab for itself, a canvas that keeps its pointer) cannot hide it from here.
 * Returns the function that removes them and the mark.
 */
export function installFocusKeys(doc: Document = document): () => void {
  const root = doc.documentElement;
  const onKey = (event: KeyboardEvent): void => {
    if (event.key === "Tab") root.setAttribute(KEYS_ATTRIBUTE, "");
  };
  const onPointer = (): void => {
    root.removeAttribute(KEYS_ATTRIBUTE);
  };
  doc.addEventListener("keydown", onKey, true);
  doc.addEventListener("pointerdown", onPointer, true);
  return () => {
    doc.removeEventListener("keydown", onKey, true);
    doc.removeEventListener("pointerdown", onPointer, true);
    root.removeAttribute(KEYS_ATTRIBUTE);
  };
}
