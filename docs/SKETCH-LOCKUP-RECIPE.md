# Sketch-to-lockup digitization recipe (v3)

> **July 2026 update.** For pure lettering/wordmark traces the per-job-copy
> pattern below is superseded: the validated machinery now lives in
> [`scripts/trace_lib.py`](../scripts/trace_lib.py) and a job is a thin driver
> ([`scripts/trace_paris_review.py`](../scripts/trace_paris_review.py) is the
> reference). Builds are auto-scored against
> [`docs/QUALITY-BAR.md`](QUALITY-BAR.md); the satin defaults changed since
> this recipe was written (0.40mm nominal satin spacing with 0.38mm on broad
> traced curves, satin width floor ~1.05mm,
> per-path trim suppression). This document remains the recipe for **character
> sketches with solid fills** (width-split + potrace), which trace_lib does
> not cover yet.

The locked-in recipe for turning a hand-drawn marker **lockup** (a character +
handwritten text, e.g. the Makeout Summer rat) into a hat-ready `.pes`. Settled
July 2026 after several iterations; these are the defaults to reuse, not
re-derive. Reference implementation:
[`scripts/trace_lockup.py`](../scripts/trace_lockup.py) (copy per job). Worked
example outputs: `out/archive/makeout-summer-lockup-v3*.pes` (iterations
archived 2026-07-25; the fabric-validated final is `out/makeout-summer-lockup-v4-satin.pes`).

## Pipeline and sizing

```text
scripts/trace_lockup.py  (raster sketch -> width-split -> SVG)
      -> stitch_cli.hershey.smooth_svg_paths   (corner-preserving spline pass)
      -> stitch from-svg --preset hat-twill-black-detail   -> strict audit -> out/<name>.pes
      -> stitch preview --png
```

The trace splits ink by local stroke width: thin marker becomes skeleton
centerlines, solid marker becomes potrace fills. `TARGET_H_MM` sets the design
height and width follows the bbox aspect (width = height x bbox_w / bbox_h).
~90mm wide is the safe default; to **max the 4" hoop**, size so the stitched
bounds land just under 100mm on both axes (the toolchain warns above ~100.4mm) —
for the Makeout aspect that was `TARGET_H_MM = 63` giving 99.8 x 63.6mm.

The width-split pixel thresholds (`THICK_HALFWIDTH_PX`, `SUPPORT_HALFWIDTH_PX`,
`MIN_FILL_AREA_PX`, `PRUNE_PX`, `MIN_SPECK_PX`) are in **source pixels**, so
rescale them whenever a new drawing's resolution differs — halve the widths and
quarter the areas for an image half the pixel size. Check the EDT full-width
percentiles: the threshold should sit between the linework (~p75) and the solid
masses (~p95). Which shapes become fills is per-drawing too — it may be a hat
scribble, heart-sunglasses lenses, a mouth, or a nose; small outline hearts
scattered around stay bean linework, so keep them out of the text and fill zones.

## Character (the rat) — bean + fills

The drawn character stays sketchy: centerlines for the linework, fills only for
genuinely solid ink.

- **Linework** -> `bean_stitch` (triple pass) for weight on twill.
- **Sunglass bridge** between the heart eyes -> single `running_stitch`, thin and
  light so it doesn't read as heavy as the rest of the linework.
- **Hair / hat scribble** -> **consolidate the thick mask first**, then
  `contour_fill`. The raw scribble mask is holey and tendrilly, so `auto_fill`
  fragments it into short rows/jumps/micro-stitches and stitches out messy.
  Consolidate with `binary_closing(disk(SUPPORT_HALFWIDTH+4))` +
  `binary_fill_holes` + `binary_opening(disk(4))`, keeping the largest component;
  `contour_fill` then follows the shape for a clean hair-like texture.
- **Heart eyes** -> `auto_fill` with `data-fill-angle="45"` for a diagonal sheen
  distinct from the flat-row hat/cheek/nose.
- **Cheek bar, nose dot** -> plain `auto_fill`.

## Text (MAKEOUT / SUMMER / year) — satin

The handwritten text is rendered as bold satin columns, sized so the small
counters stay open. All lettering uses `data-stitch-method="satin"` (converted by
the `stroke_to_satin` pre-pass).

- **Force text out of the fill layer.** Bold handwritten digits are wide enough
  to be classified "thick" and become solid fills — which closes the counters in
  `0`/`6`. Subtract the text zone from the `thick` mask so all lettering stays a
  satin centerline.
- Big words use satin width `clamp(w*1.75, 1.3, 2.05)`mm. The **small year line
  uses a single-repeat bean stitch** instead of sub-millimeter satin. This keeps
  the `0`/`6` counters open without creating an unstable, over-dense satin tier.
- **Never geometrically widen a small counter** (e.g. scaling the "6" loop
  outward). It scales only part of the glyph and tears it at the join, so the 6
  looks broken. Switching that tier to bean stitch is the correct fix.

Smoothing runs after the trace: `hershey.smooth_svg_paths` resamples the paths
(corner-preserving Catmull-Rom), and `tuning.py` honors the resulting
`data-smoothed="true"` with a tighter running/bean tolerance so the arcs survive
to the needle instead of decimating back to facets.

The one recurring gotcha, worth stating on its own: the region classifiers
(`is_text`, `is_bridge`, `is_hat_region`, `is_heart_region`, and the inline
`hatzone`) are in **source-pixel coordinates** and **must be re-derived for every
new drawing** from the gridded debug overlay the script writes. Even a small art
tweak shifts the layout and silently breaks the zones — a moved heart gets tagged
as hat, the bridge catches nothing, and so on. Always eyeball the debug PNG
before building.

The lockup tracer now shares the topology-safe 8-neighbor rule used by
`trace_lib.py`, so diagonal pixels beside an orthogonal bridge do not create
false loops or junctions. Generated SVGs declare the `mixed` audit profile, and
all production CLI builds fail closed unless an operator explicitly supplies
`--no-strict-audit` for a scrap-only experiment.
