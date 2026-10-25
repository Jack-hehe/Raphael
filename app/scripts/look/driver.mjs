import { checkRendered } from "./checks.mjs";

export function findVisibleElement(selector) {
  return [...document.querySelectorAll(selector)].find(element => {
    const box = element.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return false;
    for (let parent = element; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      if (parent.hidden || style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") return false;
    }
    return true;
  }) ?? null;
}

export async function perform(page, step) {
  // A collapsed work section can precede the visible reply with the same selector.
  const visible = async selector => {
    try { return (await page.waitForFunction(findVisibleElement, { timeout: 6000 }, selector)).asElement(); }
    catch (error) { throw new Error(`No visible match: ${selector}`, { cause: error }); }
  };
  const click = async selector => { const element = await visible(selector); try { await element.click(); } finally { await element.dispose(); } };
  const fill = async (selector, text) => {
    const element = await visible(selector);
    await element.evaluate((input, value) => { input.focus(); input.value = value; input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true })); }, text);
    await element.dispose();
  };
  const compact = () => page.evaluate(() => getComputedStyle(document.querySelector(".mb-pick")).display !== "none");
  const roomsMenu = async () => {
    if (await compact()) { await click(".mb-pick"); await click('#app-popover [data-action="1"]'); }
    else { await click("[data-room-new]"); await page.keyboard.press("Escape"); }
  };
  switch (step.action) {
    case "advance-time":
      // Advance the shared fixed clock, then let production timers observe it.
      await page.evaluate(ms => { window.__look.now = Date.now() + ms; }, step.ms);
      await new Promise(resolve => setTimeout(resolve, 100));
      break;
    case "icon-interactions": {
      const element = await visible(step.selector);
      const state = () => element.evaluate(button => {
        const style = getComputedStyle(button), color = document.createElement("span").style;
        color.color = style.getPropertyValue("--accent-icon");
        return { background: style.backgroundColor, color: style.color, accent: color.color, outline: style.outlineStyle, outlineWidth: style.outlineWidth };
      });
      try {
        await element.hover();
        await new Promise(resolve => setTimeout(resolve, 200));
        const hover = await state();
        await page.mouse.down();
        let active;
        try {
          await new Promise(resolve => setTimeout(resolve, 200));
          active = await state();
        } finally { await page.mouse.move(0, 0); await page.mouse.up(); }
        if ([hover, active].some(value => value.background !== "rgba(0, 0, 0, 0)")) throw new Error("Icon hover/active must remain transparent");
        if (active.color !== active.accent) throw new Error("Pressed icon must use the accent color");
        await page.keyboard.press("Tab");
        await element.focus();
        const focus = await state();
        if (focus.outline === "none" || parseFloat(focus.outlineWidth) < 2) throw new Error("Keyboard icon focus needs a visible ring");
        return { check: "icon-interactions", hover, active, focus };
      } finally { await element.dispose(); }
    }
    case "open-details":
      await visible(".conversation-trigger");
      // Exercise the title from a closed state at every width, including the default wide panel.
      if (await page.$('.conversation-trigger[aria-expanded="true"]')) await click('.mb-det [data-det="close"]');
      await click(".conversation-trigger");
      await visible('.mb-det:not([hidden])');
      break;
    case "check": return page.evaluate(checkRendered, step.name);
    case "click": await click(step.selector); break;
    case "type": await fill(step.selector, step.text); break;
    case "hover": { const element = await visible(step.selector); await element.hover(); await element.dispose(); break; }
    case "wait": { const element = await visible(step.selector); await element.dispose(); break; }
    case "require":
      if (!await page.$(step.selector)) throw new Error(`Unavailable in this checkout: ${step.reason} (${step.selector})`);
      break;
    case "key": await page.keyboard.press(step.key); break;
    case "focus": await page.focus(step.selector); break;
    case "scroll": { const element = await visible(step.selector); await element.evaluate(element => element.scrollIntoView({ block: "end", inline: "nearest" })); await element.dispose(); break; }
    case "wait-scroll-end": {
      const element = await visible(step.selector);
      try { await page.waitForFunction(element => element.scrollTop + element.clientHeight >= element.scrollHeight - 1, { timeout: 6000 }, element); }
      finally { await element.dispose(); }
      break;
    }
    case "member":
      if (await compact()) { await click(".mb-pick"); await click(`#app-popover [data-value="${step.id}"]`); }
      else await click(`.mb-col [data-member="${step.id}"]`);
      break;
    case "room":
      if (await compact()) { await roomsMenu(); await click(`#app-popover [data-value="${step.id}"]`); }
      else await click(`[data-room="${step.id}"]`);
      break;
    case "new-room":
      if (await compact()) { await roomsMenu(); await click('#app-popover [data-action="0"]'); }
      else await click("[data-room-new]");
      break;
    case "room-row-menu":
      if (await compact()) {
        await roomsMenu(); await click(`#app-popover [data-value="${step.id}"]`);
        await click("[data-room-menu]");
      } else {
        const more = await visible(`[data-room-more="${step.id}"]`);
        try { await more.hover(); await more.click(); } finally { await more.dispose(); }
      }
      break;
    case "rooms-menu":
      if (await compact()) await roomsMenu();
      else await page.waitForSelector(".mb-col [data-room]", { visible: true });
      break;
    case "search":
      if (await compact()) { await click(".mb-pick"); await click('#app-popover [data-action="0"]'); await fill("#app-popover input", step.text); }
      else { await click('[data-col="search"]'); await fill(".mb-find-input", step.text); }
      break;
    case "drop": case "drag-start":
      { const element = await visible(step.selector);
      await element.evaluate((element, options) => {
        const transfer = new DataTransfer();
        transfer.setData("application/x-raphael-member", String(options.memberId ?? element.dataset.teamMember));
        element.dispatchEvent(new DragEvent(options.action === "drop" ? "drop" : "dragstart", { bubbles: true, cancelable: true, dataTransfer: transfer }));
      }, step);
      await element.dispose(); }
      break;
    case "disable-abilities":
      await visible("#ob [data-ability]");
      while (await page.$('#ob [data-ability][aria-checked="true"]')) await click('#ob [data-ability][aria-checked="true"]');
      break;
    default: throw new Error(`Unknown scene action: ${step.action}`);
  }
  // Let event handlers, microtasks and popover positioning finish between declarative actions.
  await new Promise(resolve => setTimeout(resolve, 100));
}
