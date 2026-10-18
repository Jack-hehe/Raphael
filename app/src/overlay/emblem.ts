// Raphael's emblem, recreated frame by frame from the anime (spec section 6).
//
// This is the approved renderer, ported from the preview mockups
// (.superpowers/brainstorm/.../raphael-emblem-v3.html and raphael-settings.html). Every number in
// the tables and in `params` / `drawEmblem` was measured from the anime clip: change none of them
// without measuring again. Radii are in units of R, the emblem's outer radius.
//
// This module must stay importable without a browser (the tests run in Node), so nothing here
// touches `document`, `window` or `Image` until a function that needs it is called.

import { ProgressArc, type ArcSample, type ProgressReport } from "./progress";

const TAU = Math.PI * 2;
const clamp = (x: number, a = 0, b = 1): number => Math.max(a, Math.min(b, x));

/** What the emblem shows while it is on screen. */
export type Mode = "listening" | "thinking" | "speaking" | "steady";
/** `hidden` draws nothing; `appear` and `vanish` are the two animations from the anime. */
export type Phase = "hidden" | "appear" | "vanish" | Mode;

/** Keyframes: `[seconds, value]` pairs with linear interpolation between them. */
export type Keyframes = ReadonlyArray<readonly [number, number]>;

export function kf(table: Keyframes, t: number): number {
  if (t <= table[0][0]) return table[0][1];
  for (let i = 1; i < table.length; i++) {
    if (t <= table[i][0]) {
      const [t0, v0] = table[i - 1];
      const [t1, v1] = table[i];
      return v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
    }
  }
  return table[table.length - 1][1];
}

/** The anime effect runs at 24 frames per second: every effect clock is quantized to it. */
export const tick = (t: number): number => Math.floor(t * 24 + 1e-6) / 24;

// ---------- numbers measured from the anime clip ----------

/** The glyph ring turns clockwise, in radians per second (3.55 degrees). */
export const RATE = (3.55 * Math.PI) / 180;
/** Half the texture's size in units of R. */
const TEX_HALF = 104 / 97;
const RING_R = 0.6;

/** The appear animation, 0.833 s. */
export const AP = {
  dur: 0.833,
  point: [[0, 0.012], [0.033, 0.02], [0.067, 0.034], [0.133, 0.045], [0.167, 0.05]],
  pointA: [[0, 0.55], [0.033, 0.85], [0.067, 1]],
  ringR: [[0.167, 0.34], [0.2, 0.42], [0.233, 0.5], [0.3, 0.55], [0.333, 0.57], [0.367, 0.585], [0.433, 0.6]],
  glyphR: [[0.133, 0.08], [0.167, 0.12], [0.2, 0.16], [0.233, 0.21], [0.3, 0.26], [0.333, 0.31], [0.367, 0.37], [0.4, 0.43], [0.467, 0.48], [0.5, 0.53], [0.533, 0.62], [0.567, 0.68], [0.6, 0.71], [0.667, 0.75], [0.7, 0.79], [0.733, 0.82], [0.767, 0.84], [0.833, 0.86]],
  glyphMix: [[0.133, 0], [0.233, 0.14], [0.333, 0.23], [0.467, 0.32], [0.5, 0.41], [0.533, 0.5], [0.567, 0.77], [0.6, 0.86], [0.667, 0.95], [0.7, 1]],
  glyphGlow: [[0.133, 1], [0.267, 0.8], [0.4, 0.35], [0.533, 0.1], [0.633, 0]],
  haloA: [[0.2, 0], [0.267, 0.8], [0.367, 0.6], [0.433, 0]],
  rays: [[0.15, 0.05], [0.2, 0.15], [0.267, 0.3], [0.333, 0.5], [0.4, 0.75], [0.5, 1]],
} as const satisfies Record<string, number | Keyframes>;

/** The vanish animation, 0.467 s. */
export const VA = {
  dur: 0.467,
  ringR: [[0, 0.59], [0.033, 0.55], [0.067, 0.52], [0.1, 0.46], [0.167, 0.41], [0.2, 0.35], [0.233, 0.3], [0.267, 0.24], [0.333, 0.18], [0.367, 0.13], [0.4, 0.08], [0.433, 0.04], [0.467, 0]],
  ringW: [[0, 0.06], [0.2, 0.05], [0.267, 0.04], [0.333, 0.03], [0.4, 0.022], [0.467, 0.015]],
  glyphA: [[0, 1], [0.033, 0.95], [0.067, 0.85], [0.1, 0.7], [0.167, 0.45], [0.2, 0.32], [0.233, 0.2], [0.267, 0.12], [0.333, 0.04], [0.367, 0]],
  rays: [[0, 1], [0.167, 0.6], [0.267, 0.2], [0.333, 0]],
  coreA: [[0, 1], [0.3, 0.7], [0.333, 0.6], [0.367, 0.9], [0.4, 0.9], [0.433, 0.3], [0.467, 0]],
} as const satisfies Record<string, number | Keyframes>;

// ---------- how a frame looks ----------

/** Everything `drawEmblem` needs to know about one frame (radii in units of R). */
export interface Params {
  coreR: number;
  coreA: number;
  ringR: number;
  ringW: number;
  ringA: number;
  glyphR: number;
  glyphA: number;
  glyphMix: number;
  glyphGlow: number;
  glyphBlur: number;
  rays: number;
  raysScale: number;
  haloA: number;
  sparkT: number;
}

/**
 * The look of the emblem `pt` seconds into `phase`; `lvl` (0 to 1) is the loudness that sets the
 * white ring's width. Returns null when nothing is drawn. Every phase that is not `hidden`,
 * `appear` or `vanish` looks the same (the modes differ in rotation speed, brightness and in
 * where `lvl` comes from, see `Emblem`).
 */
export function params(phase: Phase, pt: number, lvl: number): Params | null {
  if (phase === "hidden") return null;
  const p: Params = {
    coreR: 0.08 + 0.03 * lvl,
    coreA: 1,
    ringR: RING_R,
    ringW: 0.012 + lvl * 0.062,
    ringA: 1,
    glyphR: 0.86,
    glyphA: 1,
    glyphMix: 1,
    glyphGlow: 0,
    glyphBlur: 0,
    rays: 1,
    raysScale: 1,
    haloA: 0,
    sparkT: -1,
  };
  if (phase === "appear") {
    if (pt < 0.15) {
      p.coreR = kf(AP.point, pt);
      p.coreA = kf(AP.pointA, pt);
      p.ringA = 0;
      p.rays = 0;
    } else {
      p.ringR = kf(AP.ringR, pt);
      p.ringW = pt < 0.4 ? 0.03 : p.ringW;
      p.rays = kf(AP.rays, pt);
    }
    if (pt >= 0.133) {
      p.glyphR = kf(AP.glyphR, pt);
      p.glyphMix = kf(AP.glyphMix, pt);
      p.glyphGlow = kf(AP.glyphGlow, pt);
    } else {
      p.glyphA = 0;
    }
    p.haloA = kf(AP.haloA, pt);
    p.sparkT = pt;
  } else if (phase === "vanish") {
    p.ringR = kf(VA.ringR, pt);
    p.ringW = kf(VA.ringW, pt);
    p.ringA = p.ringR > 0.01 ? 1 : 0;
    p.glyphA = kf(VA.glyphA, pt);
    p.glyphBlur = Math.max(0, 0.5 - p.glyphA) * 0.06;
    p.rays = kf(VA.rays, pt);
    p.raysScale = Math.max(0.15, p.ringR / RING_R);
    p.coreA = kf(VA.coreA, pt);
    p.coreR = 0.08 * Math.max(0.25, p.ringR / RING_R);
  }
  return p;
}

/** A small seeded random generator: the same seed draws the same wobbly ring and rays. */
function mulberry32(seed: number): () => number {
  let a = seed;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------- the glyph texture ----------

// Sampled from the anime, for personal use only (gitignored, see scripts/copy-private.mjs).
// Without it a plain vector ring is drawn.
let glyph: HTMLImageElement | null = null;
let tintCanvas: HTMLCanvasElement | null = null;
/** Redraw requests for the emblems when the texture arrives (set by the emblem loop below). */
let onTextureLoaded: () => void = () => {};

/**
 * Loads the glyph ring texture and uses it from now on. A failed load returns null and the
 * plain vector ring is drawn.
 */
export function loadGlyphTexture(url: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    if (typeof Image === "undefined") {
      resolve(null);
      return;
    }
    const image = new Image();
    image.onload = () => {
      glyph = image;
      onTextureLoaded();
      resolve(image);
    };
    image.onerror = () => resolve(null);
    image.src = url;
  });
}

/** The texture, tinted from yellow (mix 0) to its own orange (mix 1) while it grows. */
function tinted(tex: HTMLImageElement, mix: number): CanvasImageSource {
  if (mix >= 0.999) return tex;
  if (!tintCanvas) tintCanvas = document.createElement("canvas");
  if (tintCanvas.width !== tex.naturalWidth) {
    tintCanvas.width = tex.naturalWidth;
    tintCanvas.height = tex.naturalHeight;
  }
  const g = tintCanvas.getContext("2d");
  if (!g) return tex;
  g.globalCompositeOperation = "source-over";
  g.clearRect(0, 0, tintCanvas.width, tintCanvas.height);
  g.drawImage(tex, 0, 0);
  g.globalCompositeOperation = "source-atop";
  g.fillStyle = "rgba(255,240,90," + ((1 - mix) * 0.85).toFixed(3) + ")";
  g.fillRect(0, 0, tintCanvas.width, tintCanvas.height);
  return tintCanvas;
}

/** The glyph ring, texture or plain strokes, at the context's current transform and alpha. */
function glyphLayer(ctx: CanvasRenderingContext2D, R: number, p: Params): void {
  const tex = glyph;
  if (tex && tex.naturalWidth) {
    const half = TEX_HALF * R * (p.glyphR / 0.86);
    ctx.drawImage(tinted(tex, p.glyphMix), -half, -half, half * 2, half * 2);
  } else {
    plainGlyphs(ctx, R, p);
  }
}

/** Where the progress arc runs: on the glyph ring's thin outer edge (1.0 R). */
export const ARC_R = 1.0;
/** The charged glyphs fade in over this many radians behind the arc's head, in a few steps. */
const ARC_FEATHER = 0.22;
const FEATHER_STEPS = 3;

/**
 * The progress arc (not from the anime: Raphael's own way of showing how far a dictation has
 * come). From twelve o'clock clockwise over `arc.fraction` of the circle, the glyphs light up as
 * if charged and a thin line of light runs along the ring's outer edge, brightest at its head.
 * It stays where it is while the glyphs turn underneath, and fades with them when the emblem
 * vanishes.
 */
function drawArc(ctx: CanvasRenderingContext2D, R: number, p: Params, rot: number, arc: ArcSample): void {
  const alpha = clamp(arc.alpha) * clamp(p.glyphA);
  const f = clamp(arc.fraction);
  if (alpha < 0.003) return;
  const s = p.glyphR / 0.86;
  const r = R * ARC_R * s;
  const a0 = -Math.PI / 2;
  const a1 = a0 + TAU * f;
  const glow = 1 + 0.8 * clamp(arc.flash);
  ctx.save();
  ctx.globalCompositeOperation = "lighter";

  // The track: the whole edge, barely there, so that the part still to fill can be read.
  ctx.globalAlpha = alpha;
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(255,214,130,0.16)";
  ctx.lineWidth = Math.max(1, R * 0.012);
  ctx.beginPath();
  ctx.arc(0, 0, r, 0, TAU);
  ctx.stroke();

  if (f > 0.002) {
    // The charged glyphs: the ring drawn once more, added on top, inside the filled wedge. The
    // last few degrees fade in steps, so the light seems to seep in rather than be cut off.
    const closed = f >= 0.999;
    const feather = closed ? 0 : Math.min(ARC_FEATHER, TAU * f);
    const slices: Array<[number, number, number]> = [[a0, a1 - feather, 1]];
    for (let k = 0; k < FEATHER_STEPS && feather > 0; k++) {
      const from = a1 - feather + (feather * k) / FEATHER_STEPS;
      slices.push([from, from + feather / FEATHER_STEPS, 1 - (k + 1) / (FEATHER_STEPS + 1)]);
    }
    for (const [from, to, share] of slices) {
      if (to - from < 1e-4) continue;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, R * 1.1 * s, from, to);
      ctx.closePath();
      ctx.clip();
      ctx.rotate(rot);
      ctx.globalAlpha = alpha * 0.6 * glow * share;
      glyphLayer(ctx, R, p);
      ctx.restore();
    }

    // The line of light along the edge.
    ctx.globalAlpha = alpha;
    ctx.shadowColor = "rgba(255,210,110,0.95)";
    ctx.shadowBlur = R * 0.06 * glow;
    ctx.strokeStyle = "rgba(255,238,184," + clamp(0.95 * glow).toFixed(3) + ")";
    ctx.lineWidth = Math.max(1.4, R * 0.022);
    ctx.beginPath();
    ctx.arc(0, 0, r, a0, a1);
    ctx.stroke();
    ctx.shadowBlur = 0;

    // Its head, where the light is coming in (none once the ring is closed).
    if (!closed) {
      const hx = Math.cos(a1) * r;
      const hy = Math.sin(a1) * r;
      const hr = Math.max(3, R * 0.075);
      const g = ctx.createRadialGradient(hx, hy, 0, hx, hy, hr);
      g.addColorStop(0, "rgba(255,255,245,1)");
      g.addColorStop(0.25, "rgba(255,240,190,0.8)");
      g.addColorStop(1, "rgba(255,200,90,0)");
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(hx, hy, hr, 0, TAU);
      ctx.fill();
    }
  }
  ctx.restore();
}

/** The glyph ring drawn with plain strokes, for when there is no texture. */
function plainGlyphs(ctx: CanvasRenderingContext2D, R: number, p: Params): void {
  const s = p.glyphR / 0.86;
  const mix = p.glyphMix;
  ctx.lineWidth = Math.max(0.6, R * 0.012);
  ctx.strokeStyle = "rgba(38,16,2,0.6)";
  for (const r of [0.73, 1.0]) {
    ctx.beginPath();
    ctx.arc(0, 0, R * r * s, 0, TAU);
    ctx.stroke();
  }
  ctx.lineWidth = R * 0.12 * s;
  ctx.setLineDash([R * 0.05 * s, R * 0.025 * s, R * 0.11 * s, R * 0.03 * s]);
  ctx.strokeStyle = "rgb(255," + Math.round(176 + 64 * (1 - mix)) + "," + Math.round(38 + 52 * (1 - mix)) + ")";
  ctx.beginPath();
  ctx.arc(0, 0, R * 0.855 * s, 0, TAU);
  ctx.stroke();
  ctx.setLineDash([]);
}

function dot(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, style: string): void {
  ctx.fillStyle = style;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, TAU);
  ctx.fill();
}

/** What the renderer needs besides the look of the frame. */
export interface DrawOptions {
  /** The effect clock, quantized to 24 fps (seeds the wobble of the ring and the rays). */
  tq: number;
  /** Angle of the glyph ring, radians. */
  rot: number;
  /** Brightness of the glyph ring (0.8 while listening). */
  dim: number;
  /** How lively the rays are (less while listening and when steady). */
  calm: number;
  /** Loudness, 0 to 1. */
  lvl: number;
  /** The progress arc after the dictation key is released, if there is one. */
  arc?: ArcSample | null;
}

/** Draws one frame, centered on the context's origin, `R` pixels in outer radius. */
export function drawEmblem(
  ctx: CanvasRenderingContext2D,
  R: number,
  p: Params | null,
  o: DrawOptions,
): void {
  if (!p) return;
  const base = ctx.globalAlpha;

  if (p.glyphA > 0.003) {
    ctx.save();
    ctx.rotate(o.rot);
    ctx.globalAlpha = base * p.glyphA * o.dim;
    if (p.glyphBlur > 0) ctx.filter = "blur(" + (p.glyphBlur * R).toFixed(2) + "px)";
    const tex = glyph;
    if (tex && tex.naturalWidth) {
      const half = TEX_HALF * R * (p.glyphR / 0.86);
      const img = tinted(tex, p.glyphMix);
      ctx.drawImage(img, -half, -half, half * 2, half * 2);
      ctx.filter = "none";
      if (p.glyphGlow > 0.01) {
        ctx.globalCompositeOperation = "lighter";
        ctx.globalAlpha = base * p.glyphGlow * 0.9;
        ctx.filter = "blur(" + (1 + R * 0.025 * (p.glyphR / 0.86)).toFixed(2) + "px)";
        ctx.drawImage(img, -half, -half, half * 2, half * 2);
      }
    } else {
      plainGlyphs(ctx, R, p);
    }
    ctx.restore();
  }

  if (o.arc) drawArc(ctx, R, p, o.rot, o.arc);

  if (p.haloA > 0.01) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.strokeStyle = "rgba(255,225,90," + (p.haloA * 0.9).toFixed(3) + ")";
    ctx.lineWidth = Math.max(1.5, R * 0.03);
    ctx.shadowColor = "rgba(255,220,80,0.9)";
    ctx.shadowBlur = R * 0.04;
    ctx.beginPath();
    ctx.arc(0, 0, R * 0.12, 0, TAU);
    ctx.stroke();
    ctx.restore();
  }

  // The white ring: an irregular 15-gon with rounded corners, redrawn every tick.
  if (p.ringA > 0.01 && p.ringR > 0.005) {
    const rnd = mulberry32(1000 + Math.round(o.tq * 24));
    const N = 15;
    const jit = 0.025;
    const r0 = R * p.ringR;
    const rot0 = (rnd() * TAU) / N;
    ctx.save();
    ctx.beginPath();
    for (let i = 0; i < N; i++) {
      const a = rot0 + (i / N) * TAU;
      const rr = r0 * (1 + (rnd() - 0.5) * 2 * jit);
      if (i) ctx.lineTo(Math.cos(a) * rr, Math.sin(a) * rr);
      else ctx.moveTo(Math.cos(a) * rr, Math.sin(a) * rr);
    }
    ctx.closePath();
    ctx.lineJoin = "round";
    const w = Math.max(1, R * p.ringW);
    ctx.lineWidth = w + 1.5;
    ctx.strokeStyle = "rgba(30,14,0,0.2)";
    ctx.stroke();
    ctx.shadowColor = "rgba(255,255,255,0.75)";
    ctx.shadowBlur = R * 0.025;
    ctx.lineWidth = w;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    ctx.restore();
  }

  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  if (p.rays > 0.01) {
    const rnd = mulberry32(5000 + Math.round(o.tq * 24));
    const n = Math.round(18 + 14 * o.lvl * o.calm);
    for (let i = 0; i < n; i++) {
      const a = rnd() * TAU;
      const len = R * p.raysScale * Math.min(0.95, 0.22 + rnd() * (0.33 + 0.3 * o.lvl * o.calm));
      const al = (0.25 + 0.5 * rnd()) * p.rays * (0.5 + 0.5 * o.calm);
      ctx.strokeStyle = "rgba(255,240,185," + clamp(al).toFixed(3) + ")";
      ctx.lineWidth = Math.max(0.8, R * 0.007 * (0.6 + rnd()));
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * R * 0.02, Math.sin(a) * R * 0.02);
      ctx.lineTo(Math.cos(a) * len, Math.sin(a) * len);
      ctx.stroke();
    }
  }
  if (p.coreA > 0.01) {
    const a = clamp(p.coreA);
    const gr = R * p.coreR * 2.2 * Math.max(1, p.coreA);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, gr);
    g.addColorStop(0, "rgba(255,255,255," + a.toFixed(3) + ")");
    g.addColorStop(0.22, "rgba(255,244,190," + (0.95 * a).toFixed(3) + ")");
    g.addColorStop(0.55, "rgba(255,214,100," + (0.35 * a).toFixed(3) + ")");
    g.addColorStop(1, "rgba(255,200,80,0)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(0, 0, gr, 0, TAU);
    ctx.fill();
  }
  if (p.sparkT >= 0.2 && p.sparkT <= 0.6) {
    const rs = mulberry32(77);
    for (let k = 0; k < 6; k++) {
      const a = rs() * TAU;
      const rr = (0.62 + rs() * 0.22) * R;
      const life = clamp((p.sparkT - 0.2 - k * 0.03) / 0.2);
      if (life > 0 && life < 1) {
        dot(ctx, Math.cos(a) * rr, Math.sin(a) * rr, Math.max(0.8, R * 0.012), "rgba(255,255,240," + (1 - life).toFixed(3) + ")");
      }
    }
  }
  ctx.restore();
}

// ---------- loudness for notices ----------

// Raphael's notices have no voice yet (that is phase 3), so while one is shown the white ring
// moves with a made-up loudness, as if she were speaking it.
interface Syllable {
  t0: number;
  dur: number;
  amp: number;
}
interface SyllableStyle {
  dmin: number;
  dmax: number;
  amin: number;
  amax: number;
  gmin: number;
  gmax: number;
  pause: number;
  pmin: number;
  pmax: number;
}

function makeSyllables(seed: number, total: number, c: SyllableStyle): Syllable[] {
  const r = mulberry32(seed);
  const out: Syllable[] = [];
  let t = 0;
  while (t < total) {
    const dur = c.dmin + r() * (c.dmax - c.dmin);
    out.push({ t0: t, dur, amp: c.amin + r() * (c.amax - c.amin) });
    t += dur + c.gmin + r() * (c.gmax - c.gmin);
    if (r() < c.pause) t += c.pmin + r() * (c.pmax - c.pmin);
  }
  return out;
}

function envAt(syl: Syllable[], t: number): number {
  let v = 0;
  for (const s of syl) {
    if (s.t0 > t) break;
    if (t <= s.t0 + s.dur) v = Math.max(v, s.amp * Math.pow(Math.sin((Math.PI * (t - s.t0)) / s.dur), 0.7));
  }
  return v;
}

const SPEAK = makeSyllables(21, 40, { dmin: 0.1, dmax: 0.16, amin: 0.5, amax: 1.0, gmin: 0.015, gmax: 0.04, pause: 0.1, pmin: 0.25, pmax: 0.4 });
/** The made-up loudness repeats every 36 s; it is built for 40 s so that it never runs dry. */
const SPEAK_CYCLE = 36;

// ---------- the emblem on a canvas ----------

/** The canvas is this much larger than the emblem so that the rays have room. */
export const CANVAS_RATIO = 1.3;

/** The square canvas for an emblem of diameter `px` (CSS pixels). */
export const canvasSize = (px: number): number => Math.round(px * CANVAS_RATIO);

export interface EmblemOptions {
  /** Loudness of the white ring when nothing makes noise (0.05 is about 1 px). */
  base?: number;
  /** Start on screen (steady) instead of hidden. */
  visible?: boolean;
  /** Starting angle of the glyph ring, radians. */
  rot0?: number;
}

const devicePixelRatioNow = (): number =>
  typeof window === "undefined" ? 1 : Math.max(1, Math.min(3, window.devicePixelRatio || 1));

const prefersReducedMotion = (): boolean =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Raphael's emblem on a canvas. It animates itself: all emblems share one animation loop that
 * runs only while something is on screen, so a hidden emblem costs nothing.
 *
 * - `appear()` plays the 0.833 s appear animation and then shows the mode set with `set`.
 * - `vanish()` plays the 0.467 s vanish animation and then the emblem is hidden.
 * - `level` (0 to 1) sets the white ring's width while listening or speaking.
 * - `speak(seconds)` moves the ring with a made-up loudness for that long.
 * - `progress(report)` fills the glyph ring with an arc of light (see progress.ts), on the same
 *   24 fps clock; `clearProgress()` fades it out.
 *
 * With reduced motion the two animations are skipped and the glyph ring stands still.
 */
export class Emblem {
  readonly canvas: HTMLCanvasElement;
  /** Set when the next frame must be drawn even if the 24 fps clock did not tick. */
  dirty = true;

  private _phase: Phase;
  private mode: Mode = "steady";
  private pt = 0;
  private clock = 0;
  private lastTick = -1;
  private _rot: number;
  private shownRot: number;
  private _lvl: number;
  private readonly base: number;
  private external = 0;
  private talkUntil = 0;
  private talkOff = 0;
  private diameter = 0;
  private side = 0;
  private dpr = 1;
  private reduce = false;
  private ctx: CanvasRenderingContext2D | null = null;
  private steps = 0;
  private readonly arc = new ProgressArc();
  private shownArc: ArcSample | null = null;

  constructor(canvas: HTMLCanvasElement, sizePx: number, opts: EmblemOptions = {}) {
    this.canvas = canvas;
    this._phase = opts.visible ? "steady" : "hidden";
    this._rot = opts.rot0 ?? 0.4;
    this.shownRot = this._rot;
    this.base = opts.base ?? 0.05;
    this._lvl = this.base;
    this.reduce = prefersReducedMotion();
    this.size(sizePx);
    emblems.add(this);
    wake();
  }

  /** What is on screen now. */
  get phase(): Phase {
    return this._phase;
  }

  /** The angle of the glyph ring, radians. */
  get rot(): number {
    return this._rot;
  }

  /** How many times the animation loop has stepped this emblem (the page watches for a stall). */
  get frames(): number {
    return this.steps;
  }

  /** The progress arc as it is drawn now (null for none). */
  get progressShown(): ArcSample | null {
    return this.shownArc;
  }

  /** The loudness the white ring shows now, 0 to 1. */
  get lvl(): number {
    return this._lvl;
  }

  /** Seconds of the appear animation still to play (0 when it is not playing). */
  get appearLeft(): number {
    return this._phase === "appear" ? Math.max(0, AP.dur - this.pt) : 0;
  }

  get level(): number {
    return this.external;
  }

  set level(value: number) {
    this.external = Number.isFinite(value) ? clamp(value) : 0;
  }

  /** Resizes the emblem to a diameter of `px` CSS pixels. */
  size(px: number): void {
    this.diameter = px;
    this.dpr = devicePixelRatioNow();
    this.side = canvasSize(px);
    this.canvas.style.width = this.side + "px";
    this.canvas.style.height = this.side + "px";
    this.canvas.width = Math.round(this.side * this.dpr);
    this.canvas.height = Math.round(this.side * this.dpr);
    this.dirty = true;
    wake();
  }

  appear(): void {
    // The screen's scale may have changed since the last time.
    this.reduce = prefersReducedMotion();
    this.arc.reset();
    this.shownArc = null;
    if (devicePixelRatioNow() !== this.dpr) this.size(this.diameter);
    this._phase = this.reduce ? this.mode : "appear";
    this.pt = 0;
    this.dirty = true;
    wake();
  }

  vanish(): void {
    // Already going: starting over would make the emblem grow back and vanish twice.
    if (this._phase === "hidden" || this._phase === "vanish") return;
    this.reduce = prefersReducedMotion();
    this._phase = this.reduce ? "hidden" : "vanish";
    this.pt = 0;
    this.dirty = true;
    wake();
  }

  /** Sets the mode; while the emblem is appearing, vanishing or hidden it takes effect later. */
  set(mode: Mode): void {
    this.mode = mode;
    if (this._phase !== "appear" && this._phase !== "vanish" && this._phase !== "hidden") {
      this._phase = mode;
      this.pt = 0;
      this.dirty = true;
      wake();
    }
  }

  /**
   * How far the dictation has come since the key was released: the arc of light on the glyph
   * ring. Ignored while the emblem is hidden or vanishing.
   */
  progress(report: ProgressReport): void {
    if (this._phase === "hidden" || this._phase === "vanish") return;
    this.arc.report(report, this.reduce);
    this.dirty = true;
    wake();
  }

  /** The arc is no longer needed (a notice takes the emblem): it fades out. */
  clearProgress(): void {
    this.arc.clear(this.reduce);
    this.dirty = true;
    wake();
  }

  /** The white ring moves with a made-up loudness for `seconds`. */
  speak(seconds: number): void {
    this.talkUntil = this.clock + seconds;
    this.talkOff = Math.random() * 6;
    wake();
  }

  /**
   * Advances the animation by `dt` seconds. Returns true when the 24 fps clock ticked, that is,
   * when the picture changed and should be drawn.
   */
  step(dt: number): boolean {
    this.steps++;
    this.clock += dt;
    this.pt += dt;
    if (this._phase === "appear" && this.pt >= AP.dur) {
      this._phase = this.mode;
      this.pt = 0;
    } else if (this._phase === "vanish" && this.pt >= VA.dur) {
      this._phase = "hidden";
      this.pt = 0;
      this.dirty = true;
    }
    if (this._phase === "hidden") this.arc.reset();
    else this.arc.advance(dt, this.reduce);
    if (this._phase !== "hidden" && !this.reduce) {
      this._rot += dt * RATE * (this._phase === "thinking" ? 6 : 1);
    }
    const tk = Math.floor(this.clock * 24);
    if (tk === this.lastTick) return false;
    this.lastTick = tk;
    this.shownRot = this._rot;
    this.shownArc = this.arc.sample(this.reduce);

    let raw = this.base;
    // While the emblem is appearing, what counts is the mode it is appearing into.
    const shown = this._phase === "appear" ? this.mode : this._phase;
    if (shown === "listening") {
      // Only the microphone moves the ring while listening.
      raw = Math.max(raw, this.external);
    } else {
      if (shown === "speaking") raw = Math.max(raw, this.external);
      if (this.clock < this.talkUntil) raw = Math.max(raw, envAt(SPEAK, (this.clock + this.talkOff) % SPEAK_CYCLE));
    }
    this._lvl = clamp(raw);
    return true;
  }

  /** Draws the current frame. */
  draw(): void {
    this.ctx ??= this.canvas.getContext("2d");
    const ctx = this.ctx;
    if (!ctx) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    const k = this.dpr;
    ctx.setTransform(k, 0, 0, k, (this.side * k) / 2, (this.side * k) / 2);
    const ph = this._phase;
    drawEmblem(ctx, this.diameter / 2, params(ph, tick(this.pt), this._lvl), {
      tq: tick(this.clock),
      rot: this.shownRot,
      dim: ph === "listening" ? 0.8 : 1,
      calm: ph === "listening" ? 0.6 : ph === "steady" ? 0.7 : 1,
      lvl: this._lvl,
      arc: this.shownArc,
    });
  }

  /** Stops animating this emblem for good. */
  dispose(): void {
    emblems.delete(this);
  }
}

// ---------- the shared animation loop ----------

const emblems = new Set<Emblem>();
let frameId = 0;
let lastFrame = 0;

/** Starts the loop if it is not running. It stops by itself when every emblem is hidden. */
function wake(): void {
  if (frameId !== 0 || typeof requestAnimationFrame !== "function") return;
  lastFrame = performance.now();
  frameId = requestAnimationFrame(frame);
}

function frame(ms: number): void {
  frameId = 0;
  const dt = Math.min(0.05, Math.max(0, (ms - lastFrame) / 1000));
  lastFrame = ms;
  let busy = false;
  for (const e of emblems) {
    if (!e.canvas.isConnected) {
      emblems.delete(e);
      continue;
    }
    // A canvas that is not on screen (display: none) waits without animating.
    if (e.canvas.getClientRects().length === 0) {
      if (e.phase !== "hidden") busy = true;
      continue;
    }
    if (e.step(dt) || e.dirty) {
      e.draw();
      e.dirty = false;
    }
    if (e.phase !== "hidden" || e.dirty) busy = true;
  }
  if (busy) frameId = requestAnimationFrame(frame);
}

onTextureLoaded = () => {
  for (const e of emblems) e.dirty = true;
  wake();
};
