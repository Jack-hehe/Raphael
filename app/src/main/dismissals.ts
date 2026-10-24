// One outside-press policy for modal panels and their nested menus.
interface Layer {
  panel(): HTMLElement | null;
  close(): void;
  canClose(): boolean;
  anchor?: HTMLElement;
}
const layers: Layer[] = [];
let listening = false;
let gesture: { layer: Layer; panel: HTMLElement; pointer: number; outside: boolean; ended: boolean; handled: boolean } | null = null;

function top(): { layer: Layer; panel: HTMLElement } | null {
  for (let i = layers.length - 1; i >= 0; i--) {
    const panel = layers[i].panel();
    if (panel?.isConnected) return { layer: layers[i], panel };
  }
  return null;
}
const inside = (layer: Layer, panel: HTMLElement, target: EventTarget | null): boolean =>
  target instanceof Node && (panel.contains(target) || !!layer.anchor?.contains(target));

function listen(): void {
  if (listening) return;
  listening = true;
  document.addEventListener("pointerdown", (event) => {
    const current = top();
    gesture = event.button === 0 && event.isPrimary !== false && current ? {
      ...current, pointer: event.pointerId, outside: !inside(current.layer, current.panel, event.target), ended: false, handled: false,
    } : null;
    if (gesture?.outside) event.preventDefault();
  }, true);
  document.addEventListener("pointerup", (event) => {
    if (!gesture) return;
    gesture.ended = event.button === 0 && event.pointerId === gesture.pointer;
    gesture.outside &&= gesture.ended && !inside(gesture.layer, gesture.panel, event.target);
    const current = top();
    if (!gesture.outside || !current || gesture.layer !== current.layer || gesture.panel !== current.panel) return;
    gesture.handled = true;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (current.layer.canClose()) current.layer.close();
  }, true);
  const cancel = (): void => { if (gesture) gesture.outside = false; };
  document.addEventListener("pointercancel", cancel, true);
  window.addEventListener("blur", cancel);
  document.addEventListener("keydown", () => { gesture = null; }, true);
  document.addEventListener("click", (event) => {
    const press = gesture;
    gesture = null;
    if (event.button !== 0) return;
    if (press?.handled) {
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    const current = top();
    if (!current || inside(current.layer, current.panel, event.target)) return;
    // A keyboard/assistive activation has no pointer sequence. Real presses need both endpoints.
    if (press ? !press.outside || !press.ended || press.layer !== current.layer || press.panel !== current.panel : event.detail !== 0) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (current.layer.canClose()) current.layer.close();
  }, true);
}

/** Register a panel, or a getter when rendering replaces its DOM; dispose with its close/reset. */
export function registerDismissal(
  panel: HTMLElement | (() => HTMLElement | null), close: () => void,
  canClose: () => boolean = () => true, anchor?: HTMLElement,
): () => void {
  listen();
  const layer: Layer = { panel: typeof panel === "function" ? panel : () => panel, close, canClose, anchor };
  layers.push(layer);
  return () => {
    const index = layers.indexOf(layer);
    if (index !== -1) layers.splice(index, 1);
  };
}

/** Escape and focus handlers use the same layer order as outside presses. */
export const isTopLayer = (panel: HTMLElement | null): boolean => !!panel && top()?.panel === panel;
