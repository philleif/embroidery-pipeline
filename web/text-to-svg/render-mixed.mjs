// Headless dual-font subscript renderer.
//
// Renders text where Unicode-subscript letters (ₐ ₑ ᵢ ₙ …) drop small in one
// font and everything else stays full-size in another — then wraps each glyph
// in the same inkstitch:* fill attrs the browser tool emits (see inkstitch.js),
// tuned denser + shorter than the hat-twill preset because the subscript
// letters are small.
//
// Run: node render-mixed.mjs   →   ../../designs/yearn-v2.svg

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import opentype from "./lib/opentype.min.js";

const HERE = dirname(fileURLToPath(import.meta.url));

// ---------- design parameters ----------
const LINES       = ["Yₑₐᵣₙ ᵢₙ ₕₑₗₗ,", "bᵢₜcₕ."];
const SERIF_FILE  = "PlayfairDisplay-Regular.ttf"; // full-size letters
const SUB_FILE    = "Bevan-Regular.ttf";           // subscript letters
const FONT_SIZE   = 68;    // user units; serif cap height ≈ 0.71 of this
                           // (68 keeps the design ≈95mm wide — inside the PE900 field)
const SUB_SCALE   = 0.62;  // subscript glyphs at 62%
const SUB_DROP    = 0.18;  // subscript baseline dropped 0.18em
const LINE_HEIGHT = 1.30;  // × FONT_SIZE
const SPACE_SCALE = 1.0;   // word space, × serif space at FONT_SIZE
const FILL_HEX    = "#FFF859"; // Madeira 1147 — OHHI yellow
const PAD         = 12;
const OUT         = join(HERE, "../../designs/yearn-v2.svg");

// Ink/Stitch fill tuning. The hat-twill preset is 0.40 row / 3.00 stitch;
// this is denser + shorter so the small letters read solid. Subscript glyphs
// are smaller still, so they get the tightest stitch length + lightest underlay.
const TUNE_FULL = { row: 0.30, maxLen: 2.0, inset: 0.6, expand: 0.2 };
const TUNE_SUB  = { row: 0.30, maxLen: 1.5, inset: 0.4, expand: 0.15 };

// Unicode subscript codepoint → base ASCII letter.
const SUBSCRIPT = {
  "ₐ": "a", "ₑ": "e", "ₕ": "h", "ᵢ": "i", "ⱼ": "j",
  "ₖ": "k", "ₗ": "l", "ₘ": "m", "ₙ": "n", "ₒ": "o",
  "ₚ": "p", "ᵣ": "r", "ₛ": "s", "ₜ": "t", "ᵤ": "u",
  "ᵥ": "v", "ₓ": "x",
};

// ---------- fonts ----------
function loadFont(file) {
  const buf = readFileSync(join(HERE, "fonts", file));
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  return opentype.parse(ab);
}
const serif = loadFont(SERIF_FILE);
const subFont = loadFont(SUB_FILE);

// ---------- inkstitch attrs (mirrors inkstitch.js inkstitchAttrs("fill")) ----------
const f2 = (n) => Number(n).toFixed(2);
function fillAttrs(t) {
  return [
    `inkstitch:fill_method="auto_fill"`,
    `inkstitch:row_spacing_mm="${f2(t.row)}"`,
    `inkstitch:max_stitch_length_mm="${f2(t.maxLen)}"`,
    `inkstitch:fill_underlay="true"`,
    `inkstitch:fill_underlay_inset_mm="${f2(t.inset)}"`,
    `inkstitch:expand_mm="${f2(t.expand)}"`,
  ].join(" ");
}

// Punctuation has no subscript codepoint, so it inherits the font, size and
// baseline of the letter it follows — keeps a trailing "." or "," visually
// attached to a small subscript word instead of floating full-size beside it.
const PUNCT = new Set([",", ".", ";", ":", "!", "?", "'", "’", "…"]);

// ---------- per-character metrics ----------
// Resolve every char to a treatment {font, size, base, sub} (or {space:true}).
function resolveLine(chars) {
  const items = [];
  let prevLetter = null;
  for (const ch of chars) {
    if (ch === " ") { items.push({ space: true }); continue; }
    if (PUNCT.has(ch) && prevLetter) {
      items.push({ ...prevLetter, base: ch });
      continue;
    }
    const sub = ch in SUBSCRIPT;
    const t = {
      sub,
      base: sub ? SUBSCRIPT[ch] : ch,
      font: sub ? subFont : serif,
      size: sub ? FONT_SIZE * SUB_SCALE : FONT_SIZE,
    };
    prevLetter = t;
    items.push(t);
  }
  return items;
}
function advanceOf(it) {
  if (it.space) {
    const g = serif.charToGlyph(" ");
    return g.advanceWidth * ((FONT_SIZE * SPACE_SCALE) / serif.unitsPerEm);
  }
  const g = it.font.charToGlyph(it.base);
  return g.advanceWidth * (it.size / it.font.unitsPerEm);
}

// ---------- layout: one line, centered on x = 0 ----------
function layoutLine(line, baseY) {
  const items = resolveLine([...line]);
  const width = items.reduce((w, it) => w + advanceOf(it), 0);
  let x = -width / 2;
  const glyphs = [];
  for (const it of items) {
    const adv = advanceOf(it);
    if (!it.space) {
      const y = it.sub ? baseY + FONT_SIZE * SUB_DROP : baseY;
      const path = it.font.charToGlyph(it.base).getPath(x, y, it.size);
      const dStr = path.toPathData(2);
      if (dStr) glyphs.push({ d: dStr, bbox: path.getBoundingBox(), sub: it.sub });
    }
    x += adv;
  }
  return { glyphs, width };
}

// ---------- build ----------
const glyphs = [];
LINES.forEach((line, i) => {
  const { glyphs: g, width } = layoutLine(line, i * FONT_SIZE * LINE_HEIGHT);
  glyphs.push(...g);
  console.log(`line ${i + 1}: ${g.length} glyphs, width ${width.toFixed(1)}u`);
});

let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
for (const g of glyphs) {
  x1 = Math.min(x1, g.bbox.x1); y1 = Math.min(y1, g.bbox.y1);
  x2 = Math.max(x2, g.bbox.x2); y2 = Math.max(y2, g.bbox.y2);
}
const vb = [
  Math.floor(x1 - PAD), Math.floor(y1 - PAD),
  Math.ceil(x2 - x1 + 2 * PAD), Math.ceil(y2 - y1 + 2 * PAD),
];

const NS = "http://inkstitch.org/namespace";
const out = [`<?xml version="1.0" encoding="UTF-8"?>`];
out.push(
  `<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkstitch="${NS}" ` +
  `viewBox="${vb.join(" ")}" width="${vb[2]}" height="${vb[3]}" fill="none">`,
);
out.push(`<metadata><inkstitch:inkstitch_svg_version>3</inkstitch:inkstitch_svg_version></metadata>`);
for (const g of glyphs) {
  out.push(`<path d="${g.d}" fill="${FILL_HEX}" ${fillAttrs(g.sub ? TUNE_SUB : TUNE_FULL)} />`);
}
out.push(`</svg>`);

writeFileSync(OUT, out.join("\n") + "\n");
console.log(`wrote ${OUT}  viewBox ${vb.join(" ")}  (${glyphs.length} glyph paths)`);
