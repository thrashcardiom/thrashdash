// Pure geometry for dragging and resizing widgets (no DOM, unit-tested under Node).
// All numbers are in layout ("design") pixels, never screen pixels: the caller divides pointer
// movement by the canvas scale first.

export const MIN_SIZE = 16;
export const MAX_COORD = 8000;

export const snap = (value, grid) => (grid > 0 ? Math.round(value / grid) * grid : Math.round(value));

/**
 * Move a group of rectangles by (dx, dy). The group moves as one rigid body: the first rectangle is
 * snapped to the grid and the others keep their offsets from it, so alignment between widgets survives.
 * @param {Map<string, {x:number,y:number,w:number,h:number}>} start rectangles when the drag began
 * @returns {Map<string, {x:number,y:number,w:number,h:number}>}
 */
export function dragRects(start, dx, dy, grid) {
  const out = new Map();
  const first = start.values().next().value;
  if (!first) return out;
  const sx = snap(first.x + dx, grid) - first.x;
  const sy = snap(first.y + dy, grid) - first.y;
  for (const [id, r] of start) out.set(id, { ...r, x: r.x + sx, y: r.y + sy });
  return out;
}

/**
 * Resize one rectangle by dragging a handle ("n","s","e","w","ne","nw","se","sw").
 * The edges being dragged snap to the grid; the opposite edges never move, even when the minimum
 * size stops the drag. With `keepAspect`, corner handles preserve the starting aspect ratio.
 */
export function resizeRect(start, handle, dx, dy, { grid = 0, min = MIN_SIZE, keepAspect = false } = {}) {
  let left = start.x;
  let top = start.y;
  let right = start.x + start.w;
  let bottom = start.y + start.h;

  if (keepAspect && handle.length === 2) {
    // Corner drag that preserves the starting aspect ratio: follow whichever axis the pointer has
    // stretched further, derive the other from the ratio, and respect the minimum on both axes.
    const aspect = start.w / start.h;
    const growX = handle.includes("e") ? 1 : -1;
    const growY = handle.includes("s") ? 1 : -1;
    const scale = Math.max((start.w + growX * dx) / start.w, (start.h + growY * dy) / start.h);
    let w = start.w * scale;
    let h = start.h * scale;
    if (w < min) [w, h] = [min, min / aspect];
    if (h < min) [w, h] = [min * aspect, min];
    w = Math.round(w);
    h = Math.round(h);
    return { x: handle.includes("e") ? start.x : start.x + start.w - w, y: handle.includes("s") ? start.y : start.y + start.h - h, w, h };
  }

  if (handle.includes("e")) right = snap(start.x + start.w + dx, grid);
  if (handle.includes("w")) left = snap(start.x + dx, grid);
  if (handle.includes("s")) bottom = snap(start.y + start.h + dy, grid);
  if (handle.includes("n")) top = snap(start.y + dy, grid);

  // Minimum size: stop the dragged edge, keep the opposite one where it was.
  if (right - left < min) {
    if (handle.includes("w")) left = right - min;
    else right = left + min;
  }
  if (bottom - top < min) {
    if (handle.includes("n")) top = bottom - min;
    else bottom = top + min;
  }
  return { x: left, y: top, w: right - left, h: bottom - top };
}

/** Clamp a rectangle into sane bounds (used for typed values and nudging). */
export function clampRect(r, canvasWidth) {
  const w = Math.min(MAX_COORD, Math.max(MIN_SIZE, Math.round(r.w)));
  const h = Math.min(MAX_COORD, Math.max(MIN_SIZE, Math.round(r.h)));
  const x = Math.min(MAX_COORD, Math.max(-canvasWidth, Math.round(r.x)));
  const y = Math.min(MAX_COORD, Math.max(-canvasWidth, Math.round(r.y)));
  return { x, y, w, h };
}

/** Bounding box of rectangles, or null when empty. */
export function boundsOf(rects) {
  let box = null;
  for (const r of rects) {
    if (!box) box = { x: r.x, y: r.y, x2: r.x + r.w, y2: r.y + r.h };
    else {
      box.x = Math.min(box.x, r.x);
      box.y = Math.min(box.y, r.y);
      box.x2 = Math.max(box.x2, r.x + r.w);
      box.y2 = Math.max(box.y2, r.y + r.h);
    }
  }
  return box && { x: box.x, y: box.y, w: box.x2 - box.x, h: box.y2 - box.y };
}

// ---- marquee selection, align and distribute ----------------------------------------------------------------------------------

/** The rectangle between two corner points (in any order): {x, y, w, h}. */
export function rectBetween(a, b) {
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(a.x - b.x), h: Math.abs(a.y - b.y) };
}

/** Whether two rectangles overlap (touching edges do not count). */
export const rectsOverlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/** The ids of the widgets a marquee touches. `widgets` are {id, x, y, w, h}; any overlap counts, as in most editors. */
export const widgetsInRect = (widgets, rect) => widgets.filter((w) => rectsOverlap(w, rect)).map((w) => w.id);

export const ALIGN_MODES = ["left", "centre", "right", "top", "middle", "bottom"];

/**
 * Line rectangles up with each other, against the box around all of them: "left", "centre" (the middle of each, horizontally) and
 * "right" move them sideways; "top", "middle" and "bottom" move them up and down. Sizes never change, and nothing leaves the box.
 * @param {Map<string, {x:number,y:number,w:number,h:number}>} rects
 * @returns {Map<string, {x:number,y:number,w:number,h:number}>} every rectangle given, moved where needed
 */
export function alignRects(rects, mode) {
  const box = boundsOf(rects.values());
  const out = new Map();
  if (!box || !ALIGN_MODES.includes(mode)) {
    for (const [id, r] of rects) out.set(id, { ...r });
    return out;
  }
  for (const [id, r] of rects) {
    const moved = { ...r };
    if (mode === "left") moved.x = box.x;
    else if (mode === "centre") moved.x = Math.round(box.x + (box.w - r.w) / 2);
    else if (mode === "right") moved.x = box.x + box.w - r.w;
    else if (mode === "top") moved.y = box.y;
    else if (mode === "middle") moved.y = Math.round(box.y + (box.h - r.h) / 2);
    else moved.y = box.y + box.h - r.h;
    out.set(id, moved);
  }
  return out;
}

/**
 * Space rectangles evenly along one axis ("h" across, "v" down): the first and last stay where they are, and the gaps between each
 * neighbouring pair are made equal (to the whole pixel: the gaps differ by at most one). Needs three or more; with fewer nothing moves.
 */
export function distributeRects(rects, axis) {
  const out = new Map();
  for (const [id, r] of rects) out.set(id, { ...r });
  if (rects.size < 3) return out;
  const horizontal = axis === "h";
  const pos = (r) => (horizontal ? r.x : r.y);
  const size = (r) => (horizontal ? r.w : r.h);
  const ordered = [...rects].sort((a, b) => pos(a[1]) - pos(b[1]) || String(a[0]).localeCompare(String(b[0])));
  const first = ordered[0][1];
  const last = ordered[ordered.length - 1][1];
  const span = pos(last) + size(last) - pos(first);
  const gap = (span - ordered.reduce((sum, [, r]) => sum + size(r), 0)) / (ordered.length - 1);
  let cursor = pos(first);
  for (let i = 0; i < ordered.length; i++) {
    const [id, r] = ordered[i];
    const at = i === ordered.length - 1 ? pos(last) : Math.round(cursor);
    out.set(id, horizontal ? { ...r, x: at } : { ...r, y: at });
    cursor += size(r) + gap;
  }
  return out;
}
