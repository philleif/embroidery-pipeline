// Generic single-font wordmark -> embroidery SVG (filled glyphs, one color).
// Built for the blackletter/gothic ROCKTAVIA exploration: solid red letters on
// black, no border. Scales the whole design to a target physical width.
//
// Run: node render-wordmark.mjs <fontFile> <text> <targetWmm> <stem> [trackingEm]
//   e.g. node render-wordmark.mjs UnifrakturMaguntia-Book.ttf Rocktavia 90 rk-maguntia 0

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import opentype from "./lib/opentype.min.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const [, , FONT_FILE, TEXT = "Rocktavia", TARGET_W = "90", STEM = "wordmark", TRACK = "0"] = process.argv;
const targetW = parseFloat(TARGET_W);
const trackEm = parseFloat(TRACK);
const FILL_HEX = "#C41820";

const buf = readFileSync(join(HERE, "fonts", FONT_FILE));
const font = opentype.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
const upm = font.unitsPerEm;
const SIZE = 100; // arbitrary layout size; scaled to mm at emit

const glyphs = [];
let pen = 0;
for (const ch of [...TEXT]) {
  const g = font.charToGlyph(ch);
  const p = g.getPath(pen, 0, SIZE);
  const d = p.toPathData(2);
  const bbox = p.getBoundingBox();
  if (d) glyphs.push({ d, bbox });
  pen += g.advanceWidth * (SIZE / upm) + trackEm * SIZE;
}

const B = glyphs.reduce(
  (a, g) => ({
    x1: Math.min(a.x1, g.bbox.x1), y1: Math.min(a.y1, g.bbox.y1),
    x2: Math.max(a.x2, g.bbox.x2), y2: Math.max(a.y2, g.bbox.y2),
  }),
  { x1: Infinity, y1: Infinity, x2: -Infinity, y2: -Infinity },
);

const f2 = (n) => Number(n).toFixed(2);
const PAD = 4;
const vbX = B.x1 - PAD, vbY = B.y1 - PAD;
const vbW = (B.x2 - B.x1) + 2 * PAD, vbH = (B.y2 - B.y1) + 2 * PAD;
const s = targetW / vbW;                 // user-units -> mm

const TUNE = { row: 0.30, maxLen: 2.0, inset: 1.0, expand: 0.2 };
const fillAttrs = [
  `inkstitch:fill_method="auto_fill"`,
  `inkstitch:row_spacing_mm="${f2(TUNE.row)}"`,
  `inkstitch:max_stitch_length_mm="${f2(TUNE.maxLen)}"`,
  `inkstitch:fill_underlay="true"`,
  `inkstitch:fill_underlay_inset_mm="${f2(TUNE.inset)}"`,
  `inkstitch:expand_mm="${f2(TUNE.expand)}"`,
].join(" ");

const NS = "http://inkstitch.org/namespace";
const out = [`<?xml version="1.0" encoding="UTF-8"?>`];
out.push(
  `<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkstitch="${NS}" ` +
  `width="${f2(vbW * s)}mm" height="${f2(vbH * s)}mm" ` +
  `viewBox="${f2(vbX)} ${f2(vbY)} ${f2(vbW)} ${f2(vbH)}" fill="none">`,
);
out.push(`<metadata><inkstitch:inkstitch_svg_version>3</inkstitch:inkstitch_svg_version></metadata>`);
for (const g of glyphs) out.push(`<path d="${g.d}" fill="${FILL_HEX}" ${fillAttrs} />`);
out.push(`</svg>`);

const OUT = join(HERE, "../../designs", `${STEM}.svg`);
writeFileSync(OUT, out.join("\n") + "\n");
console.log(`${FONT_FILE.padEnd(28)} "${TEXT}"  ${f2(vbW * s)} x ${f2(vbH * s)} mm  (${(vbW * s / 25.4).toFixed(2)}")  -> ${STEM}.svg`);
