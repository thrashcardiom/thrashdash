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
