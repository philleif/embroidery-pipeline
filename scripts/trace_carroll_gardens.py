"""CARROLL GARDENS / LIBRARY INSPECTOR hat wordmark — driver for trace_lib.

Two registers, both satin, no fill and no bean:

  CARROLL GARDENS   geometric sans caps, wide tracked, 7.0 mm cap height,
                    drawn ink 0.91 mm p50 — under the 1.05 mm sewn floor, so
                    every column is floored and the line sews ~15% bolder
                    than drawn (the pro CHGL napkin fattens handwriting
                    1.6-1.9x, so this is mild).
  LIBRARY INSPECTOR heavy condensed grotesque, 8.6 mm cap height, drawn ink
                    p50 2.58 / p90 2.98 / max 3.60 mm — squarely satin, well
                    inside the pro Acre file's 5 mm ceiling.

The source is a true vector SVG, so the tracer's raster is rendered from it
at ~39 px/mm rather than upscaled from a screenshot. There is no letterpress
deckle to smooth away, so outline_smooth_mm is dropped below the beach-baby
value to keep the geometric corners square.

  source SVG -> (inkscape render) designs/artwork/*.png
             -> this script      -> designs/carroll-gardens-4.5in-outline.svg
             -> stitch from-svg --preset hat-twill-satin   (strict audit)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from trace_lib import TraceConfig, WordmarkTracer


REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "designs" / "artwork" / "carroll-gardens-library-inspector.png"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-w-mm", type=float, default=114.3)   # 4.5 in
    ap.add_argument("--work-w", type=int, default=4500,           # ~39 px/mm
                    help="track the target so pixel thresholds keep their "
                         "meaning in mm")
    ap.add_argument("--out-svg", default=str(
        REPO / "designs" / "carroll-gardens-4.5in-outline.svg"))
    ap.add_argument("--debug-png", default=str(
        REPO / "out" / "carroll-gardens-4.5in-debug.png"))
    ap.add_argument("--finish-png", default=str(
        REPO / "out" / "carroll-gardens-4.5in-finish.png"))
    ap.add_argument("--hairline-mm", type=float, default=0.40,
                    help="nothing in this artwork measures below 0.56 mm, so "
                         "no stroke should fall out to bean")
    ap.add_argument("--min-col-mm", type=float, default=1.05)
    ap.add_argument("--max-col-mm", type=float, default=4.2)
    ap.add_argument("--rail-snap-mm", type=float, default=1.0)
    ap.add_argument("--outline-smooth-mm", type=float, default=0.15)
    ap.add_argument("--branch-overlap-mm", type=float, default=0.15)
    ap.add_argument("--width-gain", type=float, default=1.06,
                    help="rail chord-error allowance; it comes straight out of "
                         "the condensed line's 0.93-1.36mm counters")
    ap.add_argument("--ring-core-min-frac", type=float, default=0.95,
                    help="a component whose leaf-pruned skeleton is this much "
                         "2-core is rebuilt as ONE ring column from its outer "
                         "and counter boundaries. The library default of 0.80 "
                         "admits the P of INSPECTOR at 86.5%%: its stem is not "
                         "part of any ring, so the column spans outer edge to "
                         "counter and sews the counter shut.")
    ap.add_argument("--corner-split-deg", type=float, default=40.0,
                    help="cut a branch wherever its centerline turns this "
                         "sharply, so the A apex, the N corners and the R leg "
                         "sew as straight columns meeting in a mitre instead "
                         "of one bent column fanning round the inside corner. "
                         "That fan is what made the first sew-out choppy.")
    ap.add_argument("--concave-split-deg", type=float, default=25.0,
                    help="cut a curving branch where the ink has a concave "
                         "corner (the S's angular counter ends), so the "
                         "stitches stop pivoting on that corner")
    ap.add_argument("--straight-rects", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="sew every straight stroke as a constant-width "
                         "rectangle instead of its nearest-skeleton wedge "
                         "(the N diagonal was fanning into both counter corners)")
    ap.add_argument("--merge-dot", type=float, default=0.55)
    ap.add_argument("--rung-max-gap-mm", type=float, default=4.0)
    ap.add_argument("--rung-turn-deg", type=float, default=30.0)
    ap.add_argument("--trim-hop-mm", type=float, default=14.0)
    ap.add_argument("--satin-mode", choices=["skeleton", "outline"],
                    default="outline")
    args = ap.parse_args()

    cfg = TraceConfig(
        target_w_mm=args.target_w_mm,
        work_w=args.work_w,
        hairline_mm=args.hairline_mm,
        min_col_mm=args.min_col_mm,
        max_w_mm=args.max_col_mm,
        trim_hop_mm=args.trim_hop_mm,
        smooth_win=11,
        rail_snap_mm=args.rail_snap_mm,
        merge_dot=args.merge_dot,
        corner_split_deg=args.corner_split_deg,
        concave_split_deg=args.concave_split_deg,
        straight_rect_cols=args.straight_rects,
        ring_core_min_frac=args.ring_core_min_frac,
        rung_max_gap_mm=args.rung_max_gap_mm,
        rung_turn_deg=args.rung_turn_deg,
        satin_mode=args.satin_mode,
        outline_smooth_mm=args.outline_smooth_mm,
        branch_overlap_mm=args.branch_overlap_mm,
        width_gain=args.width_gain,
        audit_profile="satin-wordmark",
    )

    tracer = WordmarkTracer(str(SRC), cfg).trace()
    tracer.write_svg(args.out_svg)
    tracer.coverage_report()
    tracer.write_finish_png(args.finish_png)
    tracer.write_debug_png(args.debug_png)


if __name__ == "__main__":
    main()
