// View transforms and device-local group placement for the team board.
interface Point { x: number; y: number }
export interface TeamRect extends Point { width: number; height: number }

// The closest free point lies at the desired coordinate or an obstacle boundary on each axis.
export function nearestTeamPosition(rect: TeamRect, obstacles: readonly TeamRect[], gap = 16): Point {
  const blocked = (x: number, y: number): boolean => obstacles.some(other =>
    x < other.x + other.width + gap && x + rect.width + gap > other.x &&
    y < other.y + other.height + gap && y + rect.height + gap > other.y);
  if (!blocked(rect.x, rect.y)) return { x: rect.x, y: rect.y };
  const xs = [rect.x, ...obstacles.flatMap(other => [other.x - rect.width - gap, other.x + other.width + gap])];
  const ys = [rect.y, ...obstacles.flatMap(other => [other.y - rect.height - gap, other.y + other.height + gap])];
  let best = { x: rect.x, y: rect.y }, distance = Infinity;
  for (const x of xs) for (const y of ys) {
    const next = (x - rect.x) ** 2 + (y - rect.y) ** 2;
    if (next < distance && !blocked(x, y)) { best = { x, y }; distance = next; }
  }
  return best;
}
const STORAGE_KEY = "raphael.team.positions";
const clampZoom = (value: number): number => Math.max(0.5, Math.min(1.5, value));

export function createTeamCanvas(viewport: HTMLElement, board: HTMLElement, changed: (zoom: number) => void) {
  let zoom = 1, pan: Point = { x: 0, y: 0 };
  let positions: Record<string, Point> = {};
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      for (const [id, point] of Object.entries(saved)) {
        if (/^(?:none|\d+)$/.test(id) && point && Number.isFinite(point.x) && Number.isFinite(point.y)) positions[id] = { x: point.x, y: point.y };
      }
    }
  } catch { /* An empty or unavailable store keeps automatic layout. */ }
  let gesture: { id: number; start: Point; pan: Point; group?: HTMLElement; origin?: Point; moved: boolean } | null = null;
  let draggedRight = false;
  function save(): void { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(positions)); } catch { /* Keep the layout in memory. */ } }
  function draw(): void {
    board.style.transform = `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`;
    viewport.style.backgroundPosition = `${pan.x}px ${pan.y}px`;
    viewport.style.backgroundSize = `${20 * zoom}px ${20 * zoom}px`;
    changed(zoom);
  }
  const resolved = new Map<HTMLElement, Point>();
  const rect = (node: HTMLElement, point: Point = { x: node.offsetLeft, y: node.offsetTop }): TeamRect => ({ ...point, width: node.offsetWidth, height: node.offsetHeight });
  function chiefBounds(): TeamRect[] {
    const chief = board.querySelector<HTMLElement>(".team-chief .team-member");
    return chief && chief.offsetWidth && chief.offsetHeight ? [rect(chief)] : [];
  }
  function layout(): void {
    resolved.clear();
    const occupied = chiefBounds();
    const groups = [...board.querySelectorAll<HTMLElement>("[data-team-group]")].filter(group => !group.hidden);
    // Manual placements take priority; automatic groups flow around them in roster order.
    groups.sort((a, b) => Number(!!positions[b.dataset.teamGroup!]) - Number(!!positions[a.dataset.teamGroup!]));
    for (const group of groups) {
      const desired = rect(group, positions[group.dataset.teamGroup!]);
      const point = nearestTeamPosition(desired, occupied);
      resolved.set(group, point);
      const dx = point.x - group.offsetLeft, dy = point.y - group.offsetTop;
      group.style.transform = dx || dy ? `translate(${dx}px, ${dy}px)` : "";
      if (desired.width && desired.height) occupied.push({ ...desired, ...point });
    }
    draw();
  }
  function zoomAt(value: number, x = viewport.clientWidth / 2, y = viewport.clientHeight / 2): void {
    const next = clampZoom(value), ratio = next / zoom;
    pan = { x: x - (x - pan.x) * ratio, y: y - (y - pan.y) * ratio };
    zoom = next; layout();
  }
  function revealChief(): void {
    const chief = chiefBounds()[0];
    if (!chief || !viewport.clientWidth || !viewport.clientHeight) return;
    const axis = (value: number, start: number, size: number, available: number): number =>
      Math.max(16 - start * zoom, Math.min(value, available - 16 - (start + size) * zoom));
    pan = { x: axis(pan.x, chief.x, chief.width, viewport.clientWidth), y: axis(pan.y, chief.y, chief.height, viewport.clientHeight) };
    draw();
  }
  function fit(): void {
    layout();
    const nodes = [...board.querySelectorAll<HTMLElement>(".team-chief, [data-team-group]")].filter(node => !node.hidden);
    if (!nodes.length) return;
    const view = viewport.getBoundingClientRect();
    const boxes = nodes.map(node => node.getBoundingClientRect());
    const left = (Math.min(...boxes.map(box => box.left)) - view.left - pan.x) / zoom;
    const top = (Math.min(...boxes.map(box => box.top)) - view.top - pan.y) / zoom;
    const right = (Math.max(...boxes.map(box => box.right)) - view.left - pan.x) / zoom;
    const bottom = (Math.max(...boxes.map(box => box.bottom)) - view.top - pan.y) / zoom;
    if (right <= left || bottom <= top) return;
    zoom = clampZoom(Math.min((viewport.clientWidth - 32) / (right - left), (viewport.clientHeight - 32) / (bottom - top)));
    pan = { x: (viewport.clientWidth - (right - left) * zoom) / 2 - left * zoom, y: Math.max(16, (viewport.clientHeight - (bottom - top) * zoom) / 2) - top * zoom };
    draw(); revealChief();
  }
  viewport.addEventListener("pointerdown", event => {
    if (event.button === 2) draggedRight = false;
    if (event.button > 2 || (event.target as Element).closest("button, input, textarea")) return;
    const target = event.target as Element;
    const group = event.button === 0 ? target.closest<HTMLElement>(".team-group > header")?.parentElement : undefined;
    if (event.button === 0 && !group && target.closest(".team-member, .team-group")) return;
    event.preventDefault();
    gesture = { id: event.pointerId, start: { x: event.clientX, y: event.clientY }, pan: { ...pan }, group: group ?? undefined,
      origin: group ? resolved.get(group) ?? { x: group.offsetLeft, y: group.offsetTop } : undefined, moved: false };
  });
  viewport.addEventListener("pointermove", event => {
    if (!gesture || gesture.id !== event.pointerId) return;
    const dx = event.clientX - gesture.start.x, dy = event.clientY - gesture.start.y;
    if (!gesture.moved && Math.hypot(dx, dy) <= 4) return;
    if (!gesture.moved) viewport.setPointerCapture?.(event.pointerId);
    gesture.moved = true;
    if (event.buttons & 2) draggedRight = true;
    viewport.classList.add("is-panning");
    if (gesture.group && gesture.origin) {
      const others = [...resolved].filter(([group]) => group !== gesture!.group).map(([group, point]) => rect(group, point)).filter(box => box.width && box.height);
      positions[gesture.group.dataset.teamGroup!] = nearestTeamPosition(rect(gesture.group, { x: gesture.origin.x + dx / zoom, y: gesture.origin.y + dy / zoom }), [...chiefBounds(), ...others]);
      layout();
    } else { pan = { x: gesture.pan.x + dx, y: gesture.pan.y + dy }; draw(); }
  });
  const finish = (): void => {
    if (gesture?.group && gesture.moved) save();
    gesture = null; viewport.classList.remove("is-panning");
  };
  viewport.addEventListener("pointerup", finish);
  viewport.addEventListener("pointercancel", finish);
  viewport.addEventListener("lostpointercapture", finish);
  viewport.addEventListener("contextmenu", event => { if (event.button === 2 && draggedRight) event.preventDefault(); });
  viewport.addEventListener("wheel", event => {
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1;
    if (event.ctrlKey) {
      const box = viewport.getBoundingClientRect();
      zoomAt(zoom * Math.exp(-event.deltaY * unit * 0.002), event.clientX - box.left, event.clientY - box.top);
    } else { pan.x -= event.deltaX * unit; pan.y -= event.deltaY * unit; draw(); }
  }, { passive: false });
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(layout);
  observer?.observe(viewport);
  return { layout, zoomBy: (delta: number) => zoomAt(Math.round((zoom + delta) * 10) / 10), fit, revealChief,
    reset() { positions = {}; save(); pan = { x: 0, y: 0 }; zoom = 1; layout(); },
    cancel: finish };
}
