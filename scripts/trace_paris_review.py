"""THE PARIS REVIEW hat wordmark — reference driver for scripts/trace_lib.py.

High-contrast serif display type (roughened Bodoni-ish). All the technique
lives in the library; this file is just the source artwork, the size, and the
tuned thresholds for THIS artwork. The shared defaults enforce the validated
v3 floors and strict quality gate; artwork-specific junction hotspots remain a
hard build failure until repaired rather than being waived.

  uv run --with numpy --with scipy --with scikit-image --with pillow \
      --with potracer \
      python scripts/trace_paris_review.py [--target-w-mm 127]

(--with potracer is only needed for --satin-mode outline.)

Pipeline:  this script -> designs/paris-review-<size>.svg
           -> stitch from-svg --preset hat-twill-black-detail  (strict audit)
           -> stitch preview --png
"""
import argparse
from pathlib import Path

from trace_lib import TraceConfig, WordmarkTracer

REPO = Path(__file__).resolve().parent.parent
SRC = str(REPO / "designs" / "artwork" / "paris-review-masthead.png")

ap = argparse.ArgumentParser()
ap.add_argument("--target-w-mm", type=float, default=127.0)
ap.add_argument("--out-svg", default=str(REPO / "designs" / "paris-review-5in.svg"))
ap.add_argument("--debug-png", default=str(REPO / "out" / "paris-review-debug.png"))
ap.add_argument("--finish-png", default=None,
                help="as-sewn render (filled rail polygons) for edge inspection")
ap.add_argument("--hairline-mm", type=float, default=0.45)
ap.add_argument("--min-col-mm", type=float, default=1.05,
                help="narrowest satin column; pro files floor at 1.04-1.53mm")
ap.add_argument("--trim-hop-mm", type=float, default=16.0)
ap.add_argument("--smooth-win", type=int, default=9)
ap.add_argument("--straighten-mm", type=float, default=0.0)
ap.add_argument("--work-w", type=int, default=4000)
ap.add_argument("--satin-mode", choices=["skeleton", "outline"], default="skeleton",
                help="outline = branch fills + rungs for Ink/Stitch fill_to_satin")
ap.add_argument("--overrides", default=None,
                help="sidecar JSON with add_rungs/drop_rungs/force_bean edits")
cli = ap.parse_args()

cfg = TraceConfig(
    target_w_mm=cli.target_w_mm,
    work_w=cli.work_w,
    hairline_mm=cli.hairline_mm,
    min_col_mm=cli.min_col_mm,
    trim_hop_mm=cli.trim_hop_mm,
    smooth_win=cli.smooth_win,
    straighten_mm=cli.straighten_mm,
    satin_mode=cli.satin_mode,
    overrides_json=cli.overrides,
)

tracer = WordmarkTracer(SRC, cfg).trace()
tracer.write_svg(cli.out_svg)
tracer.coverage_report()
if cli.finish_png:
    tracer.write_finish_png(cli.finish_png)
tracer.write_debug_png(cli.debug_png)
