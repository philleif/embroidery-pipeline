"""CELINE Dion parody hat wordmark — driver for scripts/trace_lib.py.

Two registers in one lockup: "CÉLINE" in wide-tracked geometric sans caps
(uniform medium-weight strokes → satin columns throughout) over "Dion" in a
high-contrast script whose hairline entries/exits drop to bean while the
thick downstrokes stay satin. Same mixed-technique split as the Court Street
Journal J.

  uv run --with numpy --with scipy --with scikit-image --with pillow \
      python scripts/trace_celine_dion.py [--target-w-mm 114.3]

Pipeline:  this script -> designs/celine-dion-4.5in.svg
           -> stitch from-svg --preset hat-twill-black-detail  (strict audit)
           -> stitch preview --png
"""
import argparse
from pathlib import Path

from trace_lib import TraceConfig, WordmarkTracer

REPO = Path(__file__).resolve().parent.parent
SRC = str(REPO / "designs" / "artwork" / "celine-dion.png")

ap = argparse.ArgumentParser()
ap.add_argument("--target-w-mm", type=float, default=114.3)   # 4.5 in
ap.add_argument("--out-svg", default=str(REPO / "designs" / "celine-dion-4.5in.svg"))
ap.add_argument("--debug-png", default=str(REPO / "out" / "celine-dion-debug.png"))
ap.add_argument("--finish-png", default=None,
                help="as-sewn render (filled rail polygons) for edge inspection")
ap.add_argument("--hairline-mm", type=float, default=0.45)
ap.add_argument("--min-col-mm", type=float, default=1.05,
                help="narrowest satin column; pro files floor at 1.04-1.53mm")
ap.add_argument("--trim-hop-mm", type=float, default=16.0)
ap.add_argument("--smooth-win", type=int, default=9)
ap.add_argument("--straighten-mm", type=float, default=0.0)
ap.add_argument("--rail-snap-mm", type=float, default=1.3,
                help="outward rail snap to the ink edge; squares the sans "
                     "terminals and fills the N's acute-junction wedges")
ap.add_argument("--work-w", type=int, default=4000)
cli = ap.parse_args()

cfg = TraceConfig(
    target_w_mm=cli.target_w_mm,
    work_w=cli.work_w,
    hairline_mm=cli.hairline_mm,
    min_col_mm=cli.min_col_mm,
    trim_hop_mm=cli.trim_hop_mm,
    smooth_win=cli.smooth_win,
    straighten_mm=cli.straighten_mm,
    rail_snap_mm=cli.rail_snap_mm,
    audit_profile="mixed",
)

tracer = WordmarkTracer(SRC, cfg).trace()
tracer.write_svg(cli.out_svg)
tracer.coverage_report()
if cli.finish_png:
    tracer.write_finish_png(cli.finish_png)
tracer.write_debug_png(cli.debug_png)
