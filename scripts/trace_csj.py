"""COURT STREET JOURNAL wordmark trace — per-job copy of the v3 lockup recipe.

All-lettering variant of scripts/trace_lockup.py (see docs/SKETCH-LOCKUP-RECIPE.md):
no width split, no zone classifiers — every skeleton edge becomes a satin
centerline (data-stitch-method="satin", converted by the stroke_to_satin
pre-pass). Sizing is WIDTH-driven here (the lockup is one long line, ~10.8:1):
TARGET_W_MM sets the ink width and height follows the bbox aspect.

Run under uv (the project venv has no numpy/scipy/skimage):
  uv run --with numpy --with scipy --with scikit-image --with pillow \
      python scripts/trace_csj.py

Pipeline:  this script -> designs/court-street-journal.svg
           -> stitch_cli.hershey.smooth_svg_paths (corner-preserving spline)
           -> stitch from-svg --preset hat-twill-black-detail -> strict audit
           -> out/*.pes
           -> stitch preview --png

The historical raster is not checked in. Supply it with `--src`; the current
hybrid production recipe uses the stable checked-in `csj-j-only.svg` instead.
"""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from skimage.morphology import skeletonize, remove_small_objects

from trace_topology import topology_neighbors

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SRC = REPO / "designs" / "artwork" / "court-street-journal.png"
OUT_SVG = str(REPO / "designs" / "court-street-journal.svg")
DEBUG_PNG = str(REPO / "out" / "court-street-journal-debug.png")

# --crop keeps source-px coordinates intact so the J-zone classifier still
# applies — used to trace the script J alone for the hybrid (font sans) build
ap = argparse.ArgumentParser()
ap.add_argument("--src", default=str(DEFAULT_SRC))
ap.add_argument("--crop", nargs=2, type=int, metavar=("X0", "X1"))
ap.add_argument("--target-w-mm", type=float, default=98.5)
ap.add_argument("--out-svg", default=OUT_SVG)
ap.add_argument("--debug-png", default=DEBUG_PNG)
cli = ap.parse_args()
SRC = cli.src
OUT_SVG = cli.out_svg
DEBUG_PNG = cli.debug_png

if not Path(SRC).is_file():
    raise FileNotFoundError(
        f"source raster not found: {SRC}; supply the historical art with --src, "
        "or build the checked-in SVG directly"
    )

TARGET_W_MM = cli.target_w_mm  # centerline ink width; satin overhang adds ~1.3mm
MIN_SPECK_PX = 60    # strokes are only ~14px wide at this res; keep the bar low
PRUNE_PX = 16        # kill corner spurs (~halfwidth*1.4 ≈ 10px) but keep E-arms etc.
RDP_EPS_PX = 1.5
MARGIN_MM = 1.5

# ---------------------------------------------------------------- load & mask
img = Image.open(SRC).convert("LA")
arr = np.array(img)
gray = arr[..., 0].astype(float)
alpha = arr[..., 1].astype(float) / 255.0
gray = gray * alpha + 255 * (1 - alpha)
ink = gray < 128
ink = remove_small_objects(ink, MIN_SPECK_PX)
if cli.crop:
    ink[:, : cli.crop[0]] = False
    ink[:, cli.crop[1]:] = False

ys, xs = np.where(ink)
x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
bbox_w = x1 - x0 + 1
MM_PER_PX = TARGET_W_MM / bbox_w
print(f"bbox {bbox_w}x{y1-y0+1}px  scale {MM_PER_PX:.4f} mm/px  "
      f"design {TARGET_W_MM:.1f} x {(y1-y0+1)*MM_PER_PX:.1f} mm")

def to_mm(x, y):
    return ((x - x0) * MM_PER_PX + MARGIN_MM, (y - y0) * MM_PER_PX + MARGIN_MM)

edt = ndimage.distance_transform_edt(ink)

# --------------------------------------------------------- linework skeleton
skel = skeletonize(ink)
coords = set(zip(*np.where(skel)))
def neighbors(p):
    return topology_neighbors(p, coords)

def extract_edges(coords):
    deg = {p: len(neighbors(p)) for p in coords}
    nodes = {p for p, d in deg.items() if d != 2}
    visited_edges = set()
    edges = []
    for node in nodes:
        for nb in neighbors(node):
            key = (node, nb)
            if key in visited_edges:
                continue
            path = [node, nb]
            visited_edges.add((node, nb))
            visited_edges.add((nb, node))
            prev, cur = node, nb
            while cur not in nodes:
                nxt = [q for q in neighbors(cur) if q != prev]
                if not nxt:
                    break
                nxt = nxt[0]
                visited_edges.add((cur, nxt))
                visited_edges.add((nxt, cur))
                path.append(nxt)
                prev, cur = cur, nxt
            edges.append(path)
    in_edge = {p for e in edges for p in e}
    leftover = coords - in_edge
    while leftover:
        start = next(iter(leftover))
        loop = [start]
        leftover.discard(start)
        prev, cur = None, start
        while True:
            nxt = [q for q in neighbors(cur) if q != prev and q in leftover]
            if not nxt:
                break
            cur, prev = nxt[0], cur
            loop.append(cur)
            leftover.discard(cur)
        loop.append(start)
        edges.append(loop)
    return edges, nodes

edges, nodes = extract_edges(coords)
print(f"raw skeleton edges: {len(edges)}")

for _ in range(2):
    deg_count = {}
    for e in edges:
        for end in (e[0], e[-1]):
            deg_count[end] = deg_count.get(end, 0) + 1
    kept = []
    for e in edges:
        is_spur = (deg_count[e[0]] == 1 or deg_count[e[-1]] == 1) and len(e) < PRUNE_PX
        if not is_spur:
            kept.append(e)
    edges = kept
print(f"after prune: {len(edges)}")

def end_dir(e, at_start):
    k = min(8, len(e) - 1)
    if at_start:
        v = (e[k][0] - e[0][0], e[k][1] - e[0][1])
    else:
        v = (e[-1 - k][0] - e[-1][0], e[-1 - k][1] - e[-1][1])
    nn = max(1e-9, (v[0] ** 2 + v[1] ** 2) ** 0.5)
    return (v[0] / nn, v[1] / nn)

changed = True
while changed:
    changed = False
    endpoint_map = {}
    for idx, e in enumerate(edges):
        for at_start in (True, False):
            endpoint_map.setdefault(e[0] if at_start else e[-1], []).append((idx, at_start))
    for pt, ends in endpoint_map.items():
        if len(ends) < 2:
            continue
        best, best_dot = None, -0.3
        for a in range(len(ends)):
            for b in range(a + 1, len(ends)):
                ia, sa = ends[a]
                ib, sb = ends[b]
                if ia == ib:
                    continue
                da = end_dir(edges[ia], sa)
                db = end_dir(edges[ib], sb)
                dot = -(da[0] * db[0] + da[1] * db[1])
                if dot > best_dot:
                    best_dot, best = dot, (ia, sa, ib, sb)
        if best:
            ia, sa, ib, sb = best
            ea = edges[ia] if not sa else edges[ia][::-1]
            eb = edges[ib] if sb else edges[ib][::-1]
            merged = ea + eb[1:]
            new_edges = [e for k, e in enumerate(edges) if k not in (ia, ib)]
            new_edges.append(merged)
            edges = new_edges
            changed = True
            break
print(f"after merge: {len(edges)}")

def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    a, b = np.array(pts[0], float), np.array(pts[-1], float)
    ab = b - a
    lab = np.linalg.norm(ab)
    if lab < 1e-9:
        d = [np.linalg.norm(np.array(p, float) - a) for p in pts[1:-1]]
    else:
        d = [abs(np.cross(ab, np.array(p, float) - a)) / lab for p in pts[1:-1]]
    if not d:
        return [pts[0], pts[-1]]
    imax = int(np.argmax(d))
    if d[imax] > eps:
        left = rdp(pts[: imax + 2], eps)
        right = rdp(pts[imax + 1:], eps)
        return left[:-1] + right
    return [pts[0], pts[-1]]

def is_script_j(cx):
    # the calligraphic J of "Journal" (source-px x-range) — mixed technique:
    # thick strokes stay satin, hairline swashes become bean centerlines so the
    # spiral detail survives instead of merging into a satin blob
    return 780 < cx < 1000

HAIRLINE_MM = 1.05    # below the proven satin floor → bean, not micro-satin
MIN_RUN_PX = 15       # shorter thick/thin runs merge into their neighbor
OVERLAP_PX = 3        # extend each piece into its neighbor so the join closes

def split_by_width(e):
    """Split a J-zone skeleton edge into (pixel-run, is_hairline, taper_start,
    taper_end) pieces by local stroke width, smoothed + de-flickered. A satin
    run's taper flags mark ends that abut a hairline run inside the same edge —
    those ends get a width taper so the column blends into the bean line."""
    wloc = np.array([2 * edt[p] * MM_PER_PX for p in e])
    k = 9
    if len(wloc) > k:
        pad = k // 2
        wloc = np.convolve(np.pad(wloc, pad, mode="edge"),
                           np.ones(k) / k, mode="valid")
    thin_flags = wloc < HAIRLINE_MM
    # merge runs shorter than MIN_RUN_PX into the surrounding classification
    runs = []
    s = 0
    for i in range(1, len(e) + 1):
        if i == len(e) or thin_flags[i] != thin_flags[s]:
            runs.append([s, i, bool(thin_flags[s])])
            s = i
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i, (rs, re, fl) in enumerate(runs):
            if re - rs < MIN_RUN_PX:
                j = i - 1 if i > 0 else i + 1
                runs[j][2] = runs[j][2] if (re - rs) < (runs[j][1] - runs[j][0]) else fl
                runs[j][0] = min(runs[j][0], rs)
                runs[j][1] = max(runs[j][1], re)
                runs.pop(i)
                changed = True
                break
    return [(e[max(0, rs - OVERLAP_PX):min(len(e), re + OVERLAP_PX)], fl,
             i > 0 and runs[i - 1][2],           # thin run before → taper start
             i < len(runs) - 1 and runs[i + 1][2])  # thin run after → taper end
            for i, (rs, re, fl) in enumerate(runs)]

SAMPLE_PX = 5          # column centerline resample pitch (~0.37mm)
TAPER_MM = 2.0         # length of the blend where satin meets a bean hairline
TIP_W_MM = 1.05        # satin side of a bean transition stays width-safe

def tapered_column(piece, taper_start, taper_end):
    """Variable-width satin column from a skeleton pixel run: centerline
    resampled + smoothed, rail offset = local EDT ink width (tapered into the
    hairline at flagged ends). Returns (rail1, rail2) point lists in mm."""
    pts = np.array(piece, float)                       # (y, x)
    seg = np.sqrt(((pts[1:] - pts[:-1]) ** 2).sum(1))
    s = np.concatenate([[0], np.cumsum(seg)])
    n = max(6, int(s[-1] / SAMPLE_PX) + 1)
    si = np.linspace(0, s[-1], n)
    ys = np.interp(si, s, pts[:, 0])
    xs = np.interp(si, s, pts[:, 1])
    k = 5
    if n > k:                                          # light centerline smooth
        pad = k // 2
        ys = np.convolve(np.pad(ys, pad, mode="edge"), np.ones(k) / k, "valid")
        xs = np.convolve(np.pad(xs, pad, mode="edge"), np.ones(k) / k, "valid")
    iy = np.clip(np.round(ys).astype(int), 0, edt.shape[0] - 1)
    ix = np.clip(np.round(xs).astype(int), 0, edt.shape[1] - 1)
    w = 2 * edt[iy, ix] * MM_PER_PX
    if n > 7:
        pad = 3
        w = np.convolve(np.pad(w, pad, mode="edge"), np.ones(7) / 7, "valid")
    w = np.clip(w * 1.15, TIP_W_MM, 2.0)
    step_mm = (s[-1] / (n - 1)) * MM_PER_PX
    nt = min(n, max(2, int(TAPER_MM / step_mm)))
    if taper_start:
        r = np.linspace(0.0, 1.0, nt)
        w[:nt] = TIP_W_MM + (w[:nt] - TIP_W_MM) * r
    if taper_end:
        r = np.linspace(1.0, 0.0, nt)
        w[-nt:] = TIP_W_MM + (w[-nt:] - TIP_W_MM) * r
    ty = np.gradient(ys)
    tx = np.gradient(xs)
    norm = np.maximum(1e-9, np.sqrt(ty ** 2 + tx ** 2))
    nyv, nxv = -tx / norm, ty / norm                   # unit normal (y,x)
    h_px = (w / 2) / MM_PER_PX
    rails = []
    for sign in (1, -1):
        ry = ys + sign * nyv * h_px
        rx = xs + sign * nxv * h_px
        rails.append([to_mm(x, y) for x, y in zip(rx, ry)])
    return rails

def catmull_bezier_d(pts):
    """Catmull-Rom spline through pts as an absolute cubic-bezier path chunk.
    Curve commands keep the downstream polyline smoothing pass off this path."""
    P = [pts[0]] + list(pts) + [pts[-1]]
    d = [f"M {pts[0][0]:.2f} {pts[0][1]:.2f}"]
    for i in range(1, len(P) - 2):
        c1 = (P[i][0] + (P[i + 1][0] - P[i - 1][0]) / 6,
              P[i][1] + (P[i + 1][1] - P[i - 1][1]) / 6)
        c2 = (P[i + 1][0] - (P[i + 2][0] - P[i][0]) / 6,
              P[i + 1][1] - (P[i + 2][1] - P[i][1]) / 6)
        d.append(f"C {c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} "
                 f"{P[i + 1][0]:.2f} {P[i + 1][1]:.2f}")
    return " ".join(d)

polylines = []   # (rdp_pts, width_mm, method, extra)
n_hair = n_col = 0
for e in edges:
    if len(e) < 4:
        continue
    cx = sum(p[1] for p in e) / len(e)
    if is_script_j(cx):
        pieces = split_by_width(e)
    else:
        pieces = [(e, False, False, False)]
    for piece, hairline, tstart, tend in pieces:
        if len(piece) < 4:
            continue
        pts = rdp(piece, RDP_EPS_PX)
        w = 2 * np.mean([edt[p] for p in piece]) * MM_PER_PX
        if hairline:
            method, extra = "bean", None
            n_hair += 1
        elif is_script_j(cx):
            # J thick strokes → true variable-width satin column that follows
            # the calligraphic taper and blends into the bean hairlines
            method, extra = "column", tapered_column(piece, tstart, tend)
            n_col += 1
        else:
            method, extra = "satin", None
        polylines.append((pts, w, method, extra))
print(f"final polylines: {len(polylines)} ({n_hair} bean, {n_col} tapered column)")

# ------------------------------------------------- sans regularization
# The traced sans reads jagged: per-piece widths differ, skeleton centerlines
# wobble, and stem ends land at slightly different heights. The source font is
# geometric — uniform stroke, flat cap line and baseline — so impose that:
# one global satin width, points near the cap/base lines snapped exactly onto
# them, near-vertical segments made vertical, near-horizontal made horizontal.
CAP_ROW, BASE_ROW = 75, 155         # sans cap top / baseline (source px)
SNAP_BAND_PX = 4
ORTHO_SIN = 0.14                    # ~8 degrees

sans_ws = [w for _, w, m, _ in polylines if m == "satin"]
if sans_ws:
    W_UNI = float(np.median(sans_ws))
    SW_UNI = min(1.9, max(1.05, W_UNI * 1.05))
    hw_px = (SW_UNI / 2) / MM_PER_PX
    top_row, bot_row = CAP_ROW + hw_px, BASE_ROW - hw_px

    def regularize(pts):
        pts = [list(p) for p in pts]            # (y, x)
        for p in pts:
            if abs(p[0] - top_row) <= SNAP_BAND_PX:
                p[0] = top_row
            elif abs(p[0] - bot_row) <= SNAP_BAND_PX:
                p[0] = bot_row
        for i in range(len(pts) - 1):
            dy = pts[i + 1][0] - pts[i][0]
            dx = pts[i + 1][1] - pts[i][1]
            L = (dx * dx + dy * dy) ** 0.5
            if L < 10:
                continue
            if abs(dx) <= L * ORTHO_SIN:        # near-vertical stem
                mx = (pts[i][1] + pts[i + 1][1]) / 2
                pts[i][1] = pts[i + 1][1] = mx
            elif abs(dy) <= L * ORTHO_SIN:      # near-horizontal bar
                my = (pts[i][0] + pts[i + 1][0]) / 2
                pts[i][0] = pts[i + 1][0] = my
        return [tuple(p) for p in pts]

    def fix_ends(pts):
        """stroke_to_satin cuts the column flat at the centerline endpoint (no
        cap extension), so free stroke ends render half a satin width short of
        the cap/base lines while curve apexes reach them — the baseline jumps.
        Move each free end along its direction so the perpendicular end edge
        lands exactly on CAP_ROW / BASE_ROW (angle-compensated: a diagonal
        end's low corner otherwise dips below the line). Horizontal bar ends
        get plain half-width extension to cover the ink."""
        pts = [list(p) for p in pts]
        for i, j in ((0, 1), (len(pts) - 1, len(pts) - 2)):
            dy = pts[i][0] - pts[j][0]
            dx = pts[i][1] - pts[j][1]
            L = max(1e-9, (dx * dx + dy * dy) ** 0.5)
            dy, dx = dy / L, dx / L                    # outward end direction
            y = pts[i][0]
            if abs(dy) > 0.35 and abs(y - top_row) <= SNAP_BAND_PX + 2:
                ty = CAP_ROW + hw_px * abs(dx)         # end-edge top on cap line
            elif abs(dy) > 0.35 and abs(y - bot_row) <= SNAP_BAND_PX + 2:
                ty = BASE_ROW - hw_px * abs(dx)        # end-edge bottom on baseline
            elif abs(dy) <= 0.35:
                pts[i][0] += dy * hw_px                # horizontal bar end: plain
                pts[i][1] += dx * hw_px                # half-width extension
                continue
            else:
                continue
            t = max(-3 * hw_px, min(3 * hw_px, (ty - y) / dy))
            pts[i][0] += dy * t
            pts[i][1] += dx * t
        return [tuple(p) for p in pts]

    polylines = [
        (fix_ends(regularize(pts)), W_UNI, m, ex) if m == "satin" else (pts, w, m, ex)
        for pts, w, m, ex in polylines
    ]
    print(f"sans regularized: uniform satin {SW_UNI:.2f}mm, "
          f"cap/base snap rows {top_row:.1f}/{bot_row:.1f}")

# ------------------------------------------------------------- stitch order
ordered = []
remaining = list(polylines)
cur = (y0, x0)
while remaining:
    best_i, best_rev, best_d = 0, False, 1e18
    for i, (pts, w, m, ex) in enumerate(remaining):
        for rev in (False, True):
            p = pts[-1] if rev else pts[0]
            d = (p[0] - cur[0]) ** 2 + (p[1] - cur[1]) ** 2
            if d < best_d:
                best_i, best_rev, best_d = i, rev, d
    pts, w, m, ex = remaining.pop(best_i)
    if best_rev:
        pts = pts[::-1]
    ordered.append((pts, w, m, ex))
    cur = pts[-1]

# ----------------------------------------------------------------- write SVG
W_MM = TARGET_W_MM + 2 * MARGIN_MM
H_MM = (y1 - y0 + 1) * MM_PER_PX + 2 * MARGIN_MM
svg = []
svg.append(
    f'<svg xmlns="http://www.w3.org/2000/svg" '
    f'xmlns:inkstitch="http://inkstitch.org/namespace" '
    f'width="{W_MM:.2f}mm" height="{H_MM:.2f}mm" '
    f'viewBox="0 0 {W_MM:.2f} {H_MM:.2f}" data-audit-profile="mixed">'
)
svg.append('  <g id="lettering">')
for i, (pts, w, method, extra) in enumerate(ordered):
    mm = [to_mm(p[1], p[0]) for p in pts]
    d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in mm)
    if method == "bean":
        # hairline swash → bean centerline (triple pass for weight on twill)
        sw = min(0.9, max(0.4, w))
        svg.append(
            f'    <path id="line{i}" style="fill:none;stroke:#000000;stroke-width:{sw:.2f};'
            f'stroke-linecap:round" data-stroke-method="bean_stitch" d="{d}"/>'
        )
    elif method == "column":
        # hand-authored variable-width satin column: two bezier rails with
        # equal node counts (Ink/Stitch pairs the nodes). Bezier commands also
        # keep smooth_svg_paths off it; tuning stamps params via the marker.
        rail1, rail2 = extra
        d = catmull_bezier_d(rail1) + " " + catmull_bezier_d(rail2)
        svg.append(
            f'    <path id="line{i}" style="fill:none;stroke:#000000;stroke-width:0.26" '
            f'inkstitch:satin_column="true" d="{d}"/>'
        )
    else:
        # small-lettering satin tier — no width inflation beyond the ink
        # (tuning adds pull compensation downstream); 1.25x read too heavy
        sw = min(1.9, max(1.05, w * 1.05))
        svg.append(
            f'    <path id="line{i}" style="fill:none;stroke:#000000;stroke-width:{sw:.2f};'
            f'stroke-linecap:round" data-stitch-method="satin" d="{d}"/>'
        )
svg.append("  </g>")
svg.append("</svg>")
with open(OUT_SVG, "w") as f:
    f.write("\n".join(svg))
print(f"wrote {OUT_SVG}  ({W_MM:.1f} x {H_MM:.1f} mm, {len(ordered)} satin paths)")

# --------------------------------------------------------------- debug image
dbg = Image.fromarray(np.stack([(220 - ink * 40).astype(np.uint8)] * 3, axis=-1))
draw = ImageDraw.Draw(dbg)
for pts, w, method, extra in ordered:
    color = {"bean": (230, 30, 200), "column": (255, 140, 0)}.get(method, (30, 170, 60))
    draw.line([(p[1], p[0]) for p in pts], fill=color, width=3)
dbg.crop((max(0, x0 - 20), max(0, y0 - 20), x1 + 20, y1 + 20)).save(DEBUG_PNG)
print(f"wrote {DEBUG_PNG}")
