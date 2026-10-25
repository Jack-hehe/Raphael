// Self-contained so the same checks run in Chromium and in the DOM contract tests.
export function auditDocument(doc = document, viewport = { width: innerWidth, height: innerHeight }, namespaces = []) {
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
  const outsideViewport = [], horizontalOverflow = [], missingI18nKeys = [], nativeControls = [], invalidDurations = [];
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
  return { horizontalOverflow: { document: documentWidth > viewport.width + 1, documentWidth, viewportWidth: viewport.width, elements: horizontalOverflow }, outsideViewport, missingI18nKeys, nativeControls, invalidDurations };
}

export function failedChecks(result) {
  return result.runtimeErrors.length > 0 || result.horizontalOverflow.document || result.horizontalOverflow.elements.length > 0 || result.outsideViewport.length > 0 || result.missingI18nKeys.length > 0 || result.nativeControls.length > 0 || (result.invalidDurations?.length ?? 0) > 0;
}
