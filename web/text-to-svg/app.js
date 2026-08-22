// Fancy Text → SVG generator.
// Uses opentype.js to convert text in a chosen web font into vector path data,
// then wraps those paths with the same inkstitch:* tuning attributes that
// stitch_cli/tuning.py would produce — output drops straight into designs/.

import {
  DEFAULT_TUNING,
  INKSTITCH_NS,
  inkstitchAttrs,
  metadataXml,
} from "./inkstitch.js";

const FONTS = [
  { id: "unifrakturcook-bold",   label: "UnifrakturCook (blackletter)", file: "UnifrakturCook-Bold.ttf",   group: "Blackletter" },
  { id: "pirata-one",            label: "Pirata One (blackletter)",     file: "PirataOne-Regular.ttf",     group: "Blackletter" },
  { id: "great-vibes",           label: "Great Vibes (formal script)",  file: "GreatVibes-Regular.ttf",    group: "Script" },
  { id: "pinyon-script",         label: "Pinyon Script (formal script)",file: "PinyonScript-Regular.ttf",  group: "Script" },
  { id: "pacifico",              label: "Pacifico (bold script)",       file: "Pacifico-Regular.ttf",      group: "Script" },
  { id: "berkshire-swash",       label: "Berkshire Swash (bold script)",file: "BerkshireSwash-Regular.ttf",group: "Script" },
  { id: "lobster",               label: "Lobster (bold script)",        file: "Lobster-Regular.ttf",       group: "Script" },
  { id: "alfa-slab-one",         label: "Alfa Slab One (slab)",         file: "AlfaSlabOne-Regular.ttf",   group: "Display" },
  { id: "bevan",                 label: "Bevan (slab)",                 file: "Bevan-Regular.ttf",         group: "Display" },
  { id: "bowlby-one",            label: "Bowlby One (display)",         file: "BowlbyOne-Regular.ttf",     group: "Display" },
  { id: "bungee",                label: "Bungee (display)",             file: "Bungee-Regular.ttf",        group: "Display" },
  { id: "limelight",             label: "Limelight (display)",          file: "Limelight-Regular.ttf",     group: "Display" },
];

// Pulled from materials/threads.toml.
const THREAD_COLORS = [
  { hex: "#FFF859", label: "Madeira 1147 — OHHI yellow" },
  { hex: "#000000", label: "Madeira 1023 — black" },
  { hex: "#C41820", label: "Madeira 1147 — OHHI red" },
];

// ---------- Font loading (cached) ----------
const fontCache = new Map();
function loadFont(file) {
  if (fontCache.has(file)) return fontCache.get(file);
  const p = new Promise((resolve, reject) => {
    opentype.load(`fonts/${file}`, (err, font) => {
      if (err) reject(err);
      else resolve(font);
    });
  });
  fontCache.set(file, p);
  return p;
}

// ---------- Unicode fancy-text substitution ----------
// Maps every ASCII letter to a parallel Unicode block. Letters that don't
// exist in the parallel block (e.g. fraktur "C" sits at U+212D, not in the
// math fraktur run) use explicit overrides. Anything outside A-Z/a-z passes
// through unchanged.
function buildMap(upperStart, lowerStart, overrides = {}) {
  const m = {};
  for (let i = 0; i < 26; i++) {
    const U = String.fromCharCode(0x41 + i);
    const L = String.fromCharCode(0x61 + i);
    m[U] = overrides[U] || String.fromCodePoint(upperStart + i);
    m[L] = overrides[L] || String.fromCodePoint(lowerStart + i);
  }
  return m;
}

const UNICODE_STYLES = {
  bold:        buildMap(0x1d400, 0x1d41a),
  italic:      buildMap(0x1d434, 0x1d44e, { h: "ℎ" }),
  bolditalic:  buildMap(0x1d468, 0x1d482),
  script:      buildMap(0x1d49c, 0x1d4b6, {
                  B:"ℬ", E:"ℰ", F:"ℱ", H:"ℋ", I:"ℐ",
                  L:"ℒ", M:"ℳ", R:"ℛ",
                  e:"ℯ", g:"ℊ", o:"ℴ" }),
  boldscript:  buildMap(0x1d4d0, 0x1d4ea),
  fraktur:     buildMap(0x1d504, 0x1d51e, {
                  C:"ℭ", H:"ℌ", I:"ℑ", R:"ℜ", Z:"ℨ" }),
  doublestruck:buildMap(0x1d538, 0x1d552, {
                  C:"ℂ", H:"ℍ", N:"ℕ", P:"ℙ", Q:"ℚ",
                  R:"ℝ", Z:"ℤ" }),
  monospace:   buildMap(0x1d670, 0x1d68a),
  sansbold:    buildMap(0x1d5d4, 0x1d5ee),
  sansitalic:  buildMap(0x1d608, 0x1d622),
};

function applyUnicodeStyle(text, styleId) {
  const map = UNICODE_STYLES[styleId];
  if (!map) return text;
  let out = "";
  for (const ch of text) out += map[ch] ?? ch;
  return out;
}

// ---------- Path geometry ----------
// opentype.js path.toPathData(decimals) → SVG path "d" string.
// path.getBoundingBox() → {x1,y1,x2,y2}.

function emptyBBox() {
  return { x1: Infinity, y1: Infinity, x2: -Infinity, y2: -Infinity };
}
function unionBBox(a, b) {
  return {
    x1: Math.min(a.x1, b.x1),
    y1: Math.min(a.y1, b.y1),
    x2: Math.max(a.x2, b.x2),
    y2: Math.max(a.y2, b.y2),
  };
}
function expandBBox(b, pad) {
  return { x1: b.x1 - pad, y1: b.y1 - pad, x2: b.x2 + pad, y2: b.y2 + pad };
}

// Render N lines of straight text, returns array of {d, bbox}.
function renderLines(font, lines, fontSize) {
  const lineHeight = fontSize * 1.2;
  const out = [];
  let y = fontSize; // baseline of first line
  for (const line of lines) {
    if (!line.length) { y += lineHeight; continue; }
    const path = font.getPath(line, 0, y, fontSize);
    out.push({ d: path.toPathData(2), bbox: path.getBoundingBox() });
    y += lineHeight;
  }
  return out;
}

// Render text along a circular arc. curvature ∈ [-1, 1]; 0 = flat.
// Positive curves up (text reads concave-down, like a smile flipped — i.e. an arch over a hat).
function renderArch(font, text, fontSize, curvature) {
  if (Math.abs(curvature) < 0.01) return renderLines(font, [text], fontSize);
  // Compute the unrotated layout positions of each glyph along a baseline.
  // We use opentype's per-glyph paths so we can rotate each one independently.
  const glyphs = [];
  let advance = 0;
  for (const ch of text) {
    const glyph = font.charToGlyph(ch);
    glyphs.push({ glyph, x: advance });
    advance += (glyph.advanceWidth || 0) * (fontSize / font.unitsPerEm);
  }
  const totalWidth = advance;
  // Choose a radius such that the text sweeps an arc proportional to curvature.
  const maxAngle = curvature * Math.PI; // -π … π
  const radius = totalWidth / Math.max(Math.abs(maxAngle), 0.001);
  // Center of the arc, placed below (or above for negative curvature) the baseline.
  const cx = totalWidth / 2;
  const cy = fontSize + radius; // for positive curvature: arc bows up over this center
  const sign = curvature > 0 ? -1 : 1;

  const outPaths = [];
  let bbox = emptyBBox();
  for (const { glyph, x } of glyphs) {
    // Position at midpoint of the glyph along the baseline.
    const t = (x + (glyph.advanceWidth * fontSize) / font.unitsPerEm / 2) / totalWidth;
    const angle = (t - 0.5) * maxAngle;
    const px = cx + Math.sin(angle) * radius;
    const py = cy + sign * Math.cos(angle) * radius;
    // Render glyph path at origin, then translate+rotate.
    const path = glyph.getPath(0, 0, fontSize);
    if (!path.commands.length) continue;
    // Apply transform: translate to (px, py), rotate by -angle*sign.
    const cos = Math.cos(angle * -sign);
    const sin = Math.sin(angle * -sign);
    for (const cmd of path.commands) {
      transformPoint(cmd, "x", "y", cos, sin, px, py);
      transformPoint(cmd, "x1", "y1", cos, sin, px, py);
      transformPoint(cmd, "x2", "y2", cos, sin, px, py);
    }
    const d = path.toPathData(2);
    outPaths.push({ d, bbox: path.getBoundingBox() });
    bbox = unionBBox(bbox, path.getBoundingBox());
  }
  return outPaths;
}

function transformPoint(cmd, xKey, yKey, cos, sin, tx, ty) {
  if (cmd[xKey] === undefined) return;
  const x = cmd[xKey];
  const y = cmd[yKey];
  cmd[xKey] = x * cos - y * sin + tx;
  cmd[yKey] = x * sin + y * cos + ty;
}

// ---------- SVG assembly ----------
function buildSvg({ paths, fillHex, effects, viewBox, embroideryReady }) {
  const ns = `xmlns="http://www.w3.org/2000/svg"`;
  const inkNs = embroideryReady ? ` xmlns:inkstitch="${INKSTITCH_NS}"` : "";
  const lines = [];
  lines.push(`<svg ${ns}${inkNs} viewBox="${viewBox.join(" ")}" width="${viewBox[2]}" height="${viewBox[3]}" fill="none">`);

  if (effects.gradient.enabled) {
    lines.push(`<title>Preview-only — gradient is not embroidery-ready.</title>`);
    lines.push(
      `<defs><linearGradient id="fancy-gradient" x1="0" y1="0" x2="1" y2="0">` +
        `<stop offset="0" stop-color="${effects.gradient.from}" />` +
        `<stop offset="1" stop-color="${effects.gradient.to}" />` +
      `</linearGradient></defs>`,
    );
  }
  if (embroideryReady) lines.push(metadataXml());

  const mainFill = effects.gradient.enabled ? `url(#fancy-gradient)` : fillHex;

  // Drop shadow: emit first so it sits beneath the main fill (and stitches first).
  if (effects.shadow.enabled) {
    const dx = effects.shadow.dx;
    const dy = effects.shadow.dy;
    const shadowAttrs = embroideryReady ? inkstitchAttrs("fill") : "";
    for (const p of paths) {
      lines.push(
        `<path d="${p.d}" transform="translate(${dx} ${dy})" fill="${effects.shadow.hex}" ${shadowAttrs} />`,
      );
    }
  }

  // Main fill paths.
  const fillAttrs = embroideryReady ? inkstitchAttrs("fill") : "";
  for (const p of paths) {
    lines.push(`<path d="${p.d}" fill="${mainFill}" ${fillAttrs} />`);
  }

  // Outline: emit stroked version on top with the satin attrs, separate color.
  if (effects.outline.enabled) {
    const w = effects.outline.width;
    const satinAttrs = embroideryReady ? inkstitchAttrs("satin") : "";
    for (const p of paths) {
      lines.push(
        `<path d="${p.d}" fill="none" stroke="${effects.outline.hex}" stroke-width="${w}" ${satinAttrs} />`,
      );
    }
  }

  lines.push(`</svg>`);
  return lines.join("\n");
}

function computeViewBox(paths, padding = 8) {
  let bb = emptyBBox();
  for (const p of paths) bb = unionBBox(bb, p.bbox);
  bb = expandBBox(bb, padding);
  if (!isFinite(bb.x1)) return [0, 0, 100, 100];
  return [
    Math.floor(bb.x1),
    Math.floor(bb.y1),
    Math.ceil(bb.x2 - bb.x1),
    Math.ceil(bb.y2 - bb.y1),
  ];
}

// ---------- DOM wiring ----------
function $(id) { return document.getElementById(id); }

function populateFonts() {
  const sel = $("font");
  const groups = {};
  for (const f of FONTS) (groups[f.group] ||= []).push(f);
  for (const [g, items] of Object.entries(groups)) {
    const og = document.createElement("optgroup");
    og.label = g;
    for (const f of items) {
      const opt = document.createElement("option");
      opt.value = f.id;
      opt.textContent = f.label;
      og.appendChild(opt);
    }
    sel.appendChild(og);
  }
}

function populateColors() {
  const sel = $("color-preset");
  for (const c of THREAD_COLORS) {
    const opt = document.createElement("option");
    opt.value = c.hex;
    opt.textContent = c.label;
    opt.style.background = c.hex;
    sel.appendChild(opt);
  }
}

function readInputs() {
  const fontId = $("font").value;
  const fontDef = FONTS.find((f) => f.id === fontId);
  return {
    text: $("text").value,
    fontDef,
    fontSize: parseFloat($("size").value) || 80,
    fillHex: $("color-hex").value,
    unicodeStyle: $("unicode-style").value,
    effects: {
      outline: {
        enabled: $("outline-on").checked,
        width: parseFloat($("outline-width").value) || 4,
        hex: $("outline-hex").value,
      },
      shadow: {
        enabled: $("shadow-on").checked,
        dx: parseFloat($("shadow-dx").value) || 0,
        dy: parseFloat($("shadow-dy").value) || 0,
        hex: $("shadow-hex").value,
      },
      arch: {
        enabled: $("arch-on").checked,
        curvature: parseFloat($("arch-curvature").value) || 0,
      },
      gradient: {
        enabled: $("gradient-on").checked,
        from: $("gradient-from").value,
        to: $("gradient-to").value,
      },
    },
  };
}

async function render() {
  const inputs = readInputs();
  const { fontDef, fontSize, fillHex, unicodeStyle, effects } = inputs;
  const note = $("note");
  note.textContent = "";

  if (!fontDef) return;
  let font;
  try {
    font = await loadFont(fontDef.file);
  } catch (e) {
    note.textContent = `Font load failed: ${e.message}`;
    return;
  }

  const styledText = applyUnicodeStyle(inputs.text, unicodeStyle);
  $("unicode-output").value = styledText;

  const lines = styledText.split("\n");

  let paths;
  if (effects.arch.enabled && lines.length === 1) {
    paths = renderArch(font, styledText, fontSize, effects.arch.curvature);
  } else {
    paths = renderLines(font, lines, fontSize);
  }
  paths = paths.filter((p) => p.d && p.d.length);

  const viewBox = computeViewBox(paths);
  const embroideryReady = !effects.gradient.enabled;
  if (effects.gradient.enabled) {
    note.textContent =
      "⚠ Gradient enabled — output is preview-only (no inkstitch attributes). Disable to get an embroidery-ready SVG.";
  } else if (effects.arch.enabled && lines.length > 1) {
    note.textContent = "Arch is applied to the first line only — multi-line text falls back to flat layout.";
  }

  const svg = buildSvg({ paths, fillHex, effects, viewBox, embroideryReady });
  $("preview").innerHTML = svg;
  $("preview").querySelector("svg")?.setAttribute("style", "max-width:100%;height:auto;background:#f5f5f5;");
  $("svg-source").value = svg;

  $("download").onclick = () => {
    const blob = new Blob([svg], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filenameFor(inputs.text || "text");
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };
}

function filenameFor(text) {
  const slug = text
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9-]/g, "")
    .slice(0, 40) || "text";
  return `text-${slug}.svg`;
}

function bind() {
  const ids = [
    "text", "font", "size", "color-preset", "color-hex", "unicode-style",
    "outline-on", "outline-width", "outline-hex",
    "shadow-on", "shadow-dx", "shadow-dy", "shadow-hex",
    "arch-on", "arch-curvature",
    "gradient-on", "gradient-from", "gradient-to",
  ];
  for (const id of ids) {
    const el = $(id);
    if (!el) continue;
    el.addEventListener("input", render);
    el.addEventListener("change", render);
  }
  $("color-preset").addEventListener("change", (e) => {
    if (e.target.value) $("color-hex").value = e.target.value;
    render();
  });
  $("copy-unicode").addEventListener("click", () => {
    navigator.clipboard.writeText($("unicode-output").value);
  });
}

window.addEventListener("DOMContentLoaded", () => {
  populateFonts();
  populateColors();
  bind();
  render();
});
