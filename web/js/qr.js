// A small QR code encoder (byte mode, error-correction level M, versions 1-10: up to 213 bytes, plenty for a URL).
// Written for ThrashDash so the page can show a scannable address without any third-party library. Follows ISO/IEC 18004.
// Verified against an independent implementation in relay/tests/test_qr.py.

const GF_EXP = new Array(512);
const GF_LOG = new Array(256);
(() => {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d; // the Reed-Solomon field polynomial x^8 + x^4 + x^3 + x^2 + 1
  }
  for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];
})();
const gfMul = (a, b) => (a === 0 || b === 0 ? 0 : GF_EXP[GF_LOG[a] + GF_LOG[b]]);

/** Generator polynomial of the given degree (coefficients, highest power first, leading 1 omitted). */
function rsGenerator(degree) {
  let poly = [1];
  for (let i = 0; i < degree; i++) {
    const next = new Array(poly.length + 1).fill(0);
    poly.forEach((c, j) => {
      next[j] ^= c;
      next[j + 1] ^= gfMul(c, GF_EXP[i]);
    });
    poly = next;
  }
  return poly.slice(1);
}

/** Reed-Solomon remainder: the `degree` error-correction codewords for `data`. */
export function rsRemainder(data, degree) {
  const gen = rsGenerator(degree);
  const rem = new Array(degree).fill(0);
  for (const byte of data) {
    const factor = byte ^ rem.shift();
    rem.push(0);
    gen.forEach((g, i) => {
      rem[i] ^= gfMul(g, factor);
    });
  }
  return rem;
}

// Error-correction level M. version -> [EC codewords per block, [[block count, data codewords per block], ...]]
const BLOCKS_M = {
  1: [10, [[1, 16]]], 2: [16, [[1, 28]]], 3: [26, [[1, 44]]], 4: [18, [[2, 32]]], 5: [24, [[2, 43]]],
  6: [16, [[4, 27]]], 7: [18, [[4, 31]]], 8: [22, [[2, 38], [2, 39]]], 9: [22, [[3, 36], [2, 37]]], 10: [26, [[4, 43], [1, 44]]],
};
const ALIGN = { 1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50] };
const MAX_VERSION = 10;

const dataCodewords = (v) => BLOCKS_M[v][1].reduce((n, [count, per]) => n + count * per, 0);
const bit = (value, i) => ((value >>> i) & 1) === 1;

/** Smallest version whose capacity holds `length` bytes, or throws. */
export function versionFor(length) {
  for (let v = 1; v <= MAX_VERSION; v++) {
    const countBits = v < 10 ? 8 : 16;
    if (4 + countBits + 8 * length <= 8 * dataCodewords(v)) return v;
  }
  throw new RangeError("text is too long for the QR code");
}

function makeCodewords(bytes, version) {
  const capacity = 8 * dataCodewords(version);
  const bits = [];
  const push = (value, count) => {
    for (let i = count - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  push(0b0100, 4); // byte mode
  push(bytes.length, version < 10 ? 8 : 16);
  for (const b of bytes) push(b, 8);
  push(0, Math.min(4, capacity - bits.length)); // terminator
  while (bits.length % 8) bits.push(0);
  for (let pad = 0xec; bits.length < capacity; pad ^= 0xec ^ 0x11) push(pad, 8);
  const data = [];
  for (let i = 0; i < bits.length; i += 8) data.push(parseInt(bits.slice(i, i + 8).join(""), 2));

  // Split into blocks, add each block's error-correction codewords, then interleave.
  const [ecPer, groups] = BLOCKS_M[version];
  const blocks = [];
  let at = 0;
  for (const [count, per] of groups) {
    for (let i = 0; i < count; i++) {
      const chunk = data.slice(at, at + per);
      at += per;
      blocks.push({ data: chunk, ec: rsRemainder(chunk, ecPer) });
    }
  }
  const out = [];
  const longest = Math.max(...blocks.map((b) => b.data.length));
  for (let i = 0; i < longest; i++) for (const b of blocks) if (i < b.data.length) out.push(b.data[i]);
  for (let i = 0; i < ecPer; i++) for (const b of blocks) out.push(b.ec[i]);
  return out;
}

const MASKS = [
  (r, c) => (r + c) % 2 === 0,
  (r) => r % 2 === 0,
  (r, c) => c % 3 === 0,
  (r, c) => (r + c) % 3 === 0,
  (r, c) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
  (r, c) => ((r * c) % 2) + ((r * c) % 3) === 0,
  (r, c) => (((r * c) % 2) + ((r * c) % 3)) % 2 === 0,
  (r, c) => ((((r + c) % 2) + ((r * c) % 3)) % 2) === 0,
];

/** 15 format bits for level M and the given mask: 5 data bits, BCH(15,5) check, XOR mask 0x5412. */
export function formatBits(mask) {
  const data = (0b00 << 3) | mask; // level M = 00
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

/** 18 version bits (versions 7+): 6 data bits plus a BCH(18,6) check. */
export function versionBits(version) {
  let rem = version;
  for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
  return (version << 12) | rem;
}

function penalty(m) {
  const n = m.length;
  let score = 0;
  const lines = [];
  for (let i = 0; i < n; i++) {
    lines.push(m[i]);
    lines.push(m.map((row) => row[i]));
  }
  for (const line of lines) {
    let run = 1;
    for (let i = 1; i <= n; i++) {
      if (i < n && line[i] === line[i - 1]) run++;
      else {
        if (run >= 5) score += 3 + (run - 5); // rule 1: long runs
        run = 1;
      }
    }
    const s = line.map((d) => (d ? "1" : "0")).join("");
    for (const pat of ["10111010000", "00001011101"]) for (let at = s.indexOf(pat); at >= 0; at = s.indexOf(pat, at + 1)) score += 40; // rule 3: finder-like patterns
  }
  for (let r = 0; r < n - 1; r++) for (let c = 0; c < n - 1; c++) if (m[r][c] === m[r][c + 1] && m[r][c] === m[r + 1][c] && m[r][c] === m[r + 1][c + 1]) score += 3; // rule 2
  const dark = m.flat().filter(Boolean).length;
  score += 10 * Math.floor(Math.abs((dark * 100) / (n * n) - 50) / 5); // rule 4: balance of dark and light
  return score;
}

/**
 * Encode `text` (UTF-8) as a QR code.
 * @param {string} text
 * @param {{mask?: number}} [options] force a mask pattern 0-7 (used by tests); by default the best-scoring one is chosen
 * @returns {boolean[][]} square matrix, true = dark module (no quiet zone)
 */
export function encodeQr(text, { mask } = {}) {
  const bytes = [...new TextEncoder().encode(text)];
  const version = versionFor(bytes.length);
  const size = 17 + 4 * version;
  const modules = Array.from({ length: size }, () => new Array(size).fill(false));
  const fixed = Array.from({ length: size }, () => new Array(size).fill(false)); // function modules: never masked or overwritten
  const set = (x, y, dark) => {
    modules[y][x] = dark;
    fixed[y][x] = true;
  };

  // timing patterns first, then the finder patterns (with their separators) on top: the finders must win where they cross
  for (let i = 0; i < size; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const x = cx + dx;
        const y = cy + dy;
        if (x < 0 || y < 0 || x >= size || y >= size) continue;
        const dist = Math.max(Math.abs(dx), Math.abs(dy));
        set(x, y, dist !== 2 && dist !== 4);
      }
    }
  }
  // alignment patterns (not where a finder pattern already is)
  const centers = ALIGN[version];
  for (const cy of centers) {
    for (const cx of centers) {
      if ((cx === 6 && cy === 6) || (cx === 6 && cy === centers.at(-1)) || (cx === centers.at(-1) && cy === 6)) continue;
      for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }
  // reserve the format and version areas, then place data
  const drawFormat = (m) => {
    const bits = formatBits(m);
    for (let i = 0; i <= 5; i++) set(8, i, bit(bits, i));
    set(8, 7, bit(bits, 6));
    set(8, 8, bit(bits, 7));
    set(7, 8, bit(bits, 8));
    for (let i = 9; i < 15; i++) set(14 - i, 8, bit(bits, i));
    for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(bits, i));
    for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(bits, i));
    set(8, size - 8, true); // the always-dark module
  };
  drawFormat(0);
  if (version >= 7) {
    const bits = versionBits(version);
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      set(a, b, bit(bits, i));
      set(b, a, bit(bits, i));
    }
  }

  // data and error-correction bits in the zig-zag order, skipping function modules
  const codewords = makeCodewords(bytes, version);
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5; // the vertical timing pattern is skipped
    for (let v = 0; v < size; v++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? size - 1 - v : v;
        if (!fixed[y][x] && i < codewords.length * 8) {
          modules[y][x] = bit(codewords[i >>> 3], 7 - (i & 7));
          i++;
        } // any remainder bits stay light
      }
    }
  }

  const applyMask = (m) => {
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (!fixed[y][x] && MASKS[m](y, x)) modules[y][x] = !modules[y][x];
  };
  let best = mask;
  if (best === undefined) {
    let bestScore = Infinity;
    for (let m = 0; m < 8; m++) {
      applyMask(m);
      drawFormat(m);
      const score = penalty(modules);
      applyMask(m); // XOR again: undo
      if (score < bestScore) {
        bestScore = score;
        best = m;
      }
    }
  }
  applyMask(best);
  drawFormat(best);
  return modules;
}

/** Render a QR code for `text` as an SVG element (black on white, with the 4-module quiet zone scanners need). */
export function qrSvg(text, { size = 168 } = {}) {
  const modules = encodeQr(text);
  const n = modules.length;
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${n + 8} ${n + 8}`);
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("shape-rendering", "crispEdges");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `QR code for ${text}`);
  const back = document.createElementNS(NS, "rect");
  back.setAttribute("width", String(n + 8));
  back.setAttribute("height", String(n + 8));
  back.setAttribute("fill", "#fff");
  svg.appendChild(back);
  const path = document.createElementNS(NS, "path");
  let d = "";
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (modules[y][x]) d += `M${x + 4} ${y + 4}h1v1h-1z`;
  path.setAttribute("d", d);
  path.setAttribute("fill", "#000");
  svg.appendChild(path);
  return svg;
}
