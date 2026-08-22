"""PTM badge — solid-mass knockout trace (no lettering pass).

The artwork is a rough stamped disc with P/T/M carved out of it in reverse.
Nothing here is a stroke: the thread is the disc, and every letter is bare
fabric showing through. So this is a pure potrace-fill job — no skeleton, no
satin, no width split. The letters come along for free as holes and bites in
the silhouette, which is also why they cost zero stitches.

  uv run --with numpy --with scipy --with scikit-image --with pillow \
      --with potracer python scripts/trace_ptm_badge.py [--target-w-mm 50.8]

Pipeline:  this script -> designs/ptm-badge-2in.svg
           -> stitch from-svg --preset hat-twill-badge-fill  (strict audit)
           -> stitch preview --png

Why the cleanup pass exists: the source is a 209px-wide screenshot, so one
source pixel is ~0.24mm at 2in. Thresholding it raw leaves a staircased edge
plus a dozen sub-2mm specks and pinholes from the stamp texture — features
below what a needle can resolve, which auto_fill turns into stray micro-rows
and extra trims. SUPERSAMPLE + SMOOTH_MM low-pass the mask to kill the
staircase while keeping the hand-cut facets that give the badge its character;
MIN_HOLE_MM2 / MIN_SPECK_MM2 drop the unresolvable bits. Do not raise
SMOOTH_MM far — at ~0.6mm the torn edge starts rounding into a plain circle.

The audit at the end is the one that matters for a knockout: `expand_mm` (pull
compensation) grows the ink outward, which narrows every letter gap by twice
that. The script reports the post-expansion gap widths so you can see the
counters stay open before anything is stitched.
"""
import argparse
from pathlib import Path

import numpy as np
import potrace
from PIL import Image
from scipy import ndimage
from skimage.morphology import skeletonize

REPO = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--src", default=str(REPO / "designs" / "artwork" / "ptm-screenshot.png"))
ap.add_argument("--target-w-mm", type=float, default=50.8)   # 2 in
ap.add_argument("--out-svg", default=str(REPO / "designs" / "ptm-badge-2in.svg"))
ap.add_argument("--debug-png", default=str(REPO / "out" / "ptm-badge-debug.png"))
ap.add_argument("--margin-mm", type=float, default=1.5)
ap.add_argument("--fill-angle", type=float, default=45.0,
                help="rows off-axis so they never run parallel to the T bar "
                     "or the M verticals, where aligned rows read as ragged")
ap.add_argument("--pull-comp-mm", type=float, default=0.2,
                help="must match the preset's expand_mm; audit-only here")
ap.add_argument("--supersample", type=int, default=4)
ap.add_argument("--smooth-mm", type=float, default=0.22)
ap.add_argument("--min-hole-mm2", type=float, default=3.0)
ap.add_argument("--min-speck-mm2", type=float, default=3.0)
cli = ap.parse_args()

# ------------------------------------------------------------------ load & mask
im = Image.open(cli.src).convert("RGBA")
im = Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im)
gray = np.asarray(im.convert("L"))
ink0 = gray < 128

ys, xs = np.nonzero(ink0)
src_w_px = xs.max() - xs.min() + 1
MM_PER_SRC_PX = cli.target_w_mm / src_w_px
print(f"source ink bbox {src_w_px} x {ys.max()-ys.min()+1} px "
      f"-> {MM_PER_SRC_PX:.4f} mm/px at {cli.target_w_mm:.1f}mm wide")

# Supersample the *grayscale* first: the screenshot's antialiasing already
# encodes sub-pixel edge position, so resampling before the threshold recovers a
# smooth contour instead of a 0.24mm staircase.
S = cli.supersample
big = np.asarray(im.convert("L").resize(
    (gray.shape[1] * S, gray.shape[0] * S), Image.LANCZOS)).astype(float)
mm_per_px = MM_PER_SRC_PX / S
ink = big < 128

# Low-pass the binary edge (gaussian + re-threshold) to shave pixel-scale
# raggedness without pulling the torn silhouette toward a circle.
sigma_px = cli.smooth_mm / mm_per_px
ink = ndimage.gaussian_filter(ink.astype(float), sigma_px) > 0.5

px_per_mm2 = 1.0 / (mm_per_px ** 2)
min_hole_px = cli.min_hole_mm2 * px_per_mm2
min_speck_px = cli.min_speck_mm2 * px_per_mm2

# drop unresolvable ink specks
lbl, n = ndimage.label(ink)
sizes = ndimage.sum(ink, lbl, range(1, n + 1))
for i, s in enumerate(sizes, start=1):
    if s < min_speck_px:
        ink[lbl == i] = False

# fill unresolvable pinholes in the stamp texture
holes = ndimage.binary_fill_holes(ink) & ~ink
hlbl, hn = ndimage.label(holes)
hsizes = ndimage.sum(holes, hlbl, range(1, hn + 1))
n_filled = 0
for i, s in enumerate(hsizes, start=1):
    if s < min_hole_px:
        ink[hlbl == i] = True
        n_filled += 1

lbl, n = ndimage.label(ink)
sizes = ndimage.sum(ink, lbl, range(1, n + 1))
order = np.argsort(sizes)[::-1]
print(f"cleanup: filled {n_filled} sub-{cli.min_hole_mm2}mm2 pinholes; "
      f"{n} ink region(s), areas mm2 "
      f"{[round(float(sizes[i]) / px_per_mm2, 1) for i in order]}")

holes = ndimage.binary_fill_holes(ink) & ~ink
hlbl, hn = ndimage.label(holes)
hsizes = ndimage.sum(holes, hlbl, range(1, hn + 1))
print(f"enclosed knockouts kept: {hn}, areas mm2 "
      f"{sorted((round(float(s) / px_per_mm2, 1) for s in hsizes), reverse=True)}")

ys, xs = np.nonzero(ink)
x0, y0 = xs.min(), ys.min()
W_INK_MM = (xs.max() - x0 + 1) * mm_per_px
H_INK_MM = (ys.max() - y0 + 1) * mm_per_px
print(f"design ink {W_INK_MM:.1f} x {H_INK_MM:.1f} mm")


def to_mm(x, y):
    return ((x - x0) * mm_per_px + cli.margin_mm,
            (y - y0) * mm_per_px + cli.margin_mm)


# ------------------------------------------------------------------- potrace
def trace_component(comp):
    """potrace one ink region -> a single SVG `d` (outer contour + its holes).

    potracer's Bitmap takes the *inverse* convention, hence `~comp`.
    """
    t = potrace.Bitmap(~comp).trace(turdsize=int(min_speck_px / 4), alphamax=1.0,
                                    opticurve=1, opttolerance=0.35)
    d_all = []
    for curve in t:
        sp = curve.start_point
        px, py = to_mm(sp.x, sp.y)
        d = [f"M {px:.2f} {py:.2f}"]
        for seg in curve.segments:
            ex, ey = to_mm(seg.end_point.x, seg.end_point.y)
            if seg.is_corner:
                cx, cy = to_mm(seg.c.x, seg.c.y)
                d.append(f"L {cx:.2f} {cy:.2f} L {ex:.2f} {ey:.2f}")
            else:
                c1x, c1y = to_mm(seg.c1.x, seg.c1.y)
                c2x, c2y = to_mm(seg.c2.x, seg.c2.y)
                d.append(f"C {c1x:.2f} {c1y:.2f} {c2x:.2f} {c2y:.2f} {ex:.2f} {ey:.2f}")
        d.append("Z")
        d_all.append(" ".join(d))
    return " ".join(d_all)


# One <path> per ink region, biggest first. Keeping the P's counter island as
# its own path is deliberate: merged into the disc path, Ink/Stitch would
# underpath between them and lay a visible travel run straight across the
# white P. A separate object trims instead.
regions = []
for i in order:
    comp = lbl == (i + 1)
    regions.append((float(sizes[i]) / px_per_mm2, trace_component(comp)))

# ------------------------------------------------------------------ write SVG
W_MM = W_INK_MM + 2 * cli.margin_mm
H_MM = H_INK_MM + 2 * cli.margin_mm
svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W_MM:.2f}mm" '
       f'height="{H_MM:.2f}mm" viewBox="0 0 {W_MM:.2f} {H_MM:.2f}" '
       f'data-audit-profile="fill">',
       '  <g id="badge">']
for i, (area_mm2, d) in enumerate(regions):
    svg.append(f'    <path id="fill{i}" style="fill:#000000;stroke:none;fill-rule:nonzero" '
               f'data-stitch-method="auto_fill" data-fill-angle="{cli.fill_angle:g}" '
               f'd="{d}"/>')
svg.append("  </g>")
svg.append("</svg>")
Path(cli.out_svg).write_text("\n".join(svg))
print(f"wrote {cli.out_svg}  ({W_MM:.1f} x {H_MM:.1f} mm, {len(regions)} fill regions)")

# ------------------------------------------------------------------ knockout audit
# The metric that decides whether this design works: after pull compensation
# expands the ink, how wide is the narrowest surviving letter gap?
expand_px = cli.pull_comp_mm / mm_per_px
grown = ndimage.binary_dilation(ink, ndimage.generate_binary_structure(2, 2),
                                iterations=max(1, int(round(expand_px))))
inside = ndimage.binary_fill_holes(ink)
for label, mask in (("as drawn", inside & ~ink), ("after pull comp", inside & ~grown)):
    if not mask.any():
        print(f"knockout gaps, {label}: NONE LEFT — letters closed up")
        continue
    edt = ndimage.distance_transform_edt(mask)
    w = 2 * edt[skeletonize(mask)] * mm_per_px
    print(f"knockout gaps, {label}: min {w.min():.2f}mm  "
          f"p5 {np.percentile(w,5):.2f}  p25 {np.percentile(w,25):.2f}  "
          f"median {np.percentile(w,50):.2f}mm")

ink_w = 2 * ndimage.distance_transform_edt(ink)[skeletonize(ink)] * mm_per_px
print(f"ink widths: p5 {np.percentile(ink_w,5):.2f}  p25 {np.percentile(ink_w,25):.2f}  "
      f"median {np.percentile(ink_w,50):.2f}  p95 {np.percentile(ink_w,95):.2f} mm")
print(f"ink area {ink.sum()/px_per_mm2/100:.1f} cm2")

# --------------------------------------------------------------------- debug
dbg = np.full(ink.shape + (3,), 255, np.uint8)
dbg[inside & ~ink] = [235, 235, 245]      # knockout space
dbg[ink] = [20, 20, 20]                   # stitched mass
dbg[grown & ~ink] = [235, 90, 90]         # what pull comp eats into the letters
Image.fromarray(dbg[max(0, y0 - 20):ys.max() + 20, max(0, x0 - 20):xs.max() + 20]).save(cli.debug_png)
print(f"wrote {cli.debug_png}")
