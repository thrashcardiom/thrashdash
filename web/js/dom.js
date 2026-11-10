// Tiny DOM helpers. Elements are built with createElement/textContent only (never innerHTML), so
// no channel value or layout string can ever be interpreted as markup.

const SVG_NS = "http://www.w3.org/2000/svg";

export function h(tag, className, parent, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  if (parent) parent.appendChild(el);
  return el;
}

export function s(tag, attrs, parent, text) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
  if (text !== undefined) el.textContent = text;
  if (parent) parent.appendChild(el);
  return el;
}

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
