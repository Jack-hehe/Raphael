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
  // Measure the real event through its next painted frame, excluding automation transport time.
  if (step.measure) await page.evaluate(({ selector, action }) => {
    window.__look.paint = new Promise(resolve => {
      document.addEventListener(action === "type" ? "input" : "click", function capture(event) {
        if (!event.target.closest(selector)) return;
        document.removeEventListener(action === "type" ? "input" : "click", capture, true);
        const start = performance.now();
        requestAnimationFrame(() => requestAnimationFrame(() => resolve(performance.now() - start)));
      }, true);
    });
  }, step);
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
    case "member-picker": {
      if (await compact()) await click('.mb-pick');
      else {
        if (await page.$eval('.mb-col', column => column.classList.contains('is-collapsed'))) await click('[data-col="collapse"]');
        await click('[data-col="add"]'); await click('#app-popover [data-pick="conversation"]');
      }
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
    case "freeze-motion":
      // Every animation held `at` ms in, also on elements drawn again later, so a moving frame is the same each run.
      await page.addStyleTag({ content: `*, *::before, *::after { animation-delay: -${Number(step.at)}ms !important; animation-play-state: paused !important; }` });
      break;
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
    case "key":
      if (step.shift) await page.keyboard.down("Shift");
      try { await page.keyboard.press(step.key); } finally { if (step.shift) await page.keyboard.up("Shift"); }
      break;
    case "wheel": {
      const element = await visible(step.selector);
      try {
        const box = await element.boundingBox(), x = box.x + box.width / 2, y = box.y + box.height / 3;
        let deltaY = step.deltaY ?? 0;
        if (step.reveal) {
          const target = await visible(step.reveal);
          try { const rect = await target.boundingBox(); deltaY = Math.max(0, rect.y + rect.height + (step.marginBottom ?? 0) - box.y - box.height); }
          finally { await target.dispose(); }
        }
        const point = await page.$eval('.team-board', (board, at) => {
          const box = board.getBoundingClientRect(), scale = new DOMMatrix(getComputedStyle(board).transform).a;
          return { x: (at.x - box.x) / scale, y: (at.y - box.y) / scale };
        }, { x, y });
        await page.mouse.move(x, y);
        if (step.ctrl) await page.keyboard.down('Control');
        try { await page.mouse.wheel({ deltaY, deltaX: step.deltaX ?? 0 }); }
        finally { if (step.ctrl) await page.keyboard.up('Control'); }
        await new Promise(resolve => setTimeout(resolve, 100));
        if (step.ctrl) {
          const after = await page.$eval('.team-board', (board, at) => {
            const box = board.getBoundingClientRect(), scale = new DOMMatrix(getComputedStyle(board).transform).a;
            return { x: (at.x - box.x) / scale, y: (at.y - box.y) / scale, scale };
          }, { x, y });
          if (Math.abs(after.x - point.x) > 1 || Math.abs(after.y - point.y) > 1 || after.scale <= 1) throw new Error('Ctrl+wheel must zoom at the pointer');
        }
      } finally { await element.dispose(); }
      break;
    }
    case "pointer-drag": {
      const element = await visible(step.selector);
      try {
        const box = await element.boundingBox();
        const x = box.x + box.width * (step.x ?? 0.5), y = box.y + box.height * (step.y ?? 0.5);
        const before = await element.evaluate(node => node.getBoundingClientRect().toJSON());
        await page.mouse.move(x, y); await page.mouse.down({ button: step.button ?? "left" });
        try { await page.mouse.move(x + step.dx, y + step.dy, { steps: 12 }); }
        finally { await page.mouse.up({ button: step.button ?? "left" }); }
        if (step.expectMenuClosed && await page.$('#app-popover')) throw new Error('A pointer drag must not open a menu');
        if (step.expectMoved) {
          const after = await element.evaluate(node => node.getBoundingClientRect().toJSON());
          if (Math.abs(after.x - before.x - step.dx) > 1 || Math.abs(after.y - before.y - step.dy) > 1) throw new Error('The group did not follow the pointer');
        }
      } finally { await element.dispose(); }
      break;
    }
    case "right-click": {
      // A real press of the right button in the middle of the element; the page's own `contextmenu` follows it. Where it was is kept for the check.
      const element = await visible(step.selector);
      try {
        await element.scrollIntoView();
        const box = await element.boundingBox();
        const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
        await page.mouse.click(point.x, point.y, { button: "right" });
        await page.evaluate(at => { window.__look.pointer = at; }, point);
      } finally { await element.dispose(); }
      break;
    }
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
    case "drop": case "drag-start": case "drag-over":
      { const element = await visible(step.selector);
      await element.evaluate((element, options) => {
        const transfer = new DataTransfer();
        transfer.setData("application/x-raphael-member", String(options.memberId ?? element.dataset.teamMember));
        element.dispatchEvent(new DragEvent(options.action === "drop" ? "drop" : options.action === "drag-over" ? "dragover" : "dragstart", { bubbles: true, cancelable: true, dataTransfer: transfer }));
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
  if (step.measure) {
    const durationMs = await page.evaluate(() => window.__look.paint);
    if (durationMs > step.measure.budgetMs) throw new Error(`${step.measure.name}: ${durationMs.toFixed(1)}ms exceeds ${step.measure.budgetMs}ms event-to-paint budget`);
    return { check: step.measure.name, durationMs, budgetMs: step.measure.budgetMs };
  }
}
