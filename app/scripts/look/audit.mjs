// Self-contained so the same checks run in Chromium and in the DOM contract tests.
export function auditDocument(doc = document, viewport = { width: innerWidth, height: innerHeight }, namespaces = [], englishUI = []) {
  const win = doc.defaultView;
  const describe = element => ({ tag: element.tagName.toLowerCase(), id: element.id, class: typeof element.className === "string" ? element.className : "", text: element.textContent?.trim().slice(0, 100) });
  const visible = element => {
    const box = element.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return false;
    for (let parent = element; parent; parent = parent.parentElement) {
      const style = win.getComputedStyle(parent);
      if (parent.hidden || style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse" || style.opacity === "0") return false;
    }
    return true;
  };
  // Content outside a scrollport is intentionally offscreen. Inspect its container instead.
  const clipped = element => {
    const box = element.getBoundingClientRect();
    for (let parent = element.parentElement; parent && parent !== doc.body; parent = parent.parentElement) {
      const style = win.getComputedStyle(parent), rect = parent.getBoundingClientRect();
      const x = /auto|scroll|hidden|clip/.test(style.overflowX || style.overflow);
      const y = /auto|scroll|hidden|clip/.test(style.overflowY || style.overflow);
      if ((x && (box.left < rect.left - 1 || box.right > rect.right + 1)) || (y && (box.top < rect.top - 1 || box.bottom > rect.bottom + 1))) return true;
    }
    return false;
  };
  const onScreen = element => {
    const box = element.getBoundingClientRect();
    let left = Math.max(0, box.left), top = Math.max(0, box.top), right = Math.min(viewport.width, box.right), bottom = Math.min(viewport.height, box.bottom);
    for (let parent = element.parentElement; parent && parent !== doc.body; parent = parent.parentElement) {
      const style = win.getComputedStyle(parent), rect = parent.getBoundingClientRect();
      if (/auto|scroll|hidden|clip/.test(style.overflowX || style.overflow)) { left = Math.max(left, rect.left); right = Math.min(right, rect.right); }
      if (/auto|scroll|hidden|clip/.test(style.overflowY || style.overflow)) { top = Math.max(top, rect.top); bottom = Math.min(bottom, rect.bottom); }
    }
    return right > left && bottom > top;
  };
  const outsideViewport = [], horizontalOverflow = [], missingI18nKeys = [], nativeControls = [], invalidDurations = [], productRules = [];
  const keyPattern = /\b([a-z][A-Za-z0-9]*\.[A-Za-z0-9_.-]+)\b/g;
  const seenKeys = new Set();
  for (const element of doc.body.querySelectorAll("*")) {
    if (!visible(element)) continue;
    const box = element.getBoundingClientRect(), style = win.getComputedStyle(element);
    const description = describe(element);
    if (!element.matches(".window-resize") && !clipped(element) && (box.left < -1 || box.top < -1 || box.right > viewport.width + 1 || box.bottom > viewport.height + 1))
      outsideViewport.push({ ...description, bounds: { left: box.left, top: box.top, right: box.right, bottom: box.bottom } });
    if (element.clientWidth > 0 && element.scrollWidth > element.clientWidth + 1 && !/auto|scroll|hidden|clip/.test(style.overflowX || style.overflow))
      horizontalOverflow.push({ ...description, width: element.clientWidth, scrollWidth: element.scrollWidth });
    if (!onScreen(element)) continue;
    const violation = (rule, reason) => productRules.push({ ...description, rule, reason });
    if (englishUI.length && element.closest('button, label, input, textarea, h1, h2, h3, [role="tab"], [role="menuitem"], .nav') && !element.closest('.message-text, .ans, .txt')) {
      const copy = [...element.childNodes].filter(node => node.nodeType === 3).map(node => node.textContent.trim());
      for (const attribute of ['aria-label', 'placeholder', 'title']) if (element.hasAttribute(attribute)) copy.push(element.getAttribute(attribute).trim());
      const productOrKeyName = /^(?:Raphael|OpenRouter|OpenAI|Codex|Claude|Gemini|Antigravity|GitHub|Telegram|Google|Notion|MCP|PDF|CLI|Right Alt|Alt|Ctrl|Esc)$/;
      if (copy.some(value => !productOrKeyName.test(value) && englishUI.includes(value))) violation('R-UI-14', 'English interface copy in a zh-TW window');
    }
    if (element.matches('[data-tooltip], [aria-describedby]')) {
      const custom = element.hasAttribute('data-tooltip') || (element.getAttribute('aria-describedby') ?? '').split(/\s+/).some(id => doc.getElementById(id)?.getAttribute('role') === 'tooltip');
      if (custom && (element.hasAttribute('title') || element.querySelector('svg title'))) violation('R-UI-26', 'Custom and native tooltip coexist');
    }
    if (element.matches('button.danger, button[data-row-action="delete"], button[data-row-action="rename"]') && !element.closest('[role="menu"], .menu, .action-row') && element.closest('.mp-resource, .mb-routine, .mb-tool-row, .au-room-row, .mem-row, .rule-row, .word-row, .history-row, .row, [role="listitem"]'))
      violation('R-UI-11', 'Visible list-row delete or rename action');
    if (element.matches('button.btn')) {
      // Navigation cards, list choices and disclosures are not text action buttons.
      // Keep this inventory explicit: ordinary action buttons never opt out by an attribute.
      const navigation = element.matches('.brand, .conversation-trigger, .conversation-open, .mb-panel-row, .mb-panel-back, .mb-chat-choice, .mc-source, .cs-open, .cs-main, .au-room-row > .btn:first-child, .ho-spend, .ho-row, .ho-allowance, .ho-dictation, .au-edit, .steps-sum, .task-sum') || element.closest('.mb-col, .au-month-grid, .au-drag-members, .au-segment') || element.hasAttribute('aria-expanded');
      const hasBorder = ['Top', 'Right', 'Bottom', 'Left'].some(side => parseFloat(style[`border${side}Width`]) > 0 && !['transparent', 'rgba(0, 0, 0, 0)'].includes(style[`border${side}Color`]) && !['none', 'hidden'].includes(style[`border${side}Style`]));
      if (element.classList.contains('icon') && hasBorder) violation('R-UI-18', 'Icon button has a visible border');
      if (!navigation && !element.classList.contains('icon') && element.textContent.trim() && !hasBorder) violation('R-UI-18', 'Text action button has no frame');
    }
    if (element.matches('.action-row')) {
      const buttons = [...element.querySelectorAll('button')].filter(visible);
      const primary = buttons.find(button => button.matches('.pri'));
      if (primary && buttons.some(button => button !== primary && button.getBoundingClientRect().right > primary.getBoundingClientRect().left + 1)) violation('R-UI-27', 'Primary action is not rightmost');
      const destructive = buttons.find(button => button.closest('.action-destructive'));
      if (destructive && buttons.some(button => button !== destructive && button.getBoundingClientRect().left < destructive.getBoundingClientRect().right - 1)) violation('R-UI-27', 'Destructive action is not leftmost');
    }
    if (element.matches('.work-time, .work-toggle, .step-ms') && /\b(?:NaN|Infinity)\b/.test(element.textContent)) invalidDurations.push(description);
    if (element.matches('select,input[type="time"]')) nativeControls.push(description);
    const strings = [...element.childNodes].filter(node => node.nodeType === 3).map(node => node.textContent);
    for (const attribute of ["aria-label", "title", "placeholder"]) if (element.hasAttribute(attribute)) strings.push(element.getAttribute(attribute));
    for (const value of strings) for (const match of value.matchAll(keyPattern)) {
      if (!namespaces.includes(match[1].split(".")[0]) || seenKeys.has(match[1])) continue;
      seenKeys.add(match[1]); missingI18nKeys.push({ ...description, key: match[1] });
    }
  }
  const documentWidth = Math.max(doc.documentElement.scrollWidth, doc.body.scrollWidth);
  return { horizontalOverflow: { document: documentWidth > viewport.width + 1, documentWidth, viewportWidth: viewport.width, elements: horizontalOverflow }, outsideViewport, missingI18nKeys, nativeControls, invalidDurations, productRules };
}

export function failedChecks(result) {
  return result.runtimeErrors.length > 0 || result.horizontalOverflow.document || result.horizontalOverflow.elements.length > 0 || result.outsideViewport.length > 0 || result.missingI18nKeys.length > 0 || result.nativeControls.length > 0 || (result.invalidDurations?.length ?? 0) > 0 || (result.productRules?.length ?? 0) > 0;
}
