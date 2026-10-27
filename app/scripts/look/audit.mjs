// Self-contained so the same checks run in Chromium and in the DOM contract tests.
// `latinAllowed` (lower-case words, names or syntax) enables the Chinese window's language check: any other
// Latin word in visible text, a placeholder, an aria-label or a title is interface copy left in English.
// `overflowOrphans` is the runner's list (it asks the page's context-menu.ts) of the ⋯ buttons that no row of the app's table
// reaches with a right click: each is a violation of R-UI-44.
export function auditDocument(doc = document, viewport = { width: innerWidth, height: innerHeight }, namespaces = [], latinAllowed = null, englishUI = [], overflowOrphans = []) {
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
  const outsideViewport = [], horizontalOverflow = [], missingI18nKeys = [], nativeControls = [], invalidDurations = [], mixedLanguage = [], rawMarkdown = [], productRules = [];
  const markdownPattern = /(?:^|\r?\n)[\t ]*(?:#{1,6}(?:[\t ]|$)|-\s+\[[ xX]\])|\*\*[^*\r\n]+\*\*|`{3,}/;
  // A textarea holds the document its owner is editing (a private skill's SKILL.md), so Markdown there is data, not unrendered copy.
  const checkMarkdown = element => {
    if (element.matches("textarea")) return;
    for (const node of element.childNodes) if (node.nodeType === 3 && markdownPattern.test(node.textContent))
      rawMarkdown.push({ ...describe(element), text: node.textContent.trim().slice(0, 200) });
  };
  if (visible(doc.body) && onScreen(doc.body)) checkMarkdown(doc.body);
  const allowed = latinAllowed && new Set(latinAllowed), seenWords = new Set();
  const phrases = latinAllowed?.filter(name => /\s|=/.test(name)) ?? [];
  const productPattern = phrases.length ? new RegExp(`\\b(?:${phrases.map(name => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\b`, "gi") : null;
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
    if (element.matches('button.btn.icon')) {
      if (!['transparent', 'rgba(0, 0, 0, 0)', ''].includes(style.backgroundColor)) violation('R-UI-16', 'Icon action has a background surface');
      const glyph = element.querySelector(':scope > svg.i');
      if (glyph) {
        const rect = glyph.getBoundingClientRect();
        const sizes = element.matches('.cmp-send') ? [32] : [16, 20];
        // A board the owner zooms scales its glyphs too; the shared size is the unzoomed one.
        let zoom = 1;
        for (let parent = element; parent; parent = parent.parentElement) zoom *= parseFloat(win.getComputedStyle(parent).zoom) || 1;
        if (!sizes.some(size => Math.abs(rect.width / zoom - size) <= .5 && Math.abs(rect.height / zoom - size) <= .5)) violation('R-UI-19', 'Action glyph does not use the shared icon sizes');
        if (Math.abs(rect.left + rect.width / 2 - box.left - box.width / 2) > 1 || Math.abs(rect.top + rect.height / 2 - box.top - box.height / 2) > 1) violation('R-UI-19', 'Action glyph is not centered');
      }
    }
    if (element.matches('.au-month-grid .btn[aria-pressed="true"]')) {
      const luminance = value => {
        const rgb = value.match(/[\d.]+/g)?.slice(0, 3).map(Number);
        if (rgb?.length !== 3) return null;
        return rgb.map(channel => channel / 255).map(channel => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4).reduce((sum, channel, i) => sum + channel * [.2126, .7152, .0722][i], 0);
      };
      const foreground = luminance(style.color), background = luminance(style.backgroundColor);
      if (foreground !== null && background !== null && (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05) < 4.5)
        violation('R-UI-22', 'Selected date has insufficient text contrast');
    }
    if (element.matches('.mp-add')) {
      const controls = [...element.children].filter(child => visible(child) && child.matches('input, .btn'));
      if (controls.length > 1 && Math.max(...controls.map(child => child.getBoundingClientRect().height)) - Math.min(...controls.map(child => child.getBoundingClientRect().height)) > 1)
        violation('R-UI-17', 'Adjacent import controls have different heights');
    }
    if (element.matches('.au-field')) {
      const controls = [...element.children].filter(child => visible(child) && child.matches('.sel, .mb-fin, .btn'));
      if (controls.length > 1 && controls.some(child => Math.abs(child.getBoundingClientRect().width - controls[0].getBoundingClientRect().width) > 1))
        violation('R-UI-17', 'Schedule controls in one column have different widths');
    }
    if (element.matches('.skin-grid, .wgrid')) {
      const cards = [...element.children].filter(visible);
      if (cards.length > 1) {
        const row = cards.filter(card => Math.abs(card.getBoundingClientRect().top - cards[0].getBoundingClientRect().top) <= 1);
        if (row.length !== 2) violation('R-UI-30', 'Long choices must form two card columns');
      }
    }
    if (element.matches('.page-overview .kc-row')) {
      const label = element.querySelector('.kc-name'), keys = element.querySelector('.kc-keys');
      if (label && keys && Math.abs(label.getBoundingClientRect().top + label.getBoundingClientRect().height / 2 - keys.getBoundingClientRect().top - keys.getBoundingClientRect().height / 2) > 2)
        violation('R-UI-17', 'Shortcut label and keys wrap onto separate rows');
    }
    if (element.matches('.modal-body .purpose')) violation('R-UI-02', 'Settings subtitle repeats the pane purpose');
    if (element.matches('.ob-col h2') && parseFloat(style.fontSize) < 28) violation('R-UI-37', 'Onboarding needs a large headline');
    if (element.matches('.ob-actions > .btn.pri') && box.width < element.parentElement.getBoundingClientRect().width * .6)
      violation('R-UI-37', 'Onboarding primary action must fill the action row');
    if (element.matches('.ob-abilities')) {
      const choices = [...element.children].filter(visible);
      if (choices.length > 1 && choices.some((choice, index) => index > 0 && Math.abs(choice.getBoundingClientRect().top - choices[0].getBoundingClientRect().top) <= 1))
        violation('R-UI-35', 'Onboarding abilities must form a vertical checklist');
    }
    if (element.matches('.mc-source-list')) {
      const selected = element.querySelector('.mc-source[aria-pressed="true"]');
      if (selected && visible(selected)) {
        const row = selected.getBoundingClientRect();
        if (row.top < box.top - 1 || row.bottom > box.bottom + 1)
          violation('R-UI-01', 'Selected model source is clipped by its scrollport');
      }
      const footer = element.nextElementSibling;
      if (footer && visible(footer) && footer.getBoundingClientRect().top - box.bottom < 7)
        violation('R-UI-17', 'Model source action touches the final source row');
    }
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
    if (element.matches('button.danger, button[data-row-action="delete"], button[data-row-action="rename"]') && !element.closest('[role="menu"], .menu, .action-row') && element.closest('.mp-resource, .mb-routine, .mb-tool-row, .au-room-row, .mem-row, .rule-row, .rules-row, .word-row, .wrow, .history-row, .hrow, .mb-rt, .row, [role="listitem"]'))
      violation('R-UI-11', 'Visible list-row delete or rename action');
    if (element.matches('button.btn')) {
      // Navigation cards, list choices and disclosures are not text action buttons; an event line
      // that opens its room navigates like the sidebar's room rows.
      // Keep this inventory explicit: ordinary action buttons never opt out by an attribute.
      const navigation = element.matches('.brand, .conversation-trigger, .conversation-open, .mb-panel-row, .mb-panel-back, .mb-chat-choice, .mc-source, .cs-open, .cs-main, .au-room-row > .btn:first-child, .event-open, .ho-spend, .ho-row, .ho-allowance, .ho-dictation, .au-edit, .steps-sum, .task-sum') || element.closest('.mb-col, .au-month-grid, .au-drag-members, .au-segment, .mc-segments') || element.hasAttribute('aria-expanded');
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
    checkMarkdown(element);
    if (element.matches('.work-time, .work-toggle, .step-ms') && /\b(?:NaN|Infinity)\b/.test(element.textContent)) invalidDurations.push(description);
    if (element.matches('select,input[type="time"]')) nativeControls.push(description);
    // Textarea values are user data, like input.value; their labels and placeholders remain UI copy.
    const strings = element.matches("textarea") ? [] : [...element.childNodes].filter(node => node.nodeType === 3).map(node => node.textContent);
    for (const attribute of ["aria-label", "title", "placeholder"]) if (element.hasAttribute(attribute)) strings.push(element.getAttribute(attribute));
    for (const value of strings) for (const match of value.matchAll(keyPattern)) {
      if (!namespaces.includes(match[1].split(".")[0]) || seenKeys.has(match[1])) continue;
      seenKeys.add(match[1]); missingI18nKeys.push({ ...description, key: match[1] });
    }
    // The literal command-template token is syntax in every locale, not English copy.
    if (allowed) for (const value of strings) {
      const copy = productPattern ? value.replace(productPattern, "") : value;
      for (const [word] of copy.replace(/\{input\}/g, "").replace(keyPattern, "").replace(/https?:\/\/\S+|\S+\.(?:pdf|png|jpe?g|txt|md|csv|exe|com|dev|io)\b/gi, "").matchAll(/[A-Za-z]{2,}/g)) {
        if (allowed.has(word.toLowerCase()) || seenWords.has(word)) continue;
        seenWords.add(word); mixedLanguage.push({ ...description, word });
      }
    }
  }
  for (const orphan of overflowOrphans) productRules.push({ ...orphan, rule: 'R-UI-44', reason: 'A ⋯ button that a right click on its row does not reach' });
  const documentWidth = Math.max(doc.documentElement.scrollWidth, doc.body.scrollWidth);
  return { horizontalOverflow: { document: documentWidth > viewport.width + 1, documentWidth, viewportWidth: viewport.width, elements: horizontalOverflow }, outsideViewport, missingI18nKeys, nativeControls, invalidDurations, mixedLanguage, rawMarkdown, productRules };
}

export function failedChecks(result) {
  return result.runtimeErrors.length > 0 || result.horizontalOverflow.document || result.horizontalOverflow.elements.length > 0 || result.outsideViewport.length > 0 || result.missingI18nKeys.length > 0 || result.nativeControls.length > 0 || (result.invalidDurations?.length ?? 0) > 0 || (result.mixedLanguage?.length ?? 0) > 0 || (result.rawMarkdown?.length ?? 0) > 0 || (result.productRules?.length ?? 0) > 0;
}
