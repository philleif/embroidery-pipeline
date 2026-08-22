// Headless renderer for the ROCKTAVIA "heavy metal band logo".
//
// Spiked-slab / death-groove lane: AlfaSlabOne ultra-bold slab caps as the
// legible base, framed by custom vector spikes — a spike row above and below
// the word plus horizontal dagger barbs off each side. All shapes emit as
// fills, so `stitch from-svg` turns them into solid auto-fill regions.
//
// Coordinate system: 1 user unit = 1 mm (viewBox carries real mm, svg
// width/height in mm). Total width is held under the PE900's 130 mm field width.
//
// Run: node render-rocktavia.mjs [profile] [outname]
//   profile: even | flame | horns   (spike height shape; default horns)
//   outname: file stem under ../../designs/  (default rocktavia)

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import opentype from "./lib/opentype.min.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const PROFILE = process.argv[2] || "horns";
const STEM = process.argv[3] || "rocktavia";
const TARGET_W = process.argv[4] ? parseFloat(process.argv[4]) : 0; // total mm; 0 = natural 1:1

// ---------- design parameters (mm) ----------
const WORD       = "ROCKTAVIA";
const FONT_FILE  = "AlfaSlabOne-Regular.ttf";
const WORD_W     = 84;        // ink width of the wordmark
const TRACK_EM   = -0.012;    // letter tracking, em
const FILL_HEX   = "#C41820"; // Madeira red (preview tint; from-svg re-tints)

// spike frame
const N_SPIKES   = 12;        // spikes per edge
const H_MIN      = 3.0;       // shortest spike (mm)
const H_MAX      = 9.0;       // tallest spike (mm)
const BASE_INSET = 0.8;       // connecting band reaches this far into the caps
const BASE_RISE  = 0.6;       // spike valleys sit this far outside the caps
const BAR_PAD    = 1.5;       // spike row overhangs the word ink each side
const SWEEP      = 4.0;       // outermost spikes sweep this far outward (horns)
const SWEEP_X    = 2.0;       // extra height bump on the swept end spikes
const DAGGER_RCH = 6.5;       // side dagger reach beyond the word ink
const DAGGER_HALF = 3.2;      // side dagger base half-height

const OUT = join(HERE, "../../designs", `${STEM}.svg`);

// ---------- font ----------
const buf = readFileSync(join(HERE, "fonts", FONT_FILE));
const font = opentype.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
const upm = font.unitsPerEm;

const chars = [...WORD];
let advEm = 0;
chars.forEach((ch, i) => {
  advEm += font.charToGlyph(ch).advanceWidth / upm;
  if (i < chars.length - 1) advEm += TRACK_EM;
});
const F = WORD_W / advEm;
const capRatio = (font.tables.os2 && font.tables.os2.sCapHeight ? font.tables.os2.sCapHeight : 700) / upm;
const CH = capRatio * F;

// ---------- glyphs ----------
const glyphPaths = [];
let pen = -WORD_W / 2;
for (const ch of chars) {
  const g = font.charToGlyph(ch);
  const p = g.getPath(pen, 0, F);
  const d = p.toPathData(2);
  if (d) glyphPaths.push({ d, bbox: p.getBoundingBox() });
  pen += (g.advanceWidth / upm + TRACK_EM) * F;
}
const wb = glyphPaths.reduce(
  (a, g) => ({
    x1: Math.min(a.x1, g.bbox.x1), x2: Math.max(a.x2, g.bbox.x2),
    y1: Math.min(a.y1, g.bbox.y1), y2: Math.max(a.y2, g.bbox.y2),
  }),
  { x1: Infinity, x2: -Infinity, y1: Infinity, y2: -Infinity },
);

// ---------- spike geometry ----------
const f2 = (n) => Number(n).toFixed(2);
const poly = (pts) => "M " + pts.map(([x, y]) => `${f2(x)} ${f2(y)}`).join(" L ") + " Z";

const barLeft = wb.x1 - BAR_PAD;
const barRight = wb.x2 + BAR_PAD;
const span = barRight - barLeft;
const pitch = span / N_SPIKES;

function shape(t) {
  if (PROFILE === "even") return 0.5;
  if (PROFILE === "flame") return 1 - Math.abs(2 * t - 1);          // tall center
  return Math.pow(Math.abs(2 * t - 1), 1.4);                        // horns: tall ends
}

// One connected polygon: a thin connecting band on the word, spikes rising off it.
// edge="top": band at cap top, spikes point up.  edge="bottom": mirror, point down.
function spikeRow(edge) {
  const up = edge === "top";
  const bandInner = up ? wb.y1 + BASE_INSET : wb.y2 - BASE_INSET;   // into the caps
  const valleyY = up ? wb.y1 - BASE_RISE : wb.y2 + BASE_RISE;       // spike valleys
  const dir = up ? -1 : 1;                                          // spike growth sign

  const pts = [];
  pts.push([barLeft, bandInner]);            // bottom-left of band
  pts.push([barLeft, valleyY]);              // up to first valley
  for (let i = 0; i < N_SPIKES; i++) {
    const t = N_SPIKES === 1 ? 0.5 : i / (N_SPIKES - 1);
    let h = H_MIN + (H_MAX - H_MIN) * shape(t);
    let px = barLeft + (i + 0.5) * pitch;    // peak x
    // sweep the two outermost spikes outward into horns
    if (PROFILE === "horns") {
      if (i === 0) { px -= SWEEP; h += SWEEP_X; }
      if (i === N_SPIKES - 1) { px += SWEEP; h += SWEEP_X; }
    }
    pts.push([px, valleyY + dir * h]);       // peak
    pts.push([barLeft + (i + 1) * pitch, valleyY]); // next valley
  }
  pts.push([barRight, bandInner]);           // down to band
  return poly(pts);
}

function dagger(side) {
  const left = side === "left";
  const yC = -CH / 2;
  const baseX = left ? wb.x1 + 1.0 : wb.x2 - 1.0;
  const tipX = left ? wb.x1 - DAGGER_RCH : wb.x2 + DAGGER_RCH;
  return poly([
    [tipX, yC],
    [baseX, yC - DAGGER_HALF],
    [baseX, yC + DAGGER_HALF],
  ]);
}

const spikes = [spikeRow("top"), spikeRow("bottom"), dagger("left"), dagger("right")];

// ---------- global bbox ----------
const topExtent = wb.y1 - BASE_RISE - (H_MAX + SWEEP_X);
const botExtent = wb.y2 + BASE_RISE + (H_MAX + SWEEP_X);
const hornX = PROFILE === "horns" ? SWEEP : 0;
const leftExtent = Math.min(wb.x1 - DAGGER_RCH, barLeft - hornX);
const rightExtent = Math.max(wb.x2 + DAGGER_RCH, barRight + hornX);

const PAD = 1.0;
const vbX = leftExtent - PAD, vbY = topExtent - PAD;
const vbW = (rightExtent - leftExtent) + 2 * PAD, vbH = (botExtent - topExtent) + 2 * PAD;

// ---------- emit ----------
const TUNE = { row: 0.30, maxLen: 2.0, inset: 1.0, expand: 0.2 };
const fillAttrs = [
  `inkstitch:fill_method="auto_fill"`,
  `inkstitch:row_spacing_mm="${f2(TUNE.row)}"`,
  `inkstitch:max_stitch_length_mm="${f2(TUNE.maxLen)}"`,
  `inkstitch:fill_underlay="true"`,
  `inkstitch:fill_underlay_inset_mm="${f2(TUNE.inset)}"`,
  `inkstitch:expand_mm="${f2(TUNE.expand)}"`,
].join(" ");

// Uniform scale: keep the viewBox in natural units, set the physical mm size to
// the target so the whole design (word + frame + stitches) scales together.
const s = TARGET_W > 0 ? TARGET_W / vbW : 1;
const physW = vbW * s, physH = vbH * s;

const NS = "http://inkstitch.org/namespace";
const out = [`<?xml version="1.0" encoding="UTF-8"?>`];
out.push(
  `<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkstitch="${NS}" ` +
  `width="${f2(physW)}mm" height="${f2(physH)}mm" ` +
  `viewBox="${f2(vbX)} ${f2(vbY)} ${f2(vbW)} ${f2(vbH)}" fill="none">`,
);
out.push(`<metadata><inkstitch:inkstitch_svg_version>3</inkstitch:inkstitch_svg_version></metadata>`);
for (const d of spikes) out.push(`<path d="${d}" fill="${FILL_HEX}" ${fillAttrs} />`);
for (const g of glyphPaths) out.push(`<path d="${g.d}" fill="${FILL_HEX}" ${fillAttrs} />`);
out.push(`</svg>`);

writeFileSync(OUT, out.join("\n") + "\n");
console.log(`[${PROFILE}] scale ${s.toFixed(3)}  font ${(F * s).toFixed(2)}mm  cap ${(CH * s).toFixed(2)}mm  spikes/edge ${N_SPIKES}`);
console.log(`design ${physW.toFixed(1)} x ${physH.toFixed(1)} mm  (${(physW / 25.4).toFixed(2)}" wide)  ->  ${OUT}`);
