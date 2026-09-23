# The quality bar

Every production build now ends with a scorecard and **fails closed by
default** when its technique-aware bands fail. You can re-score any file with
`stitch audit <file.pes>`. The bands are measured, not invented: they
come from the satin-heavy professionally digitized files in `out/pro-samples/`
(Philip Bjerknes' Cobble Hills, Desperate, Yearn, and his version of the Paris
Review masthead). Falling outside a band means doing something no professional
file in the corpus does.

```
stitch audit out/paris-review-5in.pes
stitch audit out/*.pes --strict      # strict is explicit on this read-only verb
```

All length, reversal, satin-width, and pitch calculations are scoped to
continuous stitch blocks. They never bridge a JUMP, TRIM, STOP, or colour
change, and the quality envelope ignores positioning-only jumps (including the
phantom jump used by `stitch offset`). `stitch inspect` reports machine bounds
and stitch-only bounds separately when placement diagnostics need both.

## Scope

SVG builds select technique-aware profiles automatically: pure fill checks
fill density and stitch health; pure running and mixed work retain the universal
hotspot and trim checks; unmarked pure satin and trace-lib wordmarks use the
full pro bands. Closed satin
borders can declare `data-audit-profile="satin-outline"` on the SVG root so
their large empty counters do not create false density/reversal failures. The
tracer emits this marker for the heart design. SVG, Hershey, swatch, lettering,
generative, conversion, and offset builds all return non-zero on failure unless
the operator explicitly supplies `--no-strict-audit`. Deployment independently
rechecks the universal `mixed` profile and refuses unsafe files unless
`--allow-audit-failures` is supplied for a deliberate scrap-only test.

The bands were derived from **satin lettering**, and that is where they are
strict. Mixed-technique designs fail some of them legitimately: the Makeout
Summer lockup — validated on fabric, called perfect — fails `short_pct`,
`p10_len` and `trims_per_1k`, because its bean-stitch rat linework produces
short stitches on tight curves and its scattered hearts are genuine islands
that each need a trim. For a bean-heavy or multi-island design, read those
three as advisory. On a bean-ONLY design ignore the satin metrics entirely —
a bean stitch is a forward-back-forward repeat, so the analyzer reads its
repeats as satin reversals and `satin_pitch`/`satin_w10` become nonsense
(verified on gossip-line-hershey-v12). The same holds for a **fill-ONLY**
design: the PTM badge is one solid potraced mass with the letters knocked out
of it and contains no satin at all, yet it reports `satin_score` 14%,
`satin_pitch` 1.53mm and `satin_w10` 0.92mm, because the analyzer reads
auto_fill's row reversals at the shape boundary as columns. Judge a fill design
on `density`, `short_pct`, `p10_len` and `trims_per_1k` only. A *pure satin
lettering* build that fails anything should not go to the machine.

## The bands and what they're really saying

| metric | band | pro corpus |
|---|---|---|
| `density` | 55–155 st/cm² | 64–146 |
| `peak_1mm` | ≤15 penetrations in any 1 mm cell | 9–15 |
| `satin_score` | ≥68% | 70–79 |
| `short_pct` | ≤6.5% | 1.8–5.5 |
| `p10_len` | ≥0.92 mm | 0.98–1.08 |
| `satin_pitch` | 0.30–0.44 mm | 0.32–0.40 |
| `satin_w10` | ≥1.00 mm | 1.04–1.53 |
| `trims_per_1k` | ≤9 | 3.6–8.3 |
| `long_stitch_over_5_count` | 0 | no sewn floats over 5mm |
| `satin_heading_p90_deg` | ≤30° | interior rail-direction smoothness |
| `satin_abrupt_25_pct` | ≤15% | abrupt interior rail turns |

Presets request 0.40mm zig-zag spacing. For broad traced columns, `trace_lib`
requests 0.38mm locally because Ink/Stitch's measured same-rail pitch grows on
curves. Narrow columns stay at 0.40mm so the correction does not create a new
1mm penetration hotspot.

Three of these are the same fact seen from different angles. `satin_w10`,
`p10_len` and `short_pct` all move together, because **pro files contain no thin
satin**. Every one of them floors around 1.0–1.5 mm. A trace that chases the
artwork's hairlines below that produces narrow columns, narrow columns produce
sub-0.8 mm stitches, and sub-0.8 mm stitches produce beading, thread breaks and
a raised cord instead of a flat column. Fix the width floor and the other two
follow on their own. `TraceConfig` enforces 1.05mm as a hard minimum; artwork
below it must become bean/running stitch rather than bypassing the floor with a
per-job flag.

`long_stitch_over_5_count` is universal and fail-closed. It ignores jumps and
counts only sewn segments, so it catches an obsolete connector or an object
handoff accidentally emitted as a visible float. The two heading metrics are
enforced for satin wordmarks and mixed satin work. They measure only the
interior of sustained zig-zag runs: caps, underlay handoffs, lock stitches, and
bean turnarounds are deliberately excluded. This makes them sensitive to
jagged rail fitting without blaming legitimate technique transitions.

`density` is a whole-bounds average, so a sparse outline can pass it while a
single cusp is being stitched into a knot. `peak_1mm` is the spatial backstop:
it counts penetrations in 1 mm cells at all 100 tenth-millimetre grid phases,
which makes the result invariant to PES-resolution placement. The pro
files peak at 9–15; the visibly clumped 2-inch hearts file measured 25 even
though its average density was a passing 102 st/cm². The stabilized two-column
version measures 11.

The audit also prints the exact 1mm hotspot cell and the contributing stitch
block numbers. A peak failure therefore points back to a small geometry region
instead of requiring a manual all-phase scan of the PES.

`trims_per_1k` is a routing metric, and it is the one that separated our first
Paris Review build (82 trims) from the pro's (18) most starkly.
For motifs under 500 stitches, the rate uses a 500-stitch denominator floor so
one or two necessary object trims do not dominate a statistically tiny design;
fragmented small designs still exceed the ceiling.

## The two techniques that closed the routing gap

**Route glyph by glyph.** A global nearest-neighbour walk hops to whatever piece
is closest in space, which is regularly a different letter across a gap — and
travel across a gap can't replace a trim. Label the connected ink components and
finish one before starting the next. On the Paris Review masthead
`ndimage.label(ink)` finds exactly 17 components for 17 glyphs, which is also
exactly how many trim-separated runs the pro file has.

**Underpath the travel.** When the straight line between two pieces leaves the
ink, don't trim — route a least-cost path *through* the ink with
`skimage.graph.route_through_array` over a cost surface of `1 + 6/(edt+1)` inside
and `1e4` outside, so the connector hugs the medial axis where later stitching
buries it, then emit it as a running stitch with `data-trim-after="false"`.
Measured: 95% of our travel length now lies inside the artwork, against the pro's
92%.

Both are general. Any multi-glyph traced wordmark should use them.

## Coverage audit at design time

The trace scripts print a second, geometric audit before anything is stitched:

```
coverage: 98.37% of ink stitched, spill 12.03% of ink area,
          counter intrusion 5.80% of counter area
```

Tune against these numbers rather than a stitch preview — a 1 mm hole at a
junction is invisible at preview resolution. `counter intrusion` is the one to
watch when raising the width floor: fattening strokes closes the counters of
`e`, `a`, `R` and `P` before it does anything else visible.

## Three gates before publication

The geometric coverage score cannot detect every interpretation error, so the
production path now adds three independent gates:

1. **SVG preflight** validates rail direction, explicit interior rungs, declared
   rung counts, and routed connector endpoints before Ink/Stitch runs.
2. **Compiled-object QA** stitches annotated critical objects in isolation and
   checks their real machine-space bounds and stitch count. This catches a core
   that looks closed in SVG but collapses after Ink/Stitch parses its topology.
3. **Atomic bundle verification** creates PES, DST, both previews, the tuned
   SVG, and optional reference overlay from one staged build. A manifest records
   all checksums plus the source checksum and is promoted last. `stitch verify`
   and `stitch deploy` reject a stale, tampered, or partially published bundle.

The reference overlay is advisory because satin is intentionally wider than a
hairline source image. It is still valuable for component count, clipping,
missing spokes, terminal shape, and gross silhouette drift. Fabric remains the
final authority: the manifest and preview prove pipeline consistency, not
thread tension, stabilization, or cap distortion.

## Where the machinery lives

Everything validated here is in `scripts/trace_lib.py` (`TraceConfig` +
`WordmarkTracer`): skeleton→edges with the ordered cleanup passes, width
split, variable-width rails, per-glyph routing, underpath connectors, trim
policy, coverage audit, as-sewn/debug renders. A new wordmark job is a thin
driver — `scripts/trace_paris_review.py` is the reference, ~50 lines of source
path + sizes + thresholds. `scripts/qa_render.py` renders any PES at high
px/mm with the source ink underneath (coverage checks; for *edge* judgement
use the tracer's `--finish-png` — the needle-path render scallops column edges
and reads jagged even when the rails are straight).

Closed outline loops are rebuilt from aligned inner/outer rails, widened
locally to the 1.05mm satin floor, and cyclically rotated so lock stitches land
on a straight run instead of a cusp. At branch junctions, terminal extension
is capped at 0.15mm rather than crossing the full stroke width. All raster
recipes share topology-safe diagonal adjacency, so a right-angle raster corner
cannot become a false three-edge cycle.

The character/sketch pipeline with potrace fills (the Makeout rat) is NOT in
trace_lib yet — that path still lives in `scripts/trace_lockup.py` per
docs/SKETCH-LOCKUP-RECIPE.md. It now shares the topology helper, converts its
sub-1mm text tier to bean, declares the mixed profile, and is protected by the
same strict post-stitch gate.

## Known limits

**A design has a minimum viable size, and some legacy recipes still need
design-specific junction work.** The current Paris Review rebuild at 127mm
drops from the legacy 28-penetration peak to 17 after bounded junction overlap,
but still fails the ≤15 hotspot ceiling and remains blocked. At 99mm its density
also lands around 168 st/cm² regardless of width floor. Celine/Dion likewise
retains a 29-penetration junction in its geometric top line and is blocked.
Those are honest recipe failures, not reasons to weaken the universal band;
the next lever is to author fan/split junctions or simplify branch topology at
the reported hotspot coordinates.

The historical full Court Street Journal raster is not checked in. Its tracer
now requires an explicit `--src` instead of an expired temporary path. The
production hybrid scales the checked-in `designs/csj-j-only.svg` master and
strict-audits both that mixed J and the satin lettering parts; the current J
master is consequently blocked at a 19-penetration hotspot until redigitized.

**We keep more stroke contrast than a pro would.** Normalised to 101 mm the
artwork asks for ~9:1 thick-to-thin, the pro delivers 2.5:1, we now deliver
2.7:1 (was 4:1). That is a deliberate craft choice with a real cost — more
contrast is more fragile — and it is the setting to reach for first
(`--min-col-mm`) if a sew-out reads either too heavy or too spindly.

## Pro fill + satin corpus (Zenbul, Acre, CHGL napkin — 2026-09)

Three more professionally digitized files, measured with the scratch analysers
(`density.py`, `layers.py`, `satin.py`, `widths.py`). What they do, and what the
pipeline now does because of them:

| file | what it is | technique |
|---|---|---|
| Zenbul (78.8 x 99 mm, 7.5k st, 18.7 m, 9 trims) | brush ring + thin sans | ring = tatami **0.20 mm rows, 4.0 mm stitches**, staggered, one perpendicular tatami underlay at ~1.0 mm, then a **1.8 mm satin border over both fill edges** (own centre walk); the thin tail is border only. Letters = satin 2.6–3.1 mm (source 2.6 mm), one block per letter, zig-zag underlay + centre walk, 0.36 spacing. |
| Acre (76.2 x 19 mm, 2.0k st, 6.1 m, 6 trims) | bold sans + two-colour monogram | **everything satin, up to 5 mm wide** — no fill on bold letters. Zig-zag underlay peaks **~1.2 mm** apart + walk, 0.40 spacing. Width matches source (3.4 vs 3.5 mm). One block per letter; the `a` counter is a satin ring; junctions overlap (stem sews over bowl end). |
| CHGL napkin (95.4 mm square, 20.5k st, 63 m, 29 trims) | white napkin + black handwriting | napkin = tatami **0.21 mm rows, 4.0 mm**, tatami underlay at 90° (~1.6 mm), edge run at 2.5 mm, **2.9 mm satin border** on the square edge. Handwriting = **satin 1.0–1.35 mm** (source 0.72 mm, so 1.6–1.9x fattened), centre walk 1.1–1.4 mm, 0.40 spacing, one block per word. Not bean. |

Cross-file constants: satin spacing 0.36–0.40; fill 0.20 mm rows with 4.0 mm
stitches; fill underlay one perpendicular tatami at 1.0–1.6 mm; a satin border on
every fill edge; lettering satin floor ~0.95–1.0 mm; one trim per glyph or word.

Pipeline changes:

- New preset `hat-twill-fill` (`materials/presets.toml`): 0.2 mm rows, 4.0 mm
  stitches, `fill_underlay_row_spacing_mm = 1.2`, `fill_border_mm = 1.8`,
  `zigzag_underlay_spacing_mm = 1.2`. About 2x the thread of
  `hat-twill-badge-fill`; use it when a mass must read as solid cloth.
- `fill_border_mm` (preset) / `data-fill-border-mm` (per path, `0` opts out):
  `tuning.py` sews a running centre walk + a zig-zag of that width along every
  subpath of the fill outline, right after the fill, one trim per subpath.
- `fill_underlay_row_spacing_mm` (preset): fill underlay pitch; legacy presets
  keep 3x row spacing.
- `zigzag_underlay_spacing_mm` (preset) / `data-zigzag-underlay-mm` (per path):
  zig-zag underlay pitch on satin columns; legacy 2.0.
- Audit `fill` profile re-based: density 55–240 st/cm² (pro fills 96 and 225),
  peak_1mm ≤22 (pro 17–21). The pro fills fail the lettering bands, which was the
  audit, not the files.

Probe: `designs/recipes/fill-ring.svg` → `stitch from-svg --preset
hat-twill-fill` gives 0.23 mm top rows, 1.4 mm perpendicular underlay, a
1.8 mm border in two blocks (outer edge, hole), all fill bands ok.
