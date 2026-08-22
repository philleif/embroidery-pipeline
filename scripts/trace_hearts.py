"""Two hand-drawn outline hearts — driver for scripts/trace_lib.py.

A brush/marker drawing of a big heart and a small heart, both closed outline
loops of roughly even weight (~2.1mm of ink at 2in wide). No hairlines and no
fills: every run should come out a satin column, so the job is really about
smoothing the marker deckle without losing the hand-drawn wobble, and about
the two cusps per heart (top cleft, bottom point) where the medial axis
branches.

  uv run --with numpy --with scipy --with scikit-image --with pillow \
      python scripts/trace_hearts.py [--target-w-mm 50.8]

Pipeline:  this script -> designs/hearts-2in.svg
           -> stitch from-svg --preset hat-twill-black-detail  (strict audit)
           -> stitch preview --png
"""
import argparse
from pathlib import Path

from trace_lib import TraceConfig, WordmarkTracer

REPO = Path(__file__).resolve().parent.parent
SRC = str(REPO / "designs" / "artwork" / "hearts.png")

ap = argparse.ArgumentParser()
ap.add_argument("--target-w-mm", type=float, default=50.8)    # 2 in
ap.add_argument("--out-svg", default=str(REPO / "designs" / "hearts-2in.svg"))
ap.add_argument("--debug-png", default=str(REPO / "out" / "hearts-debug.png"))
ap.add_argument("--finish-png", default=str(REPO / "out" / "hearts-finish.png"),
                help="as-sewn render (filled rail polygons) for edge inspection")
ap.add_argument("--hairline-mm", type=float, default=0.45)
ap.add_argument("--min-col-mm", type=float, default=1.05)
ap.add_argument("--trim-hop-mm", type=float, default=16.0)
ap.add_argument("--smooth-win", type=int, default=13,
                help="wider than the type jobs: marker deckle on a long smooth "
                     "curve, not letterform detail to preserve")
ap.add_argument("--straighten-mm", type=float, default=0.0)
ap.add_argument("--rail-snap-mm", type=float, default=0.8,
                help="the marker blobs at both cusps of each heart, so the ink "
                     "there is wider than the EDT column; snapping the rails "
                     "out to the true edge took coverage 97.7 -> 99.4%. 1.3 "
                     "adds no coverage and only more spill")
ap.add_argument("--work-w", type=int, default=2300,
                help="~35 px/mm at 2in; the source is only 594px so this is an "
                     "upscale, and LANCZOS+threshold is what smooths the edge")
ap.add_argument("--satin-mode", default="skeleton", choices=["skeleton", "outline"])
cli = ap.parse_args()

cfg = TraceConfig(
    target_w_mm=cli.target_w_mm,
    work_w=cli.work_w,
    hairline_mm=cli.hairline_mm,
    min_col_mm=cli.min_col_mm,
    trim_hop_mm=cli.trim_hop_mm,
    audit_profile="satin-outline",
    smooth_win=cli.smooth_win,
    straighten_mm=cli.straighten_mm,
    rail_snap_mm=cli.rail_snap_mm,
    satin_mode=cli.satin_mode,
)

tracer = WordmarkTracer(SRC, cfg).trace()
tracer.write_svg(cli.out_svg)
tracer.coverage_report()
if cli.finish_png:
    tracer.write_finish_png(cli.finish_png)
tracer.write_debug_png(cli.debug_png)
