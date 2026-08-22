"""CANONICAL sketch-to-lockup trace — the locked-in "v3" recipe.

This is the reference template for turning a hand-drawn marker lockup (a
character + handwritten text) into a hat-ready design. Distilled from the
Makeout Summer iterations (see docs/SKETCH-LOCKUP-RECIPE.md). Copy it per job.

Pipeline:  this script -> designs/<name>.svg
           -> stitch_cli.hershey.smooth_svg_paths (corner-preserving spline)
           -> stitch from-svg --preset hat-twill-black-detail -> strict audit
           -> out/<name>.pes
           -> stitch preview --png

Technique (why each choice — full rationale in the recipe doc):
  * Character linework -> bean_stitch; sunglass bridge between eyes -> single
    running_stitch (thin/light).
  * Hair/hat scribble -> CONSOLIDATE its thick mask (closing+fill_holes+opening,
    keep largest) BEFORE potrace, else auto_fill fragments the holey scribble;
    then contour_fill for a clean hair-like texture. Heart eyes -> auto_fill @45.
  * Main text -> satin. Subtract the text zone from `thick` so bold digits never
    become solid fills (that closes the 6/0 counters). Big words satin
    clamp(w*1.75,1.3,2.05)mm; the small year line uses single-repeat bean stitch
    to keep counters open without sub-millimeter satin. Do NOT geometrically
    scale a small counter open — it tears the glyph.

EDIT PER DRAWING:
  1. SRC / OUT_SVG / DEBUG_PNG / basename.
  2. TARGET_H_MM  (design height; width = height * bbox_w/bbox_h; ~90mm safe,
     ~63 here -> ~99.6mm to max the 4" hoop; keep bounds < 100mm both axes).
  3. The FIVE pixel-coord zone fns (is_text/is_bridge/is_hat_region/
     is_heart_region + the inline hatzone) MUST be re-derived for every new
     drawing from the gridded DEBUG_PNG — even a small art tweak shifts them.
"""
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw
from scipy import ndimage
from skimage.morphology import skeletonize, binary_dilation, disk, remove_small_objects
import potrace

from trace_topology import topology_neighbors

SRC = "/Users/philleif/Downloads/Frame 3 - v2.png"
REPO = Path(__file__).resolve().parent.parent
OUT_SVG = str(REPO / "designs" / "makeout-summer-lockup-v3-4in.svg")
DEBUG_PNG = str(REPO / "out" / "makeout-summer-lockup-v3-debug.png")


def is_heart_region(cx, cy):
    """The two solid heart eyes (adjusted-art positions) — v3 gives them a 45°
    fill angle for a diagonal sheen. Right heart sits high, near the hat."""
    return 1070 < cy < 1340 and 250 < cx < 820


def is_text(cy, cx):
    """Classify (centroid in original px, Frame 3 layout) as text vs rat.
    MAKEOUT top band, SUMMER mid-right, 2026 lower-right — all above the rat."""
    if cy < 470:                        # MAKEOUT + SUMMER tops
        return True
    if cy < 680 and cx < 480:           # MAKEOUT 'M'/'A' left dip
        return True
    if 280 < cy < 820 and cx > 720:     # SUMMER
        return True
    if 780 < cy < 1120 and cx > 1000:   # 2026
        return True
    return False


def is_bridge(cy, cx):
    """The sunglass bridge arc between the two heart eyes — render it as a
    single running stitch (thinner/lighter) instead of triple bean."""
    return 1110 < cy < 1200 and 500 < cx < 660


def is_hat_region(cy, cx):
    """The scribble hat/hair mass on top of the head. Its raw thick mask is
    holey + tendrilly, which auto_fill fragments; consolidate it to a clean
    solid blob before potrace. Kept above the hearts (cy<1060)."""
    return 840 < cy < 1060 and 180 < cx < 950

TARGET_H_MM = 63.0          # -> ~99.6mm wide (full 4" hoop, maxed)
THICK_HALFWIDTH_PX = 25     # EDT >= this => solid-ink core (~1.8mm drawn)
SUPPORT_HALFWIDTH_PX = 16   # hysteresis floor connected to a core stays fill
MIN_FILL_AREA_PX = 6000     # solid blobs smaller than this go back to linework
MIN_SPECK_PX = 150          # drop ink specks entirely
PRUNE_PX = 32               # kill skeleton spurs shorter than this
RDP_EPS_PX = 2.0
MARGIN_MM = 1.5

# ---------------------------------------------------------------- load & mask
img = Image.open(SRC).convert("LA")
arr = np.array(img)
gray = arr[..., 0].astype(float)
alpha = arr[..., 1].astype(float) / 255.0
gray = gray * alpha + 255 * (1 - alpha)
ink = gray < 128
ink = remove_small_objects(ink, MIN_SPECK_PX)

ys, xs = np.where(ink)
x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
bbox_h = y1 - y0 + 1
MM_PER_PX = TARGET_H_MM / bbox_h
print(f"bbox {x1-x0+1}x{bbox_h}px  scale {MM_PER_PX:.4f} mm/px  "
      f"design {(x1-x0+1)*MM_PER_PX:.1f} x {TARGET_H_MM:.1f} mm")

def to_mm(x, y):
    return ((x - x0) * MM_PER_PX + MARGIN_MM, (y - y0) * MM_PER_PX + MARGIN_MM)

# ------------------------------------------------------------- width split
edt = ndimage.distance_transform_edt(ink)
core = edt >= THICK_HALFWIDTH_PX
support = edt >= SUPPORT_HALFWIDTH_PX
lbl, n = ndimage.label(support)
core_labels = set(np.unique(lbl[core])) - {0}
selected = np.isin(lbl, list(core_labels))
thick = binary_dilation(selected, disk(SUPPORT_HALFWIDTH_PX + 1)) & ink
lbl, n = ndimage.label(thick)
sizes = ndimage.sum(thick, lbl, range(1, n + 1))
for i, s in enumerate(sizes, start=1):
    if s < MIN_FILL_AREA_PX:
        thick[lbl == i] = False
holes = ndimage.binary_fill_holes(thick) & ~thick
hlbl, hn = ndimage.label(holes)
hsizes = ndimage.sum(holes, hlbl, range(1, hn + 1))
for i, s in enumerate(hsizes, start=1):
    if s < 400:
        thick[hlbl == i] = True

# Force text out of the fill layer: the bold 2026 digits are wide enough to be
# classified thick, which turns the "6"/"2" into solid blobs whose counters
# fill in. Subtract the text zone from `thick` so all lettering stays a satin
# centerline with an open counter.
YY, XX = np.mgrid[0:ink.shape[0], 0:ink.shape[1]]
textzone = np.zeros_like(ink)
textzone |= (YY < 470)
textzone |= (YY < 680) & (XX < 480)
textzone |= (YY > 280) & (YY < 820) & (XX > 720)
textzone |= (YY > 780) & (YY < 1120) & (XX > 1000)
thick &= ~textzone

# Consolidate the scribble hat into one clean solid blob so auto_fill runs
# cleanly (close the scribble gaps, fill holes, shave thin tendrils).
hatzone = (YY > 840) & (YY < 1060) & (XX > 180) & (XX < 950)
hat_mask = thick & hatzone
if hat_mask.any():
    hat_clean = ndimage.binary_closing(hat_mask, disk(SUPPORT_HALFWIDTH_PX + 4))
    hat_clean = ndimage.binary_fill_holes(hat_clean)
    hat_clean = ndimage.binary_opening(hat_clean, disk(4))
    thick = (thick & ~hat_mask) | hat_clean
    # keep only the largest hat component (drop any shards the opening left)
    hl, hnc = ndimage.label(hat_clean)
    if hnc > 1:
        hsz = ndimage.sum(hat_clean, hl, range(1, hnc + 1))
        keep = 1 + int(np.argmax(hsz))
        thick[(hl > 0) & (hl != keep)] = False

thin = ink & ~thick
thin = remove_small_objects(thin, MIN_SPECK_PX)
print(f"thick px {thick.sum()}  thin px {thin.sum()}")

# ------------------------------------------------------------ fills (potrace)
def fmt(v):
    return f"{v:.2f}"

fill_paths = []
lbl, n = ndimage.label(thick)
for i in range(1, n + 1):
    comp = lbl == i
    if comp.sum() < MIN_FILL_AREA_PX:
        continue
    t = potrace.Bitmap(~comp).trace(turdsize=MIN_FILL_AREA_PX // 8, alphamax=1.0,
                                    opticurve=1, opttolerance=0.4)
    d_all = []
    for curve in t:
        sp = curve.start_point
        px, py = to_mm(sp.x, sp.y)
        d = [f"M {fmt(px)} {fmt(py)}"]
        for seg in curve.segments:
            ex, ey = to_mm(seg.end_point.x, seg.end_point.y)
            if seg.is_corner:
                cx, cy = to_mm(seg.c.x, seg.c.y)
                d.append(f"L {fmt(cx)} {fmt(cy)} L {fmt(ex)} {fmt(ey)}")
            else:
                c1x, c1y = to_mm(seg.c1.x, seg.c1.y)
                c2x, c2y = to_mm(seg.c2.x, seg.c2.y)
                d.append(f"C {fmt(c1x)} {fmt(c1y)} {fmt(c2x)} {fmt(c2y)} {fmt(ex)} {fmt(ey)}")
        d.append("Z")
        d_all.append(" ".join(d))
    cy_, cx_ = ndimage.center_of_mass(comp)
    fill_paths.append((cx_, cy_, " ".join(d_all)))
print(f"fill regions: {len(fill_paths)}")

# --------------------------------------------------------- linework skeleton
skel = skeletonize(thin)
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

# RDP only; the corner-preserving spline pass runs downstream on the SVG.
polylines = []
for e in edges:
    if len(e) < 4:
        continue
    pts = rdp(e, RDP_EPS_PX)
    w = 2 * np.mean([edt[p] for p in e]) * MM_PER_PX
    polylines.append((pts, w))
print(f"final polylines: {len(polylines)}")

# ------------------------------------------------------------- stitch order
fill_paths.sort(key=lambda t: (t[1], t[0]))
ordered = []
remaining = list(polylines)
cur = (y0, x0)
while remaining:
    best_i, best_rev, best_d = 0, False, 1e18
    for i, (pts, w) in enumerate(remaining):
        for rev in (False, True):
            p = pts[-1] if rev else pts[0]
            d = (p[0] - cur[0]) ** 2 + (p[1] - cur[1]) ** 2
            if d < best_d:
                best_i, best_rev, best_d = i, rev, d
    pts, w = remaining.pop(best_i)
    if best_rev:
        pts = pts[::-1]
    ordered.append((pts, w))
    cur = pts[-1]

# ----------------------------------------------------------------- write SVG
W_MM = (x1 - x0 + 1) * MM_PER_PX + 2 * MARGIN_MM
H_MM = TARGET_H_MM + 2 * MARGIN_MM
svg = []
svg.append(
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W_MM:.2f}mm" height="{H_MM:.2f}mm" '
    f'viewBox="0 0 {W_MM:.2f} {H_MM:.2f}" data-audit-profile="mixed">'
)
svg.append('  <g id="fills">')
n_contour = n_heart45 = 0
for i, (cxf, cyf, d) in enumerate(fill_paths):
    extra = ""
    if is_hat_region(cyf, cxf):
        # v3: contour_fill follows the hat's shape → clean, hair-like texture
        extra = ' data-stitch-method="contour_fill"'
        n_contour += 1
    elif is_heart_region(cxf, cyf):
        extra = ' data-fill-angle="45"'
        n_heart45 += 1
    svg.append(f'    <path id="fill{i}" style="fill:#000000;stroke:none"{extra} d="{d}"/>')
svg.append("  </g>")
print(f"fills: {len(fill_paths)} ({n_contour} contour hat, {n_heart45} heart@45)")
# NOTE: geometric loop-widening of the "6" was removed — it scaled only the
# loop edges (not the connecting upstroke), which tore the glyph at the join and
# made the 6 look broken. Keeping the 6 open now relies purely on thinner 2026
# satin (below), which preserves the digit detail without distortion.
def is_2026(cy, cx):
    return 780 < cy < 1120 and cx > 1000

svg.append('  <g id="linework">')
n_text = 0
n_bridge = 0
for i, (pts, w) in enumerate(ordered):
    mm = [to_mm(p[1], p[0]) for p in pts]
    d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in mm)
    cy = sum(p[0] for p in pts) / len(pts)
    cx = sum(p[1] for p in pts) / len(pts)
    if is_text(cy, cx):
        # bold satin column, slightly thinner than the previous cut.
        # 2026 is the smallest line — give it a thinner satin so the digit
        # counters (0s and the widened 6) don't fill in.
        if is_2026(cy, cx):
            # The former 0.7-1.0mm satin tier sat below the measured viable
            # width floor. A three-pass bean keeps the counters and line weight
            # without the short, raised stitches of micro-satin.
            sw = min(0.9, max(0.4, w))
            n_text += 1
            svg.append(
                f'    <path id="line{i}" style="fill:none;stroke:#000000;stroke-width:{sw:.2f};'
                f'stroke-linecap:round" data-stroke-method="bean_stitch" '
                f'data-bean-repeats="1" d="{d}"/>'
            )
            continue
        else:
            sw = min(2.05, max(1.3, w * 1.75))
        n_text += 1
        svg.append(
            f'    <path id="line{i}" style="fill:none;stroke:#000000;stroke-width:{sw:.2f};'
            f'stroke-linecap:round" data-stitch-method="satin" d="{d}"/>'
        )
    elif is_bridge(cy, cx):
        # sunglass bridge: single running stitch = thinner/lighter than bean
        sw = min(1.0, max(0.4, w))
        n_bridge += 1
        svg.append(
            f'    <path id="line{i}" style="fill:none;stroke:#000000;stroke-width:{sw:.2f};'
            f'stroke-linecap:round" data-stroke-method="running_stitch" d="{d}"/>'
        )
    else:
        sw = min(1.2, max(0.4, w))
        svg.append(
            f'    <path id="line{i}" style="fill:none;stroke:#000000;stroke-width:{sw:.2f};'
            f'stroke-linecap:round" data-stroke-method="bean_stitch" d="{d}"/>'
        )
svg.append("  </g>")
print(f"text(satin) edges: {n_text}  bridge(running) edges: {n_bridge}  "
      f"rat(bean) edges: {len(ordered) - n_text - n_bridge}")
svg.append("</svg>")
with open(OUT_SVG, "w") as f:
    f.write("\n".join(svg))
print(f"wrote {OUT_SVG}  ({W_MM:.1f} x {H_MM:.1f} mm)")

# --------------------------------------------------------------- debug image
dbg = Image.fromarray(np.stack([(220 - ink * 40).astype(np.uint8)] * 3, axis=-1))
px = np.array(dbg)
px[thick] = [255, 120, 120]
dbg = Image.fromarray(px)
draw = ImageDraw.Draw(dbg)
for pts, w in ordered:
    cy = sum(p[0] for p in pts) / len(pts)
    cx = sum(p[1] for p in pts) / len(pts)
    if is_text(cy, cx):
        color = (30, 170, 60)
    elif is_bridge(cy, cx):
        color = (230, 30, 200)
    else:
        color = (30, 60, 255)
    draw.line([(p[1], p[0]) for p in pts], fill=color, width=5)
# coordinate grid (original px) every 200px, labelled — for zone tuning
Himg, Wimg = ink.shape
for gx in range(0, Wimg, 200):
    draw.line([(gx, 0), (gx, Himg)], fill=(180, 180, 180), width=1)
    draw.text((gx + 2, y0), str(gx), fill=(120, 120, 120))
for gy in range(0, Himg, 200):
    draw.line([(0, gy), (Wimg, gy)], fill=(180, 180, 180), width=1)
    draw.text((x0, gy + 2), str(gy), fill=(120, 120, 120))
dbg.crop((x0 - 20, y0 - 20, x1 + 20, y1 + 20)).save(DEBUG_PNG)
print(f"wrote {DEBUG_PNG}")
PY_DONE = True
