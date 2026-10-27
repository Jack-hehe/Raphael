// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { createTeamCanvas, nearestTeamPosition, type TeamRect } from "./team-canvas";

const intersects = (a: TeamRect, b: TeamRect): boolean => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
afterEach(() => { document.body.innerHTML = ""; localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("a colliding drop uses the closest free edge, retaining its other coordinate", () => {
  const rect = { x: 85, y: 25, width: 60, height: 40 };
  expect(nearestTeamPosition(rect, [{ x: 0, y: 0, width: 100, height: 100 }])).toEqual({ x: 116, y: 25 });
  expect(nearestTeamPosition({ ...rect, x: 300 }, [{ x: 0, y: 0, width: 100, height: 100 }])).toEqual({ x: 300, y: 25 });
});

test("resolution considers all obstacles rather than pushing into the next panel", () => {
  const obstacles = [{ x: 0, y: 0, width: 100, height: 100 }, { x: 116, y: 0, width: 100, height: 100 }];
  const rect = { x: 100, y: 30, width: 40, height: 40 };
  expect(nearestTeamPosition(rect, obstacles)).toEqual({ x: 100, y: -56 });
  expect(nearestTeamPosition(rect, [...obstacles].reverse())).toEqual({ x: 100, y: -56 });
});

function fixture(saved: Record<string, { x: number; y: number }> = {}) {
  localStorage.setItem("raphael.team.positions", JSON.stringify(saved));
  const viewport = document.createElement("div"), board = document.createElement("div");
  board.innerHTML = '<div class="team-chief"><article class="team-member"></article></div><section class="team-group" data-team-group="1"><header>First</header></section><section class="team-group" data-team-group="2"><header>Second</header></section>';
  viewport.append(board); document.body.append(viewport);
  const chief = board.querySelector<HTMLElement>(".team-member")!, first = board.querySelector<HTMLElement>('[data-team-group="1"]')!, second = board.querySelector<HTMLElement>('[data-team-group="2"]')!;
  const boxes = new Map<HTMLElement, TeamRect>([[chief, { x: 0, y: 0, width: 200, height: 100 }], [first, { x: 0, y: 140, width: 120, height: 140 }], [second, { x: 160, y: 140, width: 120, height: 100 }]]);
  for (const [node] of boxes) for (const [prop, key] of [["offsetLeft", "x"], ["offsetTop", "y"], ["offsetWidth", "width"], ["offsetHeight", "height"]] as const) Object.defineProperty(node, prop, { get: () => boxes.get(node)![key] });
  let resize = () => {};
  vi.stubGlobal("ResizeObserver", class { constructor(callback: () => void) { resize = callback; } observe() {} });
  const canvas = createTeamCanvas(viewport, board, vi.fn()); canvas.layout();
  const drawn = (node: HTMLElement): TeamRect => {
    const [dx, dy] = node.style.transform.match(/-?[\d.]+/g)?.map(Number) ?? [0, 0];
    const box = boxes.get(node)!; return { ...box, x: box.x + dx, y: box.y + dy };
  };
  const separated = (): void => {
    const all = [...boxes.keys()].filter(node => !node.hidden).map(drawn);
    for (let i = 0; i < all.length; i++) for (let j = i + 1; j < all.length; j++) expect(intersects(all[i], all[j])).toBe(false);
  };
  return { canvas, viewport, first, second, chief, boxes, drawn, separated, resize: () => resize() };
}

test("automatic panels flow around saved panels, and a hidden group is not an obstacle", () => {
  const f = fixture({ "1": { x: 140, y: 140 } });
  expect(f.drawn(f.first)).toMatchObject({ x: 140, y: 140 }); expect(f.second.style.transform).not.toBe(""); f.separated();
  f.first.hidden = true; f.canvas.layout(); expect(f.second.style.transform).toBe("");
});

test("loading and narrowing resolve saved collisions with the chief and each other without rewriting preferences", () => {
  const saved = { "1": { x: 40, y: 40 }, "2": { x: 170, y: 140 } }, f = fixture(saved);
  f.separated();
  f.boxes.set(f.first, { x: 0, y: 140, width: 278, height: 300 }); f.boxes.set(f.second, { x: 0, y: 464, width: 278, height: 200 });
  f.resize(); f.separated();
  f.canvas.zoomBy(0.1); f.separated();
  expect(JSON.parse(localStorage.getItem("raphael.team.positions")!)).toEqual(saved);
  f.canvas.reset(); f.separated(); expect(f.first.style.transform).toBe(""); expect(f.second.style.transform).toBe("");
});

test("dragging into a panel saves the nearest free drop, using the currently resolved origin", () => {
  const f = fixture();
  const pointer = (type: string, x: number, y: number): void => { f.first.querySelector("header")!.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1, button: 0, buttons: type === "pointerup" ? 0 : 1, clientX: x, clientY: y })); };
  pointer("pointerdown", 10, 150); pointer("pointermove", 130, 150); pointer("pointerup", 130, 150);
  f.separated(); expect(f.drawn(f.first)).toMatchObject({ x: 24, y: 140 });
  expect(JSON.parse(localStorage.getItem("raphael.team.positions")!)["1"]).toEqual({ x: 24, y: 140 });
  f.canvas.layout(); f.separated();
});

test("a growing drop preview triggers another collision-free layout", () => {
  const f = fixture({ "1": { x: 140, y: 140 }, "2": { x: 140, y: 296 } });
  f.boxes.set(f.first, { x: 0, y: 140, width: 120, height: 280 }); f.canvas.layout(); f.separated();
  expect(f.drawn(f.second)).toMatchObject({ x: 4, y: 296 });
});
