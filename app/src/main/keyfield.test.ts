import { describe, expect, test } from "vitest";
import { keyFieldAction, wireKeyField } from "./keyfield";

describe("keyFieldAction", () => {
  test("Enter submits the key", () => {
    expect(keyFieldAction({ type: "keydown", key: "Enter", isComposing: false })).toBe("submit");
  });

  test("Enter that ends an IME composition does not", () => {
    expect(keyFieldAction({ type: "keydown", key: "Enter", isComposing: true })).toBe("none");
  });

  test("other keys do not", () => {
    for (const key of ["a", "v", "Tab", "Escape", " ", "Backspace"]) {
      expect(keyFieldAction({ type: "keydown", key, isComposing: false })).toBe("none");
    }
  });

  test("leaving the field for another place in the page clears what was typed", () => {
    expect(keyFieldAction({ type: "focusout", toPasteButton: false, documentHasFocus: true })).toBe("clear");
  });

  test("moving to the paste button keeps it, so that the button can send it", () => {
    expect(keyFieldAction({ type: "focusout", toPasteButton: true, documentHasFocus: true })).toBe("none");
  });

  test("switching to another program keeps it: the key may be copied from a browser", () => {
    expect(keyFieldAction({ type: "focusout", toPasteButton: false, documentHasFocus: false })).toBe("none");
  });
});

/** A page root that remembers its listeners, and fields to aim events at. */
function fakeHost() {
  const handlers: Record<string, (e: unknown) => void> = {};
  const host = {
    addEventListener: (type: string, handler: (e: unknown) => void) => {
      handlers[type] = handler;
    },
  } as unknown as HTMLElement;
  return { host, handlers };
}

const field = (id: string, value = "sk-or-v1-typed") => ({ id, value });
const paste = { closest: (selector: string) => (selector === '[data-ob="key-paste"]' ? paste : null) };
const elsewhere = { closest: () => null };

describe("wireKeyField", () => {
  function wired(hasFocus = true) {
    const sent: string[] = [];
    const { host, handlers } = fakeHost();
    wireKeyField(host, {
      inputId: "ob-key-input",
      pasteSelector: '[data-ob="key-paste"]',
      submit: (value) => sent.push(value),
      hasFocus: () => hasFocus,
    });
    return { sent, handlers };
  }

  test("listens to the keyboard and to focus, never to change (which comes when a field is left)", () => {
    expect(Object.keys(wired().handlers).sort()).toEqual(["focusout", "keydown"]);
  });

  test("Enter in the field sends the typed key and stops the key there", () => {
    const { sent, handlers } = wired();
    let prevented = false;
    handlers.keydown({ target: field("ob-key-input"), key: "Enter", isComposing: false, preventDefault: () => (prevented = true) });
    expect(sent).toEqual(["sk-or-v1-typed"]);
    expect(prevented).toBe(true);
  });

  test("Enter in another field sends nothing", () => {
    const { sent, handlers } = wired();
    handlers.keydown({ target: field("ob-limit", "20"), key: "Enter", isComposing: false, preventDefault: () => {} });
    expect(sent).toEqual([]);
  });

  test("leaving the field for the cancel button, the back button or the skip link sends nothing and clears it", () => {
    const { sent, handlers } = wired();
    const input = field("ob-key-input");
    handlers.focusout({ target: input, relatedTarget: elsewhere });
    expect(sent).toEqual([]);
    expect(input.value).toBe("");
  });

  test("clicking on the page itself clears it too", () => {
    const { handlers } = wired();
    const input = field("ob-key-input");
    handlers.focusout({ target: input, relatedTarget: null });
    expect(input.value).toBe("");
  });

  test("moving to the paste button leaves the text for the button", () => {
    const { sent, handlers } = wired();
    const input = field("ob-key-input");
    handlers.focusout({ target: input, relatedTarget: paste });
    expect(input.value).toBe("sk-or-v1-typed");
    expect(sent).toEqual([]);
  });

  test("losing the window's focus leaves the text", () => {
    const { handlers } = wired(false);
    const input = field("ob-key-input");
    handlers.focusout({ target: input, relatedTarget: null });
    expect(input.value).toBe("sk-or-v1-typed");
  });

  test("another field losing focus is none of its business", () => {
    const { handlers } = wired();
    const input = field("ob-limit", "20");
    handlers.focusout({ target: input, relatedTarget: null });
    expect(input.value).toBe("20");
  });
});
