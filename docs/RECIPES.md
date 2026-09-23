# Base recipes: satin, fill, bean — on hat twill

Three starting points, one per technique. Each is a preset in
`materials/presets.toml` (fabric `twill-hat-front`, 90/14 needle, Madeira Classic
40 black — swap the thread id for a colour) plus a minimal example SVG in
`designs/recipes/` that builds and passes the strict audit. The numbers come from
the professionally digitized corpus measured in `docs/QUALITY-BAR.md`.

| recipe | preset | example | audit profile | build |
|---|---|---|---|---|
| satin | `hat-twill-satin` | `designs/recipes/satin-bar.svg` | `satin-lettering` | 668 st, 2.3 m, satin_score 89 |
| fill | `hat-twill-fill` | `designs/recipes/fill-ring.svg` | `fill` | 5644 st, 14.6 m, 0.2 mm rows |
| bean | `hat-twill-bean` | `designs/recipes/bean-loop.svg` | `running` | 264 st, 0.3 m, triple bean |

```
source .venv/bin/activate
stitch from-svg designs/recipes/satin-bar.svg --preset hat-twill-satin --strict-audit
stitch from-svg designs/recipes/fill-ring.svg --preset hat-twill-fill  --strict-audit
stitch from-svg designs/recipes/bean-loop.svg --preset hat-twill-bean  --strict-audit
```

Every design starts from one of these. A mixed design (fill badge with satin
lettering on it) uses the preset of its dominant mass and per-path attributes for
the rest; the root `data-audit-profile` picks the bands.

## Satin — `hat-twill-satin`

What the pros do (Acre, Zenbul letters, HVGL Two, CHGL handwriting): every stroke
from ~1.0 mm to 5 mm is a satin column, one block per letter, zig-zag underlay
plus a centre walk, 0.36–0.40 spacing, counters as satin rings, junctions by
overlap. Bold letters are never filled.

Preset: spacing 0.40, unsplit columns up to 5.0 mm, zig-zag underlay peaks 1.2 mm
apart, centre walk 1.2 mm down-and-back, pull comp 0.2 (fabric).

Draw: a column is ONE `<path>` with `inkstitch:satin_column="true"`: two rail
subpaths first, then rung subpaths that cross both rails (direction guides, never
on a cap end). Root `data-require-explicit-rungs="true"` makes preflight enforce
it. Getting satin from art: `stitch_cli/tracing.py` (raster → columns),
`stitch lettering` (Ink/Stitch fonts), `--satin-mode outline` (fills → satin).

Per path:
- `data-satin-underlay="center"` on columns under ~1.3 mm (zig-zag + walk + top
  pass in a sub-millimetre column raises a cord).
- `data-satin-spacing-mm`, `data-satin-max-stitch-mm` (4–7), `data-pull-comp-mm`
  (fine serifs want less than 0.2).
- `data-trim-after="false"` + `data-min-jump-mm` to chain columns of one glyph.

## Fill — `hat-twill-fill`

What the pros do (Zenbul ring, CHGL napkin): tatami at 0.20 mm rows and 4.0 mm
stitches, staggered; one tatami underlay at 90° to the top fill at 1.0–1.6 mm;
then a satin run (1.8–2.9 mm) over every fill edge, with its own centre walk.

Preset: rows 0.2, stitch 4.0, underlay 1.2 mm perpendicular, border 1.8 mm.
About twice the thread of `hat-twill-badge-fill` (0.4 mm rows) — use that one
when a patch must stay flexible; use this one when the mass must read as cloth.

Draw: a filled `<path>` (`fill:#000000; stroke:none`), holes as extra subpaths
with `fill-rule:evenodd`, every subpath opening with an absolute `M`. Root
`data-audit-profile="fill"`.

Per path:
- `data-fill-angle="0"` — pros run the rows across the shape's long axis.
- `data-fill-border-mm="0"` opts a path out of the border; any other value
  overrides the preset width.
- `data-stitch-method="contour_fill"` for a spiral fill.

## Bean (rail) — `hat-twill-bean`

What the pro does (HVGL One): a hand-drawn pen path — one open path per pen
stroke, in writing order, loops drawn as loops, crossbars simply cross — sewn as a
running walk out and a triple bean back, 1.2–1.45 mm pitch, ~0.7 mm in tight
turns, one trim per glyph. This pen workflow needs hand-drawn centerlines; a medial-axis skeleton does
not preserve pen stroke order.

Preset: `bean_repeats = 1` (Ink/Stitch: 1 = triple; the old default 3 was seven
passes), pitch 1.25 mm on smoothed paths.

Draw: stroked paths (`fill:none`) with `data-stroke-method="bean_stitch"`; use
`data-smoothed="true"` on curves (tight tolerance, keeps the arc). A dead-end
branch: bean it out, then a copy of the branch with
`data-stroke-method="running_stitch"` walks back, and the pen continues — no
float, no trim. Hops under ~3 mm: `data-trim-after="false"` +
`data-min-jump-mm` above the hop. Root `data-audit-profile="running"`.

Per path: `data-bean-repeats` (1/2/3), `data-running-stitch-length-mm`
(0.8–5.0).

## What is deliberately not in the recipes

- Satin below ~1.0 mm (trace_lib floor 1.05): the pros satin 0.95 mm, but on
  twill it cords; go monoline/bean instead.
- Bean over a fill: the pro napkin uses 1.0–1.35 mm satin for handwriting on a
  fill, not bean.
- Row spacing below 0.2 or above 0.45: outside every pro file we have measured.
