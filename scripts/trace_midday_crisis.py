"""MIDDAY CRISIS hand-drawn outline lettering — production trace driver.

The source is a thin black raster outline.  Each stable closed outline is
rebuilt as one continuous satin loop; the configured minimum column width
keeps the small source line from becoming fragile micro-satin at cap scale.

  uv run --with numpy --with scipy --with scikit-image --with pillow \
      python scripts/trace_midday_crisis.py

Pipeline: this script -> designs/midday-crisis-125mm.svg
          -> scripts/stack_wordmark_svg.py ... --target-content-width-mm 101.6
             (subtracts the preset's 0.2mm/edge pull compensation)
          -> stitch from-svg --preset hat-twill-black-detail (strict by default)
          -> stitch preview --png
"""

import argparse
from pathlib import Path

from trace_lib import TraceConfig, WordmarkTracer


REPO = Path(__file__).resolve().parent.parent
SRC = str(REPO / "designs" / "artwork" / "midday-crisis.png")

ap = argparse.ArgumentParser()
ap.add_argument("--target-w-mm", type=float, default=124.0)
ap.add_argument(
    "--out-svg", default=str(REPO / "designs" / "midday-crisis-125mm.svg")
)
ap.add_argument(
    "--debug-png", default=str(REPO / "out" / "midday-crisis-debug.png")
)
ap.add_argument(
    "--finish-png", default=str(REPO / "out" / "midday-crisis-finish.png")
)
ap.add_argument("--hairline-mm", type=float, default=0.35)
ap.add_argument("--min-col-mm", type=float, default=1.05)
ap.add_argument("--trim-hop-mm", type=float, default=10.0)
ap.add_argument("--smooth-win", type=int, default=11)
ap.add_argument("--work-w", type=int, default=4500)
ap.add_argument(
    "--overrides",
    default=str(REPO / "designs" / "midday-crisis.overrides.json"),
    help="sidecar JSON for design-specific satin/bean decisions",
)
cli = ap.parse_args()

cfg = TraceConfig(
    target_w_mm=cli.target_w_mm,
    work_w=cli.work_w,
    hairline_mm=cli.hairline_mm,
    min_col_mm=cli.min_col_mm,
    trim_hop_mm=cli.trim_hop_mm,
    smooth_win=cli.smooth_win,
    satin_mode="skeleton",
    audit_profile="satin-outline",
    overrides_json=cli.overrides,
)

tracer = WordmarkTracer(SRC, cfg).trace()
tracer.write_svg(cli.out_svg)
tracer.coverage_report()
tracer.write_finish_png(cli.finish_png)
tracer.write_debug_png(cli.debug_png)
