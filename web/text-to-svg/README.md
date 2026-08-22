# Fancy Text → SVG

Browser tool for turning text into an embroidery-ready SVG. The browser does the font rendering (so web fonts look right), `opentype.js` converts the rendered glyphs to vector paths, and the output ships pre-tuned for Ink/Stitch — same `inkstitch:*` attributes that `stitch_cli/tuning.py` would otherwise stamp on later.

## Run it

ES modules need `http://`, not `file://`. Serve the directory:

```bash
cd web/text-to-svg
python3 -m http.server 8000
open http://localhost:8000
```

## Use it

1. Type your text.
2. Pick a font and a thread color.
3. (Optional) toggle effects:
   - **Outline** — adds a stroked path with `inkstitch:satin_column="true"`.
   - **Drop shadow** — adds an offset duplicate fill, sewn first (separate thread color).
   - **Arch** — single-line text along a circular arc.
   - **Gradient** — preview-only; disables embroidery tuning and won't sew on the PE900.
4. (Optional) substitute Unicode styles (𝐛𝐨𝐥𝐝, 𝓼𝓬𝓻𝓲𝓹𝓽, 𝔣𝔯𝔞𝔨𝔱𝔲𝔯, …). The substituted text is what gets rendered into the SVG, so the chosen font needs glyphs for those codepoints — most decorative fonts won't, in which case copy the unicode out for use elsewhere.
5. Hit **Download SVG**.
6. Drop the file into `designs/`, then run `stitch from-svg designs/text-….svg --preset hat-twill-yellow-on-black`.

## Tuning defaults

Output uses `hat-twill-yellow-on-black` preset values from `materials/presets.toml` + `materials/fabrics.toml`:

| param | value |
| --- | --- |
| `row_spacing_mm` | 0.40 |
| `max_stitch_length_mm` | 3.00 |
| `pull_compensation_mm` | 0.20 |
| `fill_underlay_inset_mm` | 1.00 |

If you target a different preset, the `stitch from-svg` re-tuning pass will overwrite these — they're set to make the SVG sewable straight out of the box, not to lock you into one fabric.

## Adding fonts

1. Drop a `.ttf` or `.otf` file into `fonts/`.
2. Add an entry to the `FONTS` array at the top of `app.js`.

Look for fonts with closed contours and enough weight to survive stitching — hairline scripts won't sew cleanly at typical patch sizes.

## Files

```
index.html        UI
app.js            font loading, glyph→path, layout, effects, SVG emit
inkstitch.js      mirrors stitch_cli/tuning.py
styles.css        layout
fonts/            curated TTFs (Google Fonts, OFL)
lib/opentype.min.js  vendored opentype.js v1.3.4
```
