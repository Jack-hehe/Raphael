// The rose of the personalization card: one petal per kind of program, all the same width and
// reaching as far out as the square root of the share of the dictation they hold, so that the AREA
// of a petal is its share and the biggest petal reaches the rim. The kinds keep their place, so
// that the picture is a shape one gets to know, and it is the same shape in every language.

import { esc } from "./ui";

export interface RoseOptions {
  /** The side of the square the rose is drawn in. */
  size?: number;
  /** The shortest a petal is drawn that has anything in it, so that a small share still shows. */
  minRadius?: number;
}

export interface Petal {
  index: number;
  /** Where the petal begins and ends, in radians clockwise from the top. */
  start: number;
  end: number;
  /** How far out it reaches (0 for no share at all). */
  radius: number;
  /** The wedge as an SVG path; empty without a radius. */
  path: string;
}

export interface Rose {
  size: number;
  center: number;
  /** The rim: how far the biggest petal reaches. */
  radius: number;
  /** Radii of the guide circles, a third of the way out, two thirds and the rim. */
  rings: number[];
  petals: Petal[];
}

/** A coordinate with at most two decimals. */
const num = (n: number): string => String(Math.round(n * 100) / 100);

/** Where a point at `radius` and `angle` (clockwise from the top) is, for a rose centred at `c`. */
const point = (c: number, radius: number, angle: number): [number, number] => [c + radius * Math.sin(angle), c - radius * Math.cos(angle)];

/** The wedge from `start` to `end` that reaches out to `radius`. */
export function wedgePath(center: number, radius: number, start: number, end: number): string {
  if (!(radius > 0)) return "";
  if (end - start >= 2 * Math.PI - 1e-9) {
    // a single petal is the whole circle, which one arc cannot draw
    const [x, y] = point(center, radius, 0);
    const [bx, by] = point(center, radius, Math.PI);
    return `M${num(x)} ${num(y)}A${num(radius)} ${num(radius)} 0 1 1 ${num(bx)} ${num(by)}A${num(radius)} ${num(radius)} 0 1 1 ${num(x)} ${num(y)}Z`;
  }
  const [x1, y1] = point(center, radius, start);
  const [x2, y2] = point(center, radius, end);
  const large = end - start > Math.PI ? 1 : 0;
  return `M${num(center)} ${num(center)}L${num(x1)} ${num(y1)}A${num(radius)} ${num(radius)} 0 ${large} 1 ${num(x2)} ${num(y2)}Z`;
}

/** The petals for `weights` (any amounts; negative and unknown count as nothing). */
export function roseGeometry(weights: readonly number[], options: RoseOptions = {}): Rose {
  const size = options.size ?? 220;
  const minRadius = options.minRadius ?? 4;
  const center = size / 2;
  // a little air between the rim and the edge of the picture, for the line of the guide circle
  const rim = center - 2;
  const amounts = weights.map((w) => (Number.isFinite(w) && w > 0 ? w : 0));
  const biggest = Math.max(0, ...amounts);
  const step = (2 * Math.PI) / Math.max(1, amounts.length);
  const petals = amounts.map((weight, index): Petal => {
    const radius = weight > 0 ? Math.min(rim, Math.max(minRadius, rim * Math.sqrt(weight / biggest))) : 0;
    const start = index * step;
    const end = (index + 1) * step;
    return { index, start, end, radius, path: wedgePath(center, radius, start, end) };
  });
  return { size, center, radius: rim, rings: [rim / 3, (2 * rim) / 3, rim], petals };
}

export interface PetalView {
  /** Which kind it is: becomes a class (`p-ai`), so it must be a plain word. */
  id: string;
  /** What the mouse says over the petal. */
  title: string;
}

/** The rose as inline SVG. `label` is what a screen reader says for the whole picture. */
export function roseSVG(rose: Rose, views: readonly PetalView[], label: string): string {
  const rings = rose.rings.map((r) => `<circle class="rose-ring" cx="${num(rose.center)}" cy="${num(rose.center)}" r="${num(r)}"/>`).join("");
  const petals = rose.petals
    .filter((petal) => petal.path !== "")
    .map((petal) => {
      const view = views[petal.index];
      const id = (view?.id ?? "").replace(/[^a-z0-9-]/gi, "");
      return `<path class="rose-petal p-${id}" d="${petal.path}"><title>${esc(view?.title ?? "")}</title></path>`;
    })
    .join("");
  return (
    `<svg class="rose" width="${rose.size}" height="${rose.size}" viewBox="0 0 ${rose.size} ${rose.size}" role="img" aria-label="${esc(label)}">` +
    rings +
    petals +
    `</svg>`
  );
}
