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
  const click = async selector => {
    for (let attempt = 0; ; attempt++) {
      const element = await visible(selector);
      try { await element.click(); return; }
      catch (error) {
        // A production redraw can replace a menu between lookup and pointer dispatch.
        // Retry only pre-dispatch stale/unclickable handles; never replay app errors.
        if (attempt >= 2 || !/Node is detached|Node is either not clickable or not an Element/.test(error.message)) throw error;
      } finally { await element.dispose(); }
    }
  };
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
    case "window-control": {
      const selector = `[data-window-action="${step.control}"]`;
      if (step.restore) {
        await click(selector);
        await page.waitForSelector(`${selector}[aria-pressed="true"]`);
      }
      await page.mouse.move(0, 100);
      const element = await visible(selector);
      const measure = () => element.evaluate(button => {
        const box = button.getBoundingClientRect(), style = getComputedStyle(button);
        const glyph = [...button.querySelectorAll('svg')].find(svg => getComputedStyle(svg).display !== 'none').getBoundingClientRect();
        const strip = button.parentElement.getBoundingClientRect();
        const color = token => { const probe = document.createElement('span'); probe.style.color = `var(${token})`; button.append(probe); const value = getComputedStyle(probe).color; probe.remove(); return value; };
        return { background: style.backgroundColor, color: style.color, radius: parseFloat(style.borderRadius),
          x: glyph.x + glyph.width / 2, y: glyph.y + glyph.height / 2,
          centerX: box.x + box.width / 2, centerY: box.y + box.height / 2,
          marginTop: box.top - strip.top, marginBottom: strip.bottom - box.bottom,
          hover: color('--hover'), pressed: color('--pressed'), close: color('--close'), onClose: color('--on-close'),
          title: button.title, label: button.getAttribute('aria-label') };
      });
      const rest = await measure();
      await element.hover();
      await new Promise(resolve => setTimeout(resolve, 160));
      const hovered = await measure();
      if (hovered.background !== (step.control === 'close' ? hovered.close : hovered.hover) || hovered.radius <= 0) throw new Error('Caption hover needs a rounded token surface');
      if (Math.abs(hovered.centerX - hovered.x) > .5 || Math.abs(hovered.centerY - hovered.y) > .5 || Math.abs(rest.x - hovered.x) > .5 || Math.abs(rest.y - hovered.y) > .5) throw new Error('Caption glyph moved or is not centered');
      if (hovered.marginTop < 3 || Math.abs(hovered.marginTop - hovered.marginBottom) > .5) throw new Error('Caption hover needs even inner margins');
      if (!hovered.label || hovered.title !== hovered.label) throw new Error('Caption tooltip and accessible label must remain');
      if (step.control === 'close') {
        const rgb = value => value.match(/[\d.]+/g).slice(0, 3).map(Number).map(c => c / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4);
        const luminance = value => rgb(value).reduce((sum, c, i) => sum + c * [.2126, .7152, .0722][i], 0);
        const a = luminance(hovered.background), b = luminance(hovered.color);
        if ((Math.max(a, b) + .05) / (Math.min(a, b) + .05) < 4.5 || hovered.color !== hovered.onClose) throw new Error('Close glyph must contrast with its error surface');
      }
      if (step.hoverOnly) {
        if (!await page.evaluate(() => document.documentElement.dataset.windowModal === 'true' && !!document.querySelector('.modal-scrim:not([hidden])'))) throw new Error('Caption hover must preserve the open settings modal');
        await element.dispose();
        return { check: "window-control", control: step.control, hovered, modal: true };
      }
      // Release away from the caption so this assertion never minimizes or closes the page.
      await page.mouse.down();
      let pressed;
      try { await new Promise(resolve => setTimeout(resolve, 160)); pressed = await measure(); }
      finally { await page.mouse.move(0, 100); await page.mouse.up(); }
      if (pressed.background !== (step.control === 'close' ? pressed.close : pressed.pressed)) throw new Error('Caption press needs the pressed token');
      await page.keyboard.press('Tab'); await element.focus();
      const focus = await element.evaluate(button => { const style = getComputedStyle(button); return { style: style.outlineStyle, width: parseFloat(style.outlineWidth) }; });
      if (focus.style === 'none' || focus.width < 2) throw new Error('Caption keyboard focus needs a visible ring');
      await element.evaluate(button => button.blur());
      await element.hover(); await new Promise(resolve => setTimeout(resolve, 160));
      await element.dispose();
      return { check: "window-control", control: step.control, restore: !!step.restore, hovered, pressed, focus };
    }
    case "main-event": {
      // Replay only the existing native event boundary; navigation and cards remain real.
      await page.evaluate(event => window.__look.emit(event), step.event);
      break;
    }
    case "dictation-paste": {
      await page.focus(step.selector);
      await page.keyboard.type(step.text);
      // Only the existing native event boundary is synthetic; the composer is real.
      await page.evaluate(text => window.__look.emit({ type: "DictationPasted", text }), step.text);
      break;
    }
    case "calendar-tools": {
      if (await page.evaluate(() => document.querySelector('[data-au="tools"]').getBoundingClientRect().width > 0)) await click('[data-au="tools"]');
      break;
    }
    case "member-column": {
      const collapsed = await page.$eval('.mb-col', column => column.classList.contains('is-collapsed'));
      if (collapsed !== step.collapsed) await click('[data-col="collapse"]');
      else if (await page.evaluate(() => localStorage.getItem('raphael.members.collapsed')) !== String(collapsed)) {
        await click('[data-col="collapse"]');
        await click('[data-col="collapse"]');
      }
      break;
    }
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
    case "wait-gone":
      // A result that is an absence (an uninstalled app's row, a closed dialog): the selector no longer matches anything.
      try { await page.waitForFunction(selector => !document.querySelector(selector), { timeout: 6000 }, step.selector); }
      catch (error) { throw new Error(`Still present: ${step.selector}`, { cause: error }); }
      break;
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
