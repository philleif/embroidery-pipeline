"""Reusable raster→embroidery wordmark tracer — the validated core of the
sketch/wordmark pipeline, extracted from the Paris Review job after it passed
the full quality bar (docs/QUALITY-BAR.md).

What lives here is everything that is NOT job-specific:

  mask loading + downscale     (deckle smoothing at ~30-40 px/mm)
  skeleton → edge extraction   (prune, collinear merge, junction-triangle
                                removal — ORDER MATTERS, see comments)
  closed-outline stabilization (topology-safe pixel adjacency; pure rings use
                                aligned inner/outer contours as satin rails)
  end extension                (free ends reach the true ink terminus,
                                junction ends push through so columns overlap)
  width split                  (satin column vs bean hairline, per run)
  variable-width satin rails   (EDT width on the medial axis, Savitzky-Golay
                                smoothing, taper into abutting hairlines)
  glyph-by-glyph routing       (finish one connected ink component before
                                starting the next — a trim per glyph, not per
                                stroke; measured off the Bjerknes pro file)
  underpath connectors         (least-cost travel THROUGH the ink where a
                                straight hop would leave it)
  trim policy + SVG emission   (data-trim-after / data-min-jump-mm /
                                data-satin-underlay per-path opt-outs that
                                tuning.py honors)
  coverage audit               (% ink stitched, spill, counter intrusion,
                                largest gaps in mm² — tune against these
                                numbers, not a stitch preview)
  as-sewn + debug renders

Per-job scripts (scripts/trace_*.py) should reduce to: source path, a
TraceConfig, and any job-specific zone logic. See scripts/trace_paris_review.py
for the reference driver.

Install tracing dependencies with `uv sync --extra trace`.
"""

from __future__ import annotations

from dataclasses import dataclass

import inspect
import os
import json

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from scipy.signal import savgol_filter
from skimage.graph import route_through_array
from skimage.measure import find_contours
from skimage.morphology import remove_small_objects, skeletonize

from .trace_topology import topology_neighbors


@dataclass
class TraceConfig:
    target_w_mm: float = 100.0
    margin_mm: float = 1.5
    work_w: int = 4000          # downscale width; deckle smooths out at ~30-40 px/mm
    min_speck_px: int = 12
    # Satin/bean split and column shape. The floors are measured off the pro
    # corpus (satin_w10 1.04-1.53mm) — see docs/QUALITY-BAR.md before changing.
    hairline_mm: float = 0.45   # local full ink width below this → bean centerline
    min_col_mm: float = 1.05    # narrowest satin column
    max_w_mm: float = 3.4
    width_gain: float = 1.06    # covers the rail polyline chord error on tight curves
    taper_mm: float = 1.2       # blend length where a column meets a bean hairline
    smooth_win: int = 9         # Savitzky-Golay window, resampled samples (~0.38mm)
    straighten_mm: float = 0.0  # snap near-straight runs to a chord (0 = off;
                                # made coverage WORSE on the Paris Review — see memory)
    junc_factor: float = 1.35
    # A closed outline (O, zero, border, heart) should be one continuous
    # satin column.  Raster medial axes grow small branches wherever edge
    # noise makes two boundary points equally near; treating those as real
    # branches adds a cap + underlay stack at every one.  A one-hole component
    # whose leaf-pruned skeleton retains this fraction is considered a pure
    # ring and rebuilt from paired inner/outer boundary rails.
    ring_core_min_frac: float = 0.80
    ring_min_hole_mm2: float = 1.0
    # Routing / trim policy.
    trim_hop_mm: float = 16.0   # max travel that may replace a trim
    inside_frac: float = 0.92   # straight hop must be this buried to skip the trim
    max_float_mm: float = 5.0   # a buried hop longer than this is still a float that
                                # can snag, so route it under the ink as running
                                # stitches instead of sewing it in one move
    # This is a wordmark tracer, so new thin drivers inherit the full wordmark
    # quality gate automatically. Decorative closed borders override it with
    # "satin-outline"; mixed/fill recipes declare their own profile.
    audit_profile: str = "satin-wordmark"
    narrow_col_mm: float = 1.5  # below this: center-walk-only underlay, low pull comp
    narrow_pull_comp_mm: float = 0.10
    contour_col_mm: float = 2.5  # wide columns add contour, never dense zigzag
    wide_satin_spacing_mm: float = 0.38  # broad curves otherwise measure ~0.45mm
    rail_snap_mm: float = 0.0   # max outward rail extension to the true ink edge
                                # (0 = off). Fixes square-cut sans terminals (EDT
                                # width collapses toward the end cut, pinching the
                                # column into a dome) and acute-junction wedges
                                # (ink asymmetric about the medial axis). Walks
                                # contiguous ink only, so it cannot jump a counter.
    # Outline mode ("fill_to_satin" pipeline): instead of emitting skeleton+EDT
    # rails, emit each satin branch as a potrace FILL plus synthesized rungs and
    # let Ink/Stitch's fill_to_satin build the rails from the true outline
    # (stitch_cli/fillsatin.py runs the conversion). Rails are still computed
    # as the per-branch fallback when a branch has no valid rung pair.
    satin_mode: str = "skeleton"    # "skeleton" | "outline"
    outline_smooth_mm: float = 0.30  # closing/opening radius on each branch mask.
                                     # Deliberately ABOVE the source's letterpress
                                     # deckle (~0.07mm rms, 0.6mm wavelength): that
                                     # texture sits below thread resolution (0.4mm
                                     # penetrations) and stitches as noise, so rails
                                     # are idealized like the pro files — the
                                     # letterpress read comes from the medium.
                                     # Opening is capped per branch at 0.35×width so
                                     # narrow branches aren't erased.
    outline_opttolerance: float = 0.8  # potrace curve-fit slack; 0.4 traces the
                                       # deckle, 0.8 fits long clean curves through it
    branch_overlap_mm: float = 0.15  # junction overlap between adjacent branches
                                     # (0.30 doubled a fat band at every junction
                                     # — measured as the main clump excess vs pro)
    rung_end_inset_mm: float = 0.5   # terminal rungs sit this far inside the tip
    rung_turn_deg: float = 30.0      # interior rung after this much tangent turn
    rung_max_gap_mm: float = 4.0     # max arc length between rungs
    merge_dot: float = 0.55          # how head-on two skeleton edges must meet to
                                     # fuse into one branch. Lower = longer branches
                                     # and fewer, longer satin columns, at the cost
                                     # of merging through genuine corners.
    rung_pad_mm: float = 0.35        # how far a clipped rung lands outside the
                                     # branch so fill_to_satin sees a clean
                                     # outside→inside→outside crossing
    rung_max_width_ratio: float = 2.5  # reject a rung longer than this times the
                                       # local ink width — it is running along
                                       # the stroke, not across it
    counter_hole_mm: float = 0.6     # a hole this wide inside one branch is a
                                     # counter to probe around, not deckle noise
    counter_wrap_rad: float = 2.0    # a branch sweeping this far around its own
                                     # counter gets cut so the counter sits
                                     # between two branches (~115°)
    counter_wrap_max_splits: int = 24  # cap on those cuts (one per pass)
    # Corner split (outline mode). A branch that bends sharply — the apex of
    # an A, the corners of an N, an L — is one bent column whose stitches
    # fan around the inside corner: dozens of penetrations share one point,
    # the outside stitches sweep through 90-180°, and the sew-out reads as
    # choppy. Cutting the branch at the corner gives two straight columns
    # that meet in a mitre, every stitch perpendicular to its own stroke —
    # the pro treatment (Acre: stem sews over bowl end). 0 = off.
    corner_split_deg: float = 0.0      # tangent turn that counts as a corner
    corner_split_max_splits: int = 64
    # Concave-corner split (outline mode). A bowl whose INSIDE rail has a
    # sharp corner (the S of a condensed grotesque: two counters with
    # angular ends) pivots its stitches on that corner even though the
    # centerline turns smoothly, so the corner split above never fires.
    # Find the ink's concave corners morphologically (closing with a small
    # disc fills a wedge at every one) and cut the nearest curving branch
    # there. Straight strokes past a concave corner are left alone: the cut
    # needs the centerline to be turning at least this much. 0 = off.
    concave_split_deg: float = 0.0
    concave_split_radius_mm: float = 0.30  # closing disc; below counter width
    # Straight strokes as rectangles (outline mode). Nearest-skeleton ink
    # assignment hands a straight stroke the corner WEDGES where it meets its
    # neighbours (the diagonal of an N takes both counter corners), and
    # fill_to_satin then fans the stitches round each wedge. A straight
    # piece is instead given a constant-width rectangle along its chord,
    # extended to the ink edge and clipped to the ink — every stitch
    # perpendicular to the stroke, the neighbour sews over the overlap.
    straight_rect_cols: bool = False
    overrides_json: str | None = None  # sidecar: add_rungs/drop_rungs/force_bean

    def __post_init__(self):
        if self.min_col_mm < 1.05:
            raise ValueError(
                "min_col_mm must be at least 1.05mm; use bean stitch for "
                "thinner artwork"
            )
        if self.max_w_mm < self.min_col_mm:
            raise ValueError("max_w_mm must be greater than or equal to min_col_mm")
        if not 0.30 <= self.wide_satin_spacing_mm <= 0.40:
            raise ValueError("wide_satin_spacing_mm must be between 0.30 and 0.40mm")
        if self.contour_col_mm < self.narrow_col_mm:
            raise ValueError("contour_col_mm must be greater than or equal to narrow_col_mm")


class WordmarkTracer:
    """One design, one instance. trace() → write_svg() → coverage_report()."""

    def __init__(self, src_png: str, cfg: TraceConfig):
        if not np.isfinite(cfg.target_w_mm) or cfg.target_w_mm <= 0:
            raise ValueError("target width must be finite and positive")
        if cfg.work_w < 32:
            raise ValueError("work width must be at least 32 pixels")
        self.cfg = cfg
        src = Image.open(src_png).convert("LA")
        a = np.array(src)
        al = a[..., 1].astype(float) / 255
        gray = a[..., 0].astype(float) * al + 255 * (1 - al)
        work_h = round(src.size[1] * cfg.work_w / src.size[0])
        work = np.array(Image.fromarray(gray.astype(np.uint8))
                        .resize((cfg.work_w, work_h), Image.LANCZOS))
        ink = work < 128
        # scikit-image 0.26 renamed min_size to max_size and changed the edge
        # from "smaller than" to "smaller than or equal". Preserve the exact
        # historical threshold across both APIs so dependency updates cannot
        # subtly delete one more pixel-sized component from a trace.
        if "max_size" in inspect.signature(remove_small_objects).parameters:
            self.ink = remove_small_objects(
                ink, max_size=max(0, cfg.min_speck_px - 1)
            )
        else:
            self.ink = remove_small_objects(ink, min_size=cfg.min_speck_px)

        ys, xs = np.where(self.ink)
        if not len(xs):
            raise ValueError("image contains no dark foreground after speck removal")
        self.x0, self.x1 = xs.min(), xs.max()
        self.y0, self.y1 = ys.min(), ys.max()
        bbox_w = self.x1 - self.x0 + 1
        self.bbox_h = self.y1 - self.y0 + 1
        self.mm_per_px = cfg.target_w_mm / bbox_w
        self.px_per_mm = 1.0 / self.mm_per_px
        print(f"work {cfg.work_w}x{work_h}px  ink bbox {bbox_w}x{self.bbox_h}px  "
              f"{self.px_per_mm:.1f} px/mm  design {cfg.target_w_mm:.1f} x "
              f"{self.bbox_h * self.mm_per_px:.1f} mm")

        self.edt = ndimage.distance_transform_edt(self.ink)
        # scale-derived pixel constants
        self.prune_px = int(round(0.55 * self.px_per_mm))
        self.rdp_eps_px = max(1.0, 0.05 * self.px_per_mm)
        self.sample_px = max(4, int(round(0.38 * self.px_per_mm)))
        self.min_run_px = int(round(1.3 * self.px_per_mm))
        self.overlap_px = max(2, int(round(0.12 * self.px_per_mm)))
        self.tip_w_mm = max(1.05, cfg.min_col_mm)
        self.no_trim_jump_mm = cfg.trim_hop_mm + 10.0

        self.ordered = []       # (pts, width_mm, method, rails, extra)
        self.glyph_ids = []     # ink-component id per ordered item
        self.no_trim = []
        self.connector = []
        self.missed = None
        self._closed_ring_data = {}

        self.overrides = {"add_rungs": [], "drop_rungs": [], "force_bean": []}
        if cfg.overrides_json:
            with open(cfg.overrides_json) as f:
                self.overrides.update(json.load(f))
            print(f"satin overrides: {cfg.overrides_json} "
                  f"(+{len(self.overrides['add_rungs'])} rungs, "
                  f"-{len(self.overrides['drop_rungs'])} rungs, "
                  f"{len(self.overrides['force_bean'])} forced beans)")

    # ------------------------------------------------------------ geometry
    def to_mm(self, x, y):
        return ((x - self.x0) * self.mm_per_px + self.cfg.margin_mm,
                (y - self.y0) * self.mm_per_px + self.cfg.margin_mm)

    def px(self, pt):
        return ((pt[0] - self.cfg.margin_mm) * self.px_per_mm + self.x0,
                (pt[1] - self.cfg.margin_mm) * self.px_per_mm + self.y0)

    def savgol(self, v, win, order=2):
        """Odd-window Savitzky-Golay with the endpoints pinned (they set the
        column cap, and letting them drift shortens stems off the baseline).
        S-G, not a boxcar: a moving average shrinks curve radii, which pulled
        the column off the P bowl's outer edge on the Paris Review."""
        n = len(v)
        win = min(win if win % 2 else win + 1, n if n % 2 else n - 1)
        if win <= order + 1 or n < 5:
            return v
        out = savgol_filter(v, win, order, mode="interp")
        out[0], out[-1] = v[0], v[-1]
        return out

    def rdp(self, pts, eps):
        if len(pts) < 3:
            return pts
        a, b = np.array(pts[0], float), np.array(pts[-1], float)
        ab = b - a
        lab = np.linalg.norm(ab)
        if lab < 1e-9:
            d = [np.linalg.norm(np.array(p, float) - a) for p in pts[1:-1]]
        else:
            d = [abs(ab[0] * (p[1] - a[1]) - ab[1] * (p[0] - a[0])) / lab for p in pts[1:-1]]
        if not d:
            return [pts[0], pts[-1]]
        imax = int(np.argmax(d))
        if d[imax] > eps:
            return self.rdp(pts[: imax + 2], eps)[:-1] + self.rdp(pts[imax + 1:], eps)
        return [pts[0], pts[-1]]

    # ------------------------------------------------------------ skeleton
    @staticmethod
    def _neighbors_in(p, coords):
        """Return topology-preserving neighbours for a skeleton pixel.

        Skeletons need 8-connectivity so a genuinely diagonal one-pixel line
        stays connected.  A raw 8-neighbour graph is subtly wrong, though:
        at a rasterized right-angle turn it adds the diagonal as a *third*
        edge alongside the two orthogonal pixels::

            A .       A-B-C is the intended path; raw 8-connectivity also
            B C       adds A-C and turns the corner into a tiny triangle.

        Those one-pixel triangles were read as satin junctions.  A smooth
        closed outline such as a heart consequently fragmented into dozens
        of independently capped columns, piling top stitches and underlay at
        every false junction.  Keep a diagonal only when there is no
        orthogonal two-step route between the same pixels.  This preserves
        true diagonal runs while representing ordinary corners as one path.
        """
        return topology_neighbors(p, coords)

    def _neighbors(self, p):
        return self._neighbors_in(p, self.coords)

    def _edt_at(self, p):
        """EDT sample accepting both integer skeleton pixels and the
        sub-pixel contours used for stabilized closed rings."""
        y = min(self.edt.shape[0] - 1, max(0, int(round(p[0]))))
        x = min(self.edt.shape[1] - 1, max(0, int(round(p[1]))))
        return self.edt[y, x]

    def _extract_closed_rings(self):
        """Replace noisy medial-axis graphs for pure outline loops with one
        stable closed centerline each.

        Leaf pruning gives the skeleton's 2-core.  If a one-hole ink
        component keeps nearly all of its skeleton in that core, it is an
        outline loop rather than a glyph such as P (whose stem is a large
        leaf branch).  Its centerline is then constructed halfway between
        arc-length-resampled inner and outer boundaries.  Their orientation
        and cyclic phase are aligned as whole curves, so pairing stays
        monotonic through acute cusps instead of jumping to whichever side is
        locally nearest.  This follows corners that a mathematical medial
        axis represents as leaf branches while ignoring its unstable cycles.

        Returns the extracted ring edges and removes their pixels from
        ``self.coords`` so the generic branch extractor cannot digitize the
        same component again.
        """
        cfg = self.cfg
        ink_lbl, n_ink = ndimage.label(self.ink, structure=np.ones((3, 3)))
        rings = []
        remaining = set(self.coords)

        for cid in range(1, n_ink + 1):
            comp_coords = {p for p in self.coords if ink_lbl[p] == cid}
            if len(comp_coords) < 8:
                continue

            # Crop from the full ink component, not its centerline: the outer
            # contour sits roughly a half stroke-width beyond the skeleton.
            # A skeleton-sized crop clips that boundary and leaves the inner
            # counter as the only closed contour.
            ys, xs = np.where(ink_lbl == cid)
            pad = 2
            r0, r1 = max(0, int(ys.min()) - pad), min(
                self.ink.shape[0], int(ys.max()) + pad + 1)
            c0, c1 = max(0, int(xs.min()) - pad), min(
                self.ink.shape[1], int(xs.max()) + pad + 1)
            ink_sub = ink_lbl[r0:r1, c0:c1] == cid
            ink_holes = ndimage.binary_fill_holes(ink_sub) & ~ink_sub
            hole_lbl, n_holes = ndimage.label(ink_holes)
            hole_sizes = (ndimage.sum(ink_holes, hole_lbl, range(1, n_holes + 1))
                          if n_holes else [])
            significant = [i + 1 for i, area in enumerate(hole_sizes)
                           if area * self.mm_per_px ** 2 >= cfg.ring_min_hole_mm2]
            if len(significant) != 1:
                continue

            # Peel every free branch, not merely a fixed physical length.
            # For a pure loop this removes only medial-axis noise; for P/Q/6
            # it removes a meaningful stem/tail and fails the fraction test.
            core = set(comp_coords)
            queue = [p for p in core if len(self._neighbors_in(p, core)) <= 1]
            while queue:
                p = queue.pop()
                if p not in core or len(self._neighbors_in(p, core)) > 1:
                    continue
                neighbours = self._neighbors_in(p, core)
                core.remove(p)
                queue.extend(neighbours)
            core_frac = len(core) / len(comp_coords)
            if not core or core_frac < cfg.ring_core_min_frac:
                continue

            hole = hole_lbl == significant[0]
            inner_contours = find_contours(hole.astype(float), 0.5)
            component_contours = find_contours(ink_sub.astype(float), 0.5)
            if not inner_contours or not component_contours:
                continue
            inner = max(inner_contours, key=len)
            # The outer boundary encloses the greatest area.  It is not
            # necessarily the longest contour: a hand-drawn concave counter
            # can have a slightly longer perimeter than the outside edge.
            def enclosed_area(c):
                return abs(float(np.dot(c[:, 1], np.roll(c[:, 0], -1))
                                 - np.dot(c[:, 0], np.roll(c[:, 1], -1))) / 2)

            outer = max(component_contours, key=enclosed_area)
            def resample_closed(c, n):
                c = np.asarray(c, float)
                if np.hypot(*(c[0] - c[-1])) < 1e-6:
                    c = c[:-1]
                closed = np.vstack([c, c[0]])
                seg = np.sqrt(((closed[1:] - closed[:-1]) ** 2).sum(1))
                s = np.concatenate([[0.0], np.cumsum(seg)])
                si = np.linspace(0.0, s[-1], n, endpoint=False)
                return np.column_stack([
                    np.interp(si, s, closed[:, 0]),
                    np.interp(si, s, closed[:, 1]),
                ])

            # Whole-loop alignment at modest resolution is cheap and avoids
            # a local nearest-neighbour switch at V-shaped cusps.
            n_align = 512
            ia = resample_closed(inner, n_align)
            best = None
            for reverse in (False, True):
                oa = resample_closed(outer[::-1] if reverse else outer, n_align)
                for shift in range(n_align):
                    paired = np.roll(oa, -shift, axis=0)
                    cost = float(((ia - paired) ** 2).sum())
                    if best is None or cost < best[0]:
                        best = (cost, reverse, shift)

            inner_len = float(np.sqrt(((np.vstack([inner, inner[0]])[1:]
                                        - np.vstack([inner, inner[0]])[:-1]) ** 2)
                                      .sum(1)).sum())
            outer_len = float(np.sqrt(((np.vstack([outer, outer[0]])[1:]
                                        - np.vstack([outer, outer[0]])[:-1]) ** 2)
                                      .sum(1)).sum())
            n_center = max(12, int((inner_len + outer_len)
                                   / (2 * self.sample_px)) + 1)
            inner_r = resample_closed(inner, n_center)
            oriented_outer = outer[::-1] if best[1] else outer
            outer_r = resample_closed(oriented_outer, n_center)
            shift = int(round(best[2] * n_center / n_align)) % n_center
            outer_r = np.roll(outer_r, -shift, axis=0)

            # Rail contours come from pixel edges; smooth their sub-thread
            # raster steps cyclically without flattening the V cusps.  The
            # tracer's usual S-G window is short relative to a whole loop.
            win = min(cfg.smooth_win if cfg.smooth_win % 2 else cfg.smooth_win + 1,
                      n_center if n_center % 2 else n_center - 1)
            if win >= 5:
                for rail in (inner_r, outer_r):
                    rail[:, 0] = savgol_filter(rail[:, 0], win, 2, mode="wrap")
                    rail[:, 1] = savgol_filter(rail[:, 1], win, 2, mode="wrap")

            # A stabilized ring bypasses the generic width splitter below, so
            # enforce the same physical satin floor here.  Thin raster outlines
            # are often only 0.4-0.7 mm after scaling: using their literal
            # boundaries creates fragile micro-satin even though min_col_mm was
            # requested.  Expand locally about the paired-rail midpoint; this
            # preserves the traced centerline and all width variation above the
            # floor while avoiding a second offset/normal calculation at cusps.
            rail_delta = outer_r - inner_r
            rail_width_px = np.sqrt((rail_delta ** 2).sum(1))
            original_width_mm = float(np.mean(rail_width_px) * self.mm_per_px)
            min_width_px = cfg.min_col_mm / self.mm_per_px
            rail_scale = np.maximum(
                1.0, min_width_px / np.maximum(rail_width_px, 1e-6)
            )
            midpoint = (inner_r + outer_r) / 2.0
            inner_r = midpoint - 0.5 * rail_delta * rail_scale[:, None]
            outer_r = midpoint + 0.5 * rail_delta * rail_scale[:, None]

            center = (inner_r + outer_r) / 2.0

            # Contour libraries choose an arbitrary cyclic phase. That phase
            # becomes the satin object's start/finish, where tie stitches and
            # the loop closure coincide. If it lands on an acute corner, a
            # geometrically sound loop can still create a severe local knot.
            # Rotate all paired curves together so the lock lands in the
            # middle of the straightest neighbourhood instead.
            prev = np.roll(center, 1, axis=0)
            nxt = np.roll(center, -1, axis=0)
            vin = center - prev
            vout = nxt - center
            denom = np.maximum(
                1e-9,
                np.sqrt((vin ** 2).sum(1) * (vout ** 2).sum(1)),
            )
            cos_turn = np.clip((vin * vout).sum(1) / denom, -1.0, 1.0)
            turn = np.arccos(cos_turn)
            radius = max(2, int(round(0.8 / max(
                self.mm_per_px * (inner_len + outer_len)
                / (2 * n_center), 1e-6
            ))))
            turn_neighbourhood = sum(
                np.roll(turn, offset)
                for offset in range(-radius, radius + 1)
            )
            start_idx = int(np.argmin(turn_neighbourhood))
            inner_r = np.roll(inner_r, -start_idx, axis=0)
            outer_r = np.roll(outer_r, -start_idx, axis=0)
            center = np.roll(center, -start_idx, axis=0)

            inner_global = [(float(y + r0), float(x + c0)) for y, x in inner_r]
            outer_global = [(float(y + r0), float(x + c0)) for y, x in outer_r]
            edge = [(float(y + r0), float(x + c0)) for y, x in center]
            for seq in (edge, inner_global, outer_global):
                seq.append(seq[0])
            rings.append(edge)
            self._closed_ring_data[id(edge)] = {
                "rails_px": (inner_global, outer_global),
                "source_width_mm": original_width_mm,
                "width_mm": float(np.mean(np.sqrt(
                    ((inner_r - outer_r) ** 2).sum(1))) * self.mm_per_px),
            }
            remaining.difference_update(comp_coords)
            print(f"closed ring component {cid}: {len(comp_coords)} skeleton px "
                  f"-> 1 loop ({100 * core_frac:.1f}% 2-core, "
                  f"width {original_width_mm:.2f}->"
                  f"{self._closed_ring_data[id(edge)]['width_mm']:.2f} mm)")

        self.coords = remaining
        return rings

    def _extract_edges(self):
        coords = self.coords
        deg = {p: len(self._neighbors(p)) for p in coords}
        nodes = {p for p, d in deg.items() if d != 2}
        visited = set()
        edges = []
        for node in nodes:
            for nb in self._neighbors(node):
                if (node, nb) in visited:
                    continue
                path = [node, nb]
                visited.add((node, nb))
                visited.add((nb, node))
                prev, cur = node, nb
                while cur not in nodes:
                    nxt = [q for q in self._neighbors(cur) if q != prev]
                    if not nxt:
                        break
                    nxt = nxt[0]
                    visited.add((cur, nxt))
                    visited.add((nxt, cur))
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
                nxt = [q for q in self._neighbors(cur) if q != prev and q in leftover]
                if not nxt:
                    break
                cur, prev = nxt[0], cur
                loop.append(cur)
                leftover.discard(cur)
            loop.append(start)
            edges.append(loop)
        return edges

    def _end_dir(self, e, at_start):
        k = min(int(0.25 * self.px_per_mm), len(e) - 1)
        if at_start:
            v = (e[k][0] - e[0][0], e[k][1] - e[0][1])
        else:
            v = (e[-1 - k][0] - e[-1][0], e[-1 - k][1] - e[-1][1])
        nn = max(1e-9, (v[0] ** 2 + v[1] ** 2) ** 0.5)
        return (v[0] / nn, v[1] / nn)

    def _build_edges(self):
        self.coords = set(zip(*np.where(skeletonize(self.ink))))
        ring_edges = self._extract_closed_rings()
        edges = ring_edges + self._extract_edges()
        print(f"raw skeleton edges: {len(edges)}")

        for _ in range(3):
            deg_count = {}
            for e in edges:
                for end in (e[0], e[-1]):
                    deg_count[end] = deg_count.get(end, 0) + 1
            edges = [e for e in edges
                     if not ((deg_count[e[0]] == 1 or deg_count[e[-1]] == 1)
                             and len(e) < self.prune_px)]
        print(f"after prune (<{self.prune_px}px spurs): {len(edges)}")

        # Merge edge pairs that meet head-on at a node: a stem interrupted by a
        # serif junction should stay one column, not three stubs with jumps.
        changed = True
        while changed:
            changed = False
            endpoint_map = {}
            for idx, e in enumerate(edges):
                for at_start in (True, False):
                    endpoint_map.setdefault(
                        e[0] if at_start else e[-1], []).append((idx, at_start))
            for pt, ends in endpoint_map.items():
                if len(ends) < 2:
                    continue
                best, best_dot = None, self.cfg.merge_dot
                for a in range(len(ends)):
                    for b in range(a + 1, len(ends)):
                        ia, sa = ends[a]
                        ib, sb = ends[b]
                        if ia == ib:
                            continue
                        da = self._end_dir(edges[ia], sa)
                        db = self._end_dir(edges[ib], sb)
                        dot = -(da[0] * db[0] + da[1] * db[1])
                        if dot > best_dot:
                            best_dot, best = dot, (ia, sa, ib, sb)
                if best:
                    ia, sa, ib, sb = best
                    ea = edges[ia] if not sa else edges[ia][::-1]
                    eb = edges[ib] if sb else edges[ib][::-1]
                    merged = ea + eb[1:]
                    edges = [e for k, e in enumerate(edges)
                             if k not in (ia, ib)] + [merged]
                    changed = True
                    break
        print(f"after collinear merge: {len(edges)}")

        # Junction-triangle removal. Wherever a serif slab meets a stem the
        # skeleton closes a small triangle around the junction; rendered, each
        # side becomes a satin bead. Drop connectors shorter than the local
        # stroke width whose two ends are both branch points.
        #
        # Must run AFTER the collinear merge: a T-junction splits into two
        # branch points a short distance apart, so the piece of stem between
        # them is itself a short branch-to-branch edge — dropping it first blew
        # a 6.7mm² hole through the stem of the Paris Review E. Merging first
        # fuses it into the through-stroke, leaving only genuine triangle sides.
        branch_points = {p for p in self.coords if len(self._neighbors(p)) >= 3}
        kept, dropped = [], 0
        for e in edges:
            span = 2 * float(np.mean([self._edt_at(p) for p in e]))
            if (e[0] != e[-1] and e[0] in branch_points and e[-1] in branch_points
                    and len(e) < self.cfg.junc_factor * span):
                dropped += 1
                continue
            kept.append(e)
        edges = kept
        print(f"after junction-triangle removal: {len(edges)} (dropped {dropped})")

        # Bridge merge: a serif slab crossed by a stem skeletonizes as TWO
        # wing edges whose facing ends stop at the junction, one stem-width
        # apart — the collinear merge above can't see them (no shared node)
        # and each wing then becomes its own satin fragment, so the junction
        # is stitched three deep (both wings + the stem end) and knots. The
        # pros stitch a serif as ONE column crossing the stem. Join edge-end
        # pairs that head at each other along the same line across a short
        # all-ink gap.
        def bridge_px(pa, pb):
            gap = np.hypot(pb[0] - pa[0], pb[1] - pa[1])
            steps = max(2, int(gap))
            pts = [(int(round(pa[0] + (pb[0] - pa[0]) * t)),
                    int(round(pa[1] + (pb[1] - pa[1]) * t)))
                   for t in np.linspace(0, 1, steps)]
            return pts if all(self.ink[p] for p in pts) else None

        changed = self.cfg.satin_mode == "outline"   # keep validated skeleton
        n_bridged = 0                                # mode byte-identical
        while changed:
            changed = False
            ends = [(idx, at_start, e[0] if at_start else e[-1])
                    for idx, e in enumerate(edges) for at_start in (True, False)]
            for a in range(len(ends)):
                ia, sa, pa = ends[a]
                da = self._end_dir(edges[ia], sa)
                for b in range(a + 1, len(ends)):
                    ib, sb, pb = ends[b]
                    if ia == ib:
                        continue
                    gap = float(np.hypot(pb[0] - pa[0], pb[1] - pa[1]))
                    max_gap = min(4.0 * self.px_per_mm,
                                  2 * max(self._edt_at(pa), self._edt_at(pb)) + 6)
                    if gap < 2 or gap > max_gap:
                        continue
                    db = self._end_dir(edges[ib], sb)
                    v = ((pb[0] - pa[0]) / gap, (pb[1] - pa[1]) / gap)
                    # _end_dir points inward: each end must face the other.
                    # 0.7 not 0.8 — wing ends bend where the junction triangle
                    # was removed, so facing directions are only approximate.
                    if (-da[0] * v[0] - da[1] * v[1]) < 0.7:
                        continue
                    if (db[0] * v[0] + db[1] * v[1]) < 0.7:
                        continue
                    bridge = bridge_px(pa, pb)
                    if bridge is None:
                        continue
                    ea = edges[ia] if not sa else edges[ia][::-1]
                    eb = edges[ib] if sb else edges[ib][::-1]
                    merged = ea + bridge[1:-1] + eb
                    edges = [e for k, e in enumerate(edges)
                             if k not in (ia, ib)] + [merged]
                    changed = True
                    n_bridged += 1
                    break
                if changed:
                    break
        if self.cfg.satin_mode == "outline":
            print(f"after bridge merge: {len(edges)} "
                  f"({n_bridged} junction gaps bridged)")

        # End extension has two physically different cases. A free terminal
        # ends about half a stroke-width inside the ink and must reach the true
        # artwork edge. A junction endpoint is already centered where other
        # columns meet; walking it all the way to the far edge makes every arm
        # cross the full joint and stacks three or more satin passes there.
        # Give junctions only a small controlled overlap to close the seam.
        def extend_ends(e):
            if e[0] == e[-1]:
                return e
            e = list(e)
            for at_start in (True, False):
                tip = e[0] if at_start else e[-1]
                dy, dx = self._end_dir(e, at_start)
                dy, dx = -dy, -dx               # _end_dir points inward
                if tip in branch_points:
                    limit = max(1.0, self.cfg.branch_overlap_mm * self.px_per_mm)
                else:
                    limit = max(self._edt_at(tip) + 2, 0.9 * self.px_per_mm)
                added = []
                for t in np.arange(1.0, limit, 1.0):
                    p = (int(round(tip[0] + dy * t)), int(round(tip[1] + dx * t)))
                    if (not (0 <= p[0] < self.ink.shape[0]
                             and 0 <= p[1] < self.ink.shape[1])
                            or not self.ink[p]):
                        break
                    if not added or p != added[-1]:
                        added.append(p)
                if added:
                    e = (added[::-1] + e) if at_start else (e + added)
            return e

        return [extend_ends(e) for e in edges]

    # ------------------------------------------------------------ pieces
    def _split_by_width(self, e):
        """Split an edge along its length by smoothed local ink width. Returns
        (pixel-run, is_hairline, taper_start, taper_end). Hairline runs
        shorter than min_run_px are width-estimate wobble inside a thick
        stroke and merge into their neighbour."""
        wloc = np.array([2 * self._edt_at(p) * self.mm_per_px for p in e])
        k = max(3, int(round(0.3 * self.px_per_mm)) | 1)
        if len(wloc) > k:
            pad = k // 2
            wloc = np.convolve(np.pad(wloc, pad, mode="edge"),
                               np.ones(k) / k, mode="valid")
        thin_flags = wloc < self.cfg.hairline_mm
        runs, s = [], 0
        for i in range(1, len(e) + 1):
            if i == len(e) or thin_flags[i] != thin_flags[s]:
                runs.append([s, i, bool(thin_flags[s])])
                s = i
        changed = True
        while changed and len(runs) > 1:
            changed = False
            for i, (rs, re, fl) in enumerate(runs):
                if re - rs < self.min_run_px:
                    j = i - 1 if i > 0 else i + 1
                    runs[j][2] = (runs[j][2] if (re - rs) < (runs[j][1] - runs[j][0])
                                  else fl)
                    runs[j][0] = min(runs[j][0], rs)
                    runs[j][1] = max(runs[j][1], re)
                    runs.pop(i)
                    changed = True
                    break
        return [(e[max(0, rs - self.overlap_px):min(len(e), re + self.overlap_px)],
                 fl, i > 0 and runs[i - 1][2],
                 i < len(runs) - 1 and runs[i + 1][2])
                for i, (rs, re, fl) in enumerate(runs)]

    def _straighten(self, piece):
        p = np.array(piece, float)
        a, b = p[0], p[-1]
        ab = b - a
        L = float(np.hypot(*ab))
        if L < 1e-6:
            return piece, False
        dev = np.abs(ab[0] * (p - a)[:, 1] - ab[1] * (p - a)[:, 0]) / L
        if (self.cfg.straighten_mm <= 0
                or dev.max() * self.mm_per_px > self.cfg.straighten_mm):
            return piece, False
        t = np.linspace(0.0, 1.0, len(p))
        return [tuple(v) for v in (a + np.outer(t, ab))], True

    def _column_geometry(self, piece):
        """Shared centerline geometry for rails and rung synthesis: resampled
        + S-G smoothed centerline, smoothed+clipped width (mm), unit normals,
        and the per-sample arc step in mm."""
        cfg = self.cfg
        pts = np.array(piece, float)               # (y, x)
        seg = np.sqrt(((pts[1:] - pts[:-1]) ** 2).sum(1))
        s = np.concatenate([[0], np.cumsum(seg)])
        n = max(6, int(s[-1] / self.sample_px) + 1)
        si = np.linspace(0, s[-1], n)
        ys_ = np.interp(si, s, pts[:, 0])
        xs_ = np.interp(si, s, pts[:, 1])
        w_axis = 2 * np.array([self.edt[(int(round(p[0])), int(round(p[1])))]
                               for p in piece]) * self.mm_per_px
        w = np.interp(si, s, w_axis)
        ys_ = self.savgol(ys_, cfg.smooth_win)
        xs_ = self.savgol(xs_, cfg.smooth_win)
        w = self.savgol(w, cfg.smooth_win + 4)     # width is the noisier signal
        w = np.clip(w * cfg.width_gain, cfg.min_col_mm, cfg.max_w_mm)
        ty, tx = np.gradient(ys_), np.gradient(xs_)
        norm = np.maximum(1e-9, np.sqrt(ty ** 2 + tx ** 2))
        nyv, nxv = -tx / norm, ty / norm
        step_mm = (s[-1] / max(1, n - 1)) * self.mm_per_px
        return ys_, xs_, w, nyv, nxv, step_mm

    def _tapered_column(self, piece, taper_start, taper_end):
        """Variable-width satin column: centerline resampled + S-G smoothed,
        rail offset = EDT ink width read ON THE MEDIAL AXIS and interpolated
        along arc length — never resampled at the smoothed centerline, which
        lands off-axis where the EDT is smaller and narrows exactly the curves
        that need width most."""
        cfg = self.cfg
        ys_, xs_, w, nyv, nxv, step_mm = self._column_geometry(piece)
        n = len(ys_)
        nt = min(n, max(2, int(cfg.taper_mm / max(1e-6, step_mm))))
        if taper_start:
            w[:nt] = self.tip_w_mm + (w[:nt] - self.tip_w_mm) * np.linspace(0.0, 1.0, nt)
        if taper_end:
            w[-nt:] = self.tip_w_mm + (w[-nt:] - self.tip_w_mm) * np.linspace(1.0, 0.0, nt)
        h_px = (w / 2) * self.px_per_mm
        rails = []
        snap_px = cfg.rail_snap_mm * self.px_per_mm
        H, W = self.ink.shape
        for sign in (1, -1):
            ry = self.savgol(ys_ + sign * nyv * h_px, cfg.smooth_win)
            rx = self.savgol(xs_ + sign * nxv * h_px, cfg.smooth_win)
            if snap_px > 0:
                for i in range(len(ry)):
                    # Walk outward along the normal while still on contiguous
                    # ink; the first gap pixel stops the walk, so a rail can
                    # widen into a junction wedge but never across a counter.
                    best = 0.0
                    for t in np.arange(1.0, snap_px + 1.0, 1.0):
                        py = int(round(ry[i] + sign * nyv[i] * t))
                        px_ = int(round(rx[i] + sign * nxv[i] * t))
                        if not (0 <= py < H and 0 <= px_ < W) or not self.ink[py, px_]:
                            break
                        best = t
                    if best > 0:
                        ry[i] += sign * nyv[i] * best
                        rx[i] += sign * nxv[i] * best
                # Re-smooth with a short window: snapping traces the ink
                # contour pixel-exactly and leaves 1px steps where it engages.
                ry = self.savgol(ry, 5)
                rx = self.savgol(rx, 5)
            rails.append([self.to_mm(x, y) for x, y in zip(rx, ry)])
        return rails

    def _run_len_mm(self, piece):
        p = np.array(piece, float)
        return float(np.sqrt(((p[1:] - p[:-1]) ** 2).sum(1)).sum()) * self.mm_per_px

    # ------------------------------------------------------------ outline mode
    def _potrace_branch(self, submask, r0, c0):
        """Potrace one branch mask into an SVG path d (design mm). Same call
        as scripts/trace_lockup.py — potracer's Bitmap takes the INVERTED
        mask. Corner segments emit line pairs, others cubic beziers."""
        import potrace  # potracer; only outline mode needs it

        t = potrace.Bitmap(~submask).trace(
            turdsize=max(4, self.cfg.min_speck_px // 2),
            alphamax=1.0, opticurve=1,
            opttolerance=self.cfg.outline_opttolerance)

        def fmt(px_x, px_y):
            x, y = self.to_mm(px_x + c0, px_y + r0)
            return f"{x:.2f} {y:.2f}"

        d_all = []
        for curve in t:
            sp = curve.start_point
            d = [f"M {fmt(sp.x, sp.y)}"]
            for seg in curve.segments:
                if seg.is_corner:
                    d.append(f"L {fmt(seg.c.x, seg.c.y)} "
                             f"L {fmt(seg.end_point.x, seg.end_point.y)}")
                else:
                    d.append(f"C {fmt(seg.c1.x, seg.c1.y)} "
                             f"{fmt(seg.c2.x, seg.c2.y)} "
                             f"{fmt(seg.end_point.x, seg.end_point.y)}")
            d.append("Z")
            d_all.append(" ".join(d))
        return " ".join(d_all)

    def _synth_rungs(self, piece, inside):
        """Synthesize fill_to_satin direction rungs for one branch: two
        terminal rungs inset from the tips plus interior rungs wherever the
        tangent has turned rung_turn_deg or rung_max_gap_mm has passed
        (straight stems get exactly two → one merged satin section). Every
        rung is validated to cross the branch outline exactly twice —
        outside→inside→outside along its length — retrying longer, shorter
        and nudged along the centerline before giving up. Returns a list of
        ((x1,y1),(x2,y2)) in design mm, or None if fewer than 2 survive."""
        cfg = self.cfg
        ys_, xs_, w, nyv, nxv, step_mm = self._column_geometry(piece)
        n = len(ys_)
        total_mm = step_mm * (n - 1)
        if total_mm < 2 * cfg.rung_end_inset_mm + 0.3:
            return None

        i0 = min(n - 2, max(1, int(round(
            max(cfg.rung_end_inset_mm, 0.6 * w[0] / 2) / step_mm))))
        i1 = max(i0 + 1, n - 1 - max(1, int(round(
            max(cfg.rung_end_inset_mm, 0.6 * w[-1] / 2) / step_mm))))

        ang = np.unwrap(np.arctan2(nyv, nxv))
        picks, last_ang, last_i = [i0], ang[i0], i0
        for i in range(i0 + 1, i1):
            if (abs(ang[i] - last_ang) >= np.radians(cfg.rung_turn_deg)
                    or (i - last_i) * step_mm >= cfg.rung_max_gap_mm):
                picks.append(i)
                last_ang, last_i = ang[i], i
        picks.append(i1)

        nudge = max(1, int(round(0.3 / max(1e-6, step_mm))))
        pad_px = cfg.rung_pad_mm * self.px_per_mm

        def clipped_rung(j, f, samples=241):
            """Rung spanning only the inside run that holds the centerline.

            A probe scaled to a multiple of the local width re-enters the
            branch wherever it is fat and hooked — a junction wedge, or a bowl
            curving back under itself — so the exact-two-crossings test below
            rejects it and the branch is left with terminal rungs only.
            Clipping to the centered run crosses the outline exactly twice by
            construction; the pad walks outward only while still outside, so a
            neighbouring lobe can never be swallowed."""
            half = (w[j] / 2) * self.px_per_mm * f
            ts = np.linspace(-1.0, 1.0, samples)
            py = ys_[j] + nyv[j] * half * ts
            px_ = xs_[j] + nxv[j] * half * ts
            mid = samples // 2
            ins = [inside(py[k], px_[k]) for k in range(samples)]
            if not ins[mid]:
                return None
            lo = mid
            while lo > 0 and ins[lo - 1]:
                lo -= 1
            hi = mid
            while hi < samples - 1 and ins[hi + 1]:
                hi += 1
            if lo == 0 or hi == samples - 1:
                return None                     # run runs off the probe: too short
            step_px = 2.0 * half / (samples - 1)
            grow = max(1, int(round(pad_px / max(1e-6, step_px))))
            a = lo
            for k in range(lo - 1, max(-1, lo - grow - 1), -1):
                if ins[k]:
                    break
                a = k
            b = hi
            for k in range(hi + 1, min(samples, hi + grow + 1)):
                if ins[k]:
                    break
                b = k
            if a == lo or b == hi:
                return None                     # no clear outside room to land on
            span_mm = np.hypot(px_[b] - px_[a], py[b] - py[a]) / self.px_per_mm
            if span_mm > cfg.rung_max_width_ratio * w[j] + 2 * cfg.rung_pad_mm:
                return None                     # along the stroke, not across it
            return ((px_[a], py[a]), (px_[b], py[b]))

        rungs = []
        for i in picks:
            placed = None
            for f in (1.7, 2.4, 1.3):
                for di in (0, nudge, -nudge):
                    j = min(n - 1, max(0, i + di))
                    half = (w[j] / 2) * self.px_per_mm * f
                    ts = np.linspace(-1.0, 1.0, 41)
                    py = ys_[j] + nyv[j] * half * ts
                    px_ = xs_[j] + nxv[j] * half * ts
                    ins = [inside(py[k], px_[k]) for k in range(len(ts))]
                    trans = sum(1 for k in range(1, len(ins)) if ins[k] != ins[k - 1])
                    if trans == 2 and ins[len(ts) // 2] and not ins[0] and not ins[-1]:
                        placed = ((px_[0], py[0]), (px_[-1], py[-1]))
                        break
                if placed:
                    break
            if placed is None:
                for f in (2.4, 3.4, 4.8):
                    for di in (0, nudge, -nudge):
                        j = min(n - 1, max(0, i + di))
                        placed = clipped_rung(j, f)
                        if placed:
                            break
                    if placed:
                        break
            if placed:
                rungs.append(placed)
        # collapse near-duplicates from nudging (two picks landing together)
        dedup = []
        for r in rungs:
            mid = ((r[0][0] + r[1][0]) / 2, (r[0][1] + r[1][1]) / 2)
            if all(np.hypot(mid[0] - (q[0][0] + q[1][0]) / 2,
                            mid[1] - (q[0][1] + q[1][1]) / 2)
                   > 0.25 * self.px_per_mm for q in dedup):
                dedup.append(r)
        if len(dedup) < 2:
            return None
        return [(self.to_mm(a[0], a[1]), self.to_mm(b[0], b[1]))
                for a, b in dedup]

    def _assign_ink(self, polylines):
        """Label every ink pixel with the nearest piece's skeleton run, so
        branch masks tile the ink with no orphans."""
        lbl = np.zeros(self.ink.shape, np.int32)
        for k, (pts, w, method, rails, extra) in enumerate(polylines):
            run = extra.get("piece", pts) if extra else pts
            for p in run:
                lbl[int(round(p[0])), int(round(p[1]))] = k + 1
        _, (iy, ix) = ndimage.distance_transform_edt(
            lbl == 0, return_indices=True)
        return np.where(self.ink, lbl[iy, ix], 0)

    def _enclosed_counter(self, mask):
        """Centroid + area of the largest counter-sized hole in one branch
        mask, or None. Small holes are deckle noise and do not count."""
        filled = ndimage.binary_fill_holes(mask)
        holes = filled & ~mask
        if not holes.any():
            return None
        hole_lbl, n_holes = ndimage.label(holes)
        sizes = ndimage.sum(holes, hole_lbl, range(1, n_holes + 1))
        best = int(np.argmax(sizes)) + 1
        if sizes[best - 1] < (self.cfg.counter_hole_mm * self.px_per_mm) ** 2:
            return None
        ys, xs = np.nonzero(hole_lbl == best)
        return (ys.mean(), xs.mean()), float(sizes[best - 1])

    def _corner_split(self, polylines):
        """Cut every satin branch at each sharp corner along its centerline.

        A corner is a turn of at least corner_split_deg between the chords
        one stroke-width either side of a point, AND concentrated there:
        the turn across a quarter-width window must be most of the turn
        across the full-width window. That second test is what separates a
        corner from a tight bowl — an R bowl of 2mm radius turns 90° over a
        full-width window, but only a quarter of that over the short one,
        and a bowl is exactly what a satin column follows well. Halves
        shorter than ~1.2 widths are not cut off: the end cap of the other
        half already covers a corner that close to a stroke end."""
        cfg = self.cfg
        if cfg.corner_split_deg <= 0:
            return
        threshold = np.radians(cfg.corner_split_deg)
        n_cut = 0
        for _ in range(cfg.corner_split_max_splits):
            split_at = None
            for k, (pts, w, method, rails, extra) in enumerate(polylines):
                if method != "column" or not extra or "piece" not in extra:
                    continue
                if extra.get("direct_ring"):
                    continue
                piece = np.array(extra["piece"], float)
                if len(piece) < 8:
                    continue
                seg = np.hypot(*(piece[1:] - piece[:-1]).T)
                s_arc = np.concatenate([[0.0], np.cumsum(seg)])
                w_px = max(3.0, w * self.px_per_mm)
                long_w, short_w = w_px, max(2.0, w_px / 4.0)
                min_half = 1.2 * w_px

                def turn_at(i, win, _piece=piece, _s=s_arc):
                    return self._piece_turn(_piece, _s, i, win)

                # A hook — the stub of a stem left on the diagonal of an N
                # where the skeleton merged stem-top into diagonal — is a
                # hard corner with one half shorter than a stroke width.
                # Left in, the whole column fans round it; cut off, it is a
                # short parallel column that reads as the stem's end. So a
                # turn of 90° or more may cut down to 0.6 widths.
                hook_half = 0.6 * w_px
                best_i, best_turn = None, threshold
                for i in range(1, len(piece) - 1):
                    half = min(s_arc[i], s_arc[-1] - s_arc[i])
                    if half < hook_half:
                        continue
                    t_long = turn_at(i, long_w)
                    if t_long < best_turn:
                        continue
                    if half < min_half and t_long < np.radians(90.0):
                        continue
                    if turn_at(i, short_w) < 0.6 * t_long:
                        continue                # a bowl, not a corner
                    best_i, best_turn = i, t_long
                if best_i is not None:
                    split_at = (k, best_i, best_turn)
                    break
            if split_at is None:
                break
            k, m, turn = split_at
            self._cut_piece(polylines, k, m)
            n_cut += 1
        if n_cut:
            print(f"corner split: {n_cut} cut(s) at turns >= "
                  f"{cfg.corner_split_deg:.0f}° — straight strokes now sew as "
                  f"their own columns")

    def _cut_piece(self, polylines, k, m):
        """Replace polylines[k] with its two halves cut at piece index m."""
        pts, w, method, rails, extra = polylines[k]
        tstart, tend = extra.get("taper", (0.0, 0.0))
        rebuilt = []
        for half, t0, t1 in ((extra["piece"][:m + 1], tstart, 0.0),
                             (extra["piece"][m:], 0.0, tend)):
            straight, _ = self._straighten(list(half))
            rebuilt.append((
                self.rdp(straight, self.rdp_eps_px),
                2 * float(np.mean([self._edt_at(p) for p in half]))
                * self.mm_per_px,
                "column",
                self._tapered_column(list(half), t0, t1),
                {"piece": straight, "taper": (t0, t1)},
            ))
        polylines[k:k + 1] = rebuilt

    def _piece_turn(self, piece, s_arc, i, win):
        """Angle between the chords `win` px before and after piece[i]."""
        a = int(np.searchsorted(s_arc, s_arc[i] - win))
        b = int(np.searchsorted(s_arc, s_arc[i] + win)) - 1
        b = min(len(piece) - 1, max(b, i + 1))
        a = max(0, min(a, i - 1))
        d1 = piece[i] - piece[a]
        d2 = piece[b] - piece[i]
        n1, n2 = np.hypot(*d1), np.hypot(*d2)
        if n1 < 1e-9 or n2 < 1e-9:
            return 0.0
        c = float(np.dot(d1, d2) / (n1 * n2))
        return float(np.arccos(max(-1.0, min(1.0, c))))

    def _concave_split(self, polylines):
        """Cut curving branches where the ink boundary has a concave corner
        (see TraceConfig.concave_split_deg)."""
        cfg = self.cfg
        if cfg.concave_split_deg <= 0:
            return
        r = max(1, int(round(cfg.concave_split_radius_mm * self.px_per_mm)))
        yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
        disc = (yy ** 2 + xx ** 2) <= r * r
        wedges = ndimage.binary_closing(self.ink, disc, border_value=0) & ~self.ink
        lbl, n = ndimage.label(wedges, structure=np.ones((3, 3)))
        if not n:
            return
        corners = ndimage.center_of_mass(wedges, lbl, range(1, n + 1))
        sizes = ndimage.sum(wedges, lbl, range(1, n + 1))
        corners = [c for c, a in zip(corners, sizes) if a >= 4]
        threshold = np.radians(cfg.concave_split_deg)
        n_cut = 0
        for cy, cx in corners:
            best = None
            for k, (pts, w, method, rails, extra) in enumerate(polylines):
                if method != "column" or not extra or "piece" not in extra:
                    continue
                if extra.get("direct_ring"):
                    continue
                piece = np.array(extra["piece"], float)
                if len(piece) < 8:
                    continue
                d = np.hypot(piece[:, 0] - cy, piece[:, 1] - cx)
                i = int(np.argmin(d))
                w_px = max(3.0, w * self.px_per_mm)
                if d[i] > 1.0 * w_px:
                    continue
                if best is None or d[i] < best[0]:
                    best = (d[i], k, i, w_px)
            if best is None:
                continue
            _, k, i, w_px = best
            piece = np.array(polylines[k][4]["piece"], float)
            seg = np.hypot(*(piece[1:] - piece[:-1]).T)
            s_arc = np.concatenate([[0.0], np.cumsum(seg)])
            if s_arc[i] < 1.2 * w_px or s_arc[-1] - s_arc[i] < 1.2 * w_px:
                continue
            if self._piece_turn(piece, s_arc, i, w_px) < threshold:
                continue
            self._cut_piece(polylines, k, i)
            n_cut += 1
        if n_cut:
            print(f"concave split: {n_cut} cut(s) where a curving branch "
                  f"passes a concave ink corner")

    def _straight_rect_mask(self, piece, w):
        """Constant-width rectangle along a straight piece, extended while
        its full width is still inside the ink and clipped to the ink; None
        if the piece bends.

        Straightness and direction are read from the MIDDLE of the piece: a
        medial-axis run always curls toward the junction in its last half
        width or so, and that curl is not a curve in the stroke. Extension
        runs while both rails are still in ink, so a stem reaches the top of
        its letter but a diagonal stops once it pokes out of the stem it
        joins — the overlap is set by the geometry, not by a fixed cap."""
        p = np.array(piece, float)
        if len(p) < 3:
            return None
        seg = np.hypot(*(p[1:] - p[:-1]).T)
        s_arc = np.concatenate([[0.0], np.cumsum(seg)])
        total = s_arc[-1]
        w_px = max(3.0, w * self.px_per_mm)
        if total < 1.5 * w_px:
            return None
        # resample so a five-point RDP piece gets a real middle
        n = max(16, int(total / 2.0))
        si = np.linspace(0.0, total, n)
        rp = np.stack([np.interp(si, s_arc, p[:, 0]), np.interp(si, s_arc, p[:, 1])], 1)
        lo, hi = int(0.15 * n), int(0.85 * n)
        mid = rp[lo:hi]
        a, b = mid[0], mid[-1]
        ab = b - a
        L = float(np.hypot(*ab))
        if L < 1e-6:
            return None
        u = ab / L
        dev = np.abs(ab[0] * (mid - a)[:, 1] - ab[1] * (mid - a)[:, 0]) / L
        if dev.max() > 0.12 * w_px:
            return None
        h = float(np.median([self._edt_at(q) for q in mid]))
        if h < 1.0:
            return None
        nrm = np.array([-u[1], u[0]])

        def in_ink(q):
            yq, xq = int(round(q[0])), int(round(q[1]))
            return (0 <= yq < self.ink.shape[0] and 0 <= xq < self.ink.shape[1]
                    and bool(self.ink[yq, xq]))

        def extend(pt, direction):
            out = pt.copy()
            for step in np.arange(1.0, 1.5 * w_px, 1.0):
                q = pt + direction * step
                if not (in_ink(q) and in_ink(q + nrm * 0.9 * h)
                        and in_ink(q - nrm * 0.9 * h)):
                    break
                out = q
            return out

        a2, b2 = extend(a, -u), extend(b, u)
        poly = [tuple(v[::-1]) for v in (a2 + nrm * h, b2 + nrm * h,
                                         b2 - nrm * h, a2 - nrm * h)]
        canvas = Image.new("1", (self.ink.shape[1], self.ink.shape[0]), 0)
        ImageDraw.Draw(canvas).polygon(poly, fill=1)
        return np.array(canvas, dtype=bool) & self.ink, L * 2 * h

    def _counter_wrap_split(self, polylines):
        """Cut any branch that curves back around its own counter.

        A branch fill is hole-filled before potrace, because fill_to_satin
        silently refuses a shape with a hole.  That is safe only while
        counters live BETWEEN branches, which is how the skeleton usually
        splits a bowl.  Where one run wraps the counter instead — the B of
        this BEACH BABY brush script, whose bowl returns to its own stem —
        the filled blob hands fill_to_satin the counter as solid ink and the
        satin stitches it closed.  Cutting the run at the middle of its wrap
        puts the counter back between two branches."""
        cfg = self.cfg
        for _ in range(cfg.counter_wrap_max_splits):
            assigned = self._assign_ink(polylines)
            split_at = None
            for k, (pts, w, method, rails, extra) in enumerate(polylines):
                if method != "column" or not extra or "piece" not in extra:
                    continue
                if extra.get("direct_ring"):
                    continue
                found = self._enclosed_counter(assigned == k + 1)
                if found is None:
                    continue
                (cy, cx), area = found
                piece = extra["piece"]
                ang = np.unwrap(np.arctan2(
                    np.array([p[0] for p in piece]) - cy,
                    np.array([p[1] for p in piece]) - cx))
                sweep = abs(ang[-1] - ang[0])
                if sweep < cfg.counter_wrap_rad:
                    continue                    # touches a counter, does not wrap it
                mid = ang[0] + (ang[-1] - ang[0]) / 2.0
                m = int(np.argmin(np.abs(ang - mid)))
                if min(m + 1, len(piece) - m) < 4:
                    continue
                split_at = (k, m, area / self.px_per_mm ** 2, sweep)
                break
            if split_at is None:
                return
            k, m, area_mm2, sweep = split_at
            pts, w, method, rails, extra = polylines[k]
            tstart, tend = extra.get("taper", (0.0, 0.0))
            halves = [(piece_half, t0, t1) for piece_half, t0, t1 in (
                (polylines[k][4]["piece"][:m + 1], tstart, 0.0),
                (polylines[k][4]["piece"][m:], 0.0, tend))]
            rebuilt = []
            for half, t0, t1 in halves:
                straight, _ = self._straighten(list(half))
                rebuilt.append((
                    self.rdp(straight, self.rdp_eps_px),
                    2 * float(np.mean([self._edt_at(p) for p in half]))
                    * self.mm_per_px,
                    "column",
                    self._tapered_column(list(half), t0, t1),
                    {"piece": straight, "taper": (t0, t1)},
                ))
            polylines[k:k + 1] = rebuilt
            print(f"counter-wrap split: branch {k} wrapped a "
                  f"{area_mm2:.1f} mm^2 counter over {np.degrees(sweep):.0f}° "
                  f"— cut into 2 branches")
        print(f"!! counter-wrap split did not converge in "
              f"{cfg.counter_wrap_max_splits} passes — a branch still encloses "
              f"a counter and will stitch it closed")

    def _build_outline(self, polylines):
        """Attach outline-mode extras to every satin column item: the branch
        fill (potrace d), its rungs, mean width, and the branch mask for the
        coverage/finish renders. Assignment: every ink pixel goes to the
        nearest piece's skeleton run, so branch masks tile the ink with no
        orphans; each column mask is then edge-smoothed (roughened-font
        deckle) and dilated into its junctions so adjacent columns overlap
        the way pro serif mini-columns overlap stems. A column whose mask or
        rungs fail keeps rails-only extras = None and falls back to the
        legacy skeleton satin at write time."""
        cfg = self.cfg
        self._corner_split(polylines)
        self._concave_split(polylines)
        self._counter_wrap_split(polylines)
        assigned = self._assign_ink(polylines)
        n_rect = 0

        overlap_it = max(1, int(round(cfg.branch_overlap_mm * self.px_per_mm)))
        n_ok = n_fallback = 0
        for k, (pts, w, method, rails, extra) in enumerate(polylines):
            if method != "column":
                continue
            if extra and extra.get("direct_ring"):
                # Closed outline loops already carry their paired true
                # boundaries as rails.  fill_to_satin would split them back
                # into cusp fragments, so preserve the direct column even
                # when the rest of the job uses outline mode.
                continue
            mask = assigned == k + 1
            if cfg.straight_rect_cols and extra and "piece" in extra:
                found = self._straight_rect_mask(extra["piece"], w)
                # Only where it helps: a stem whose nearest-skeleton mask
                # already IS its rectangle gains nothing and pays a second
                # layer at every junction (measured +4% stitches, clump
                # 36->41 when every straight piece was swapped). The case
                # that fans is a mask cut OBLIQUELY where the stroke meets a
                # neighbour — the stems of an N lose a slanted slice to the
                # diagonal's skeleton and fill_to_satin fans between that
                # slanted end and the perpendicular interior. Swap in the
                # rectangle only when the mask is missing a real share of it.
                if found is not None:
                    rect, _ = found
                    if rect.any() and (
                            (rect & ~mask).sum() >= 0.25 * rect.sum()
                            or (mask & ~rect).sum() >= 0.25 * mask.sum()):
                        mask = rect
                        n_rect += 1
            if not mask.any():
                polylines[k] = (pts, w, method, rails, None)
                n_fallback += 1
                continue
            # Idealize the branch edge with a gaussian level-set: blur +
            # re-threshold at 0.5 rounds bumps and notches symmetrically
            # with no net erosion, so branch ENDS keep their junction
            # contact (morphological opening retracted ends ~0.3mm, broke
            # the auto_satin chains, and clip-dilating back re-absorbed the
            # deckle it was meant to remove).
            sigma = 0.5 * cfg.outline_smooth_mm * self.px_per_mm
            mask = ndimage.gaussian_filter(
                mask.astype(np.float32), sigma=sigma) > 0.5
            mask = ndimage.binary_dilation(
                mask, np.ones((3, 3)), iterations=overlap_it) & self.ink
            # A fill with a hole is a shape fill_to_satin silently refuses to
            # convert, so potrace always gets the holeless mask.  Rung probing
            # does NOT: most holes inside one branch are deckle noise, but a
            # bowl that curves back to its own stem (the B of BEACH BABY)
            # encloses a real counter within a single branch, and probing the
            # filled mask reads straight across it — a 3mm stroke then gets an
            # 8mm rung and the satin closes the counter.
            filled = ndimage.binary_fill_holes(mask)
            holes = filled & ~mask
            counters = np.zeros_like(filled)
            hole_lbl, n_holes = ndimage.label(holes)
            if n_holes:
                min_counter_px = (cfg.counter_hole_mm * self.px_per_mm) ** 2
                sizes = ndimage.sum(holes, hole_lbl, range(1, n_holes + 1))
                for hole_index, size in enumerate(sizes, start=1):
                    if size >= min_counter_px:
                        counters |= hole_lbl == hole_index
            mask = filled
            if mask.sum() < (0.5 * self.px_per_mm) ** 2:
                polylines[k] = (pts, w, method, rails, None)
                n_fallback += 1
                continue
            sl = ndimage.find_objects(mask.astype(np.int8))[0]
            pad = 2
            r0 = max(0, sl[0].start - pad)
            r1 = min(mask.shape[0], sl[0].stop + pad)
            c0 = max(0, sl[1].start - pad)
            c1 = min(mask.shape[1], sl[1].stop + pad)
            sub = mask[r0:r1, c0:c1]
            sub_rung = sub & ~counters[r0:r1, c0:c1]

            def inside(py, px_, _sub=sub_rung, _r0=r0, _c0=c0):
                yy, xx = int(round(py - _r0)), int(round(px_ - _c0))
                return (0 <= yy < _sub.shape[0] and 0 <= xx < _sub.shape[1]
                        and bool(_sub[yy, xx]))

            rungs = self._synth_rungs(extra["piece"], inside)
            if rungs is None:
                polylines[k] = (pts, w, method, rails, None)
                n_fallback += 1
                continue
            d = self._potrace_branch(sub, r0, c0)
            # col_w is the TRUE ink width; the min_col_mm floor is applied at
            # stitch time via pull compensation (fillsatin.py width tiers) so
            # the traced outline stays crisp and counters stay open.
            polylines[k] = (pts, w, method, rails, {
                "outline_d": d, "rungs": rungs, "col_w": float(w),
                "mask": (r0, c0, sub)})
            n_ok += 1

        self._apply_rung_overrides(polylines)
        for k, (pts, w, method, rails, extra) in enumerate(polylines):
            if method == "bean" and extra is not None:
                polylines[k] = (pts, w, method, rails, None)
        print(f"outline mode: {n_ok} branches converted to fills "
              f"({n_rect} straight strokes as rectangles), "
              f"{n_fallback} fell back to skeleton rails")

    def _apply_rung_overrides(self, polylines):
        """Sidecar rung edits (design mm): drop_rungs removes the nearest
        synthesized rung within 1mm of the given point; add_rungs appends a
        hand-placed rung to whichever branch mask contains its midpoint."""
        for gx, gy in self.overrides["drop_rungs"]:
            best, best_d = None, 1.0
            for k, (*_, extra) in enumerate(polylines):
                if not extra or "rungs" not in extra:
                    continue
                for ri, (a, b) in enumerate(extra["rungs"]):
                    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                    dd = np.hypot(mid[0] - gx, mid[1] - gy)
                    if dd < best_d:
                        best, best_d = (k, ri), dd
            if best:
                polylines[best[0]][4]["rungs"].pop(best[1])
        for x1, y1, x2, y2 in self.overrides["add_rungs"]:
            my, mx = self.px(((x1 + x2) / 2, (y1 + y2) / 2))[::-1]
            for k, (*_, extra) in enumerate(polylines):
                if not extra or "mask" not in extra:
                    continue
                r0, c0, sub = extra["mask"]
                yy, xx = int(round(my - r0)), int(round(mx - c0))
                if (0 <= yy < sub.shape[0] and 0 <= xx < sub.shape[1]
                        and sub[yy, xx]):
                    extra["rungs"].append(((x1, y1), (x2, y2)))
                    break

    # ------------------------------------------------------------ routing
    def _order_by_glyph(self, polylines):
        """Finish one connected ink component before starting the next. A
        global nearest-neighbour walk hops to whatever is closest in space —
        regularly a different letter across a gap, and travel across a gap
        can't replace a trim. Per-glyph routing is what turns a trim per
        stroke into a trim per glyph (pro reference: 18 trims, 17 glyphs)."""
        ink_lbl, n_ink = ndimage.label(self.ink, structure=np.ones((3, 3)))

        def component(pts):
            ids = [ink_lbl[int(round(p[0])), int(round(p[1]))] for p in pts]
            ids = [i for i in ids if i > 0]
            return max(set(ids), key=ids.count) if ids else 0

        groups = {}
        for item in polylines:
            groups.setdefault(component(item[0]), []).append(item)
        print(f"glyph components: {len(groups)} (ink components: {n_ink})")

        def chain(items, start):
            out, remaining, cur = [], list(items), start
            while remaining:
                best_i, best_rev, best_d = 0, False, 1e18
                for i, (pts, w, m, ex, extra) in enumerate(remaining):
                    for rev in (False, True):
                        p = pts[-1] if rev else pts[0]
                        d = (p[0] - cur[0]) ** 2 + (p[1] - cur[1]) ** 2
                        if d < best_d:
                            best_i, best_rev, best_d = i, rev, d
                pts, w, m, ex, extra = remaining.pop(best_i)
                if best_rev:
                    pts = pts[::-1]
                    if ex is not None:
                        ex = [r[::-1] for r in ex]
                out.append((pts, w, m, ex, extra))
                cur = pts[-1]
            return out, cur

        ordered, glyph_ids, pending, cur = [], [], dict(groups), (self.y0, self.x0)
        while pending:
            key = min(pending, key=lambda k: min(
                (p[0] - cur[0]) ** 2 + (p[1] - cur[1]) ** 2
                for pts, *_ in pending[k] for p in (pts[0], pts[-1])))
            chunk, cur = chain(pending.pop(key), cur)
            ordered.extend(chunk)
            glyph_ids.extend([key] * len(chunk))
        self.glyph_ids = glyph_ids
        return ordered

    def _underpath(self, a, b):
        """Least-cost travel from a to b THROUGH the ink, hugging the medial
        axis (cost 1 + 6/(edt+1) inside, prohibitive outside) so later
        stitching buries it. None if it can't stay inside or runs too long."""
        pad = int(4 * self.px_per_mm)
        r0 = max(0, min(a[0], b[0]) - pad)
        r1 = min(self.ink.shape[0], max(a[0], b[0]) + pad)
        c0 = max(0, min(a[1], b[1]) - pad)
        c1 = min(self.ink.shape[1], max(a[1], b[1]) + pad)
        sub = self._travel_cost[r0:r1, c0:c1]
        try:
            idx, _ = route_through_array(
                sub, (a[0] - r0, a[1] - c0), (b[0] - r0, b[1] - c0),
                fully_connected=True, geometric=True)
        except ValueError:
            return None
        path = [(p[0] + r0, p[1] + c0) for p in idx]
        if not all(self.ink[p] for p in path):
            return None
        length = sum(np.hypot(path[i + 1][0] - path[i][0],
                              path[i + 1][1] - path[i][1])
                     for i in range(len(path) - 1)) * self.mm_per_px
        if length > self.cfg.trim_hop_mm * 2.5:
            return None
        step = max(1, len(path) // max(2, int(length / 1.2)))   # ~1.2mm running
        thinned = path[::step]
        if thinned[-1] != path[-1]:
            thinned.append(path[-1])
        return thinned

    def _trim_policy(self):
        cfg = self.cfg
        self._travel_cost = np.where(self.ink, 1.0 + 6.0 / (self.edt + 1.0), 1e4)
        ordered = self.ordered
        self.no_trim = [False] * len(ordered)
        self.connector = [None] * len(ordered)
        n_direct = n_routed = 0
        for i in range(len(ordered) - 1):
            ay, ax = ordered[i][0][-1]
            by, bx = ordered[i + 1][0][0]
            hop = float(np.hypot(by - ay, bx - ax)) * self.mm_per_px
            if hop > cfg.trim_hop_mm:
                continue
            steps = max(2, int(np.hypot(by - ay, bx - ax)))
            ts = np.linspace(0, 1, steps)
            yy = np.clip(np.round(ay + (by - ay) * ts).astype(int),
                         0, self.ink.shape[0] - 1)
            xx = np.clip(np.round(ax + (bx - ax) * ts).astype(int),
                         0, self.ink.shape[1] - 1)
            if (self.ink[yy, xx].mean() >= cfg.inside_frac
                    and hop <= cfg.max_float_mm):
                self.no_trim[i] = True          # straight hop already hidden
                n_direct += 1
                continue
            # Buried but too long to sew in one move: fall through to the
            # underpath, which lays the same travel as running stitches.
            a = (int(round(ay)), int(round(ax)))
            b = (int(round(by)), int(round(bx)))
            if self.ink[a] and self.ink[b]:
                path = self._underpath(a, b)
                if path and len(path) > 2:
                    self.connector[i] = path    # travel along the letterform
                    self.no_trim[i] = True
                    n_routed += 1
        print(f"trims suppressed on {sum(self.no_trim)} of {len(ordered)} hops "
              f"({n_direct} direct, {n_routed} underpath-routed)")

    # ------------------------------------------------------------ pipeline
    def trace(self):
        cfg = self.cfg
        outline = cfg.satin_mode == "outline"
        edges = self._build_edges()
        polylines = []
        n_bean = n_col = n_stub = 0
        force_bean_px = [self.px((x, y)) for x, y in self.overrides["force_bean"]]
        for e in edges:
            if len(e) < 4:
                continue
            ring = self._closed_ring_data.get(id(e))
            if ring is not None:
                # A pure outline loop is already the ideal satin model: its
                # paired true boundaries are the rails.  Do not feed it back
                # through width splitting or normal-offset EDT rails, both of
                # which reintroduce cusp branches and local coverage gaps.
                step = max(1, len(e) // 512)
                pts = list(e[::step])
                if pts[-1] != pts[0]:
                    pts.append(pts[0])
                # Force-bean sidecar edits must also apply to stabilized
                # loops.  This is how a tiny, acute counter (for example the
                # eye of an R) can drop from satin to a three-pass outline
                # without giving up loop stabilization everywhere else.
                forced_bean = any(
                    min(np.hypot(p[1] - fx, p[0] - fy) for p in e)
                    < self.px_per_mm
                    for fx, fy in force_bean_px
                )
                if forced_bean:
                    polylines.append((
                        pts,
                        min(0.50, ring["source_width_mm"]),
                        "bean",
                        None,
                        None,
                    ))
                    n_bean += 1
                    continue
                rails = [[self.to_mm(p[1], p[0]) for p in rail]
                         for rail in ring["rails_px"]]
                ring_extra = {"direct_ring": True} if outline else None
                polylines.append((pts, ring["width_mm"], "column", rails,
                                  ring_extra))
                n_col += 1
                continue
            for piece, hairline, tstart, tend in self._split_by_width(e):
                if len(piece) < 4:
                    continue
                w = 2 * float(np.mean([self._edt_at(p) for p in piece])) * self.mm_per_px
                L = self._run_len_mm(piece)
                piece, _ = self._straighten(piece)
                pts = self.rdp(piece, self.rdp_eps_px)
                if not hairline and any(
                        min(np.hypot(p[1] - fx, p[0] - fy) for p in piece)
                        < self.px_per_mm
                        for fx, fy in force_bean_px):
                    hairline = True     # sidecar force_bean
                # A run shorter than it is wide is the leftover of a junction:
                # its rails bow into a lens and stitch as a bead. Drop it — the
                # branches meeting there already overlap.
                if (L < 1.15 * w and not hairline) or L < 0.5:
                    n_stub += 1
                    continue
                extra = ({"piece": piece, "taper": (tstart, tend)}
                         if outline else None)
                if hairline:
                    polylines.append((pts, w, "bean", None, extra))
                    n_bean += 1
                else:
                    polylines.append((pts, w, "column",
                                      self._tapered_column(piece, tstart, tend),
                                      extra))
                    n_col += 1
        print(f"pieces: {len(polylines)} ({n_col} satin columns, {n_bean} bean "
              f"hairlines, {n_stub} junction stubs dropped)")
        if outline:
            self._build_outline(polylines)
        self.ordered = self._order_by_glyph(polylines)
        self._trim_policy()
        return self

    # ------------------------------------------------------------ output
    @staticmethod
    def catmull_bezier_d(pts):
        """Catmull-Rom through pts as absolute cubic beziers (curve commands
        also keep the downstream polyline smoothing pass off the rails)."""
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

    def write_svg(self, out_path):
        cfg = self.cfg
        W_MM = cfg.target_w_mm + 2 * cfg.margin_mm
        H_MM = self.bbox_h * self.mm_per_px + 2 * cfg.margin_mm
        profile_attr = (f' data-audit-profile="{cfg.audit_profile}"'
                        if cfg.audit_profile else "")
        svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:inkstitch="http://inkstitch.org/namespace" '
            f'width="{W_MM:.2f}mm" height="{H_MM:.2f}mm" '
            f'viewBox="0 0 {W_MM:.2f} {H_MM:.2f}"{profile_attr}>',
            '  <g id="wordmark">',
        ]
        outline = cfg.satin_mode == "outline"
        open_glyph = None
        for i, (pts, w, method, rails, extra) in enumerate(self.ordered):
            converted = (outline and method == "column" and extra is not None
                         and not extra.get("direct_ring"))
            if outline:
                gid = self.glyph_ids[i] if i < len(self.glyph_ids) else 0
                if gid != open_glyph:
                    if open_glyph is not None:
                        svg.append("    </g>")
                    svg.append(f'    <g id="glyph{gid}" data-satin-mode="fill" '
                               f'data-glyph="{gid}">')
                    open_glyph = gid
            ind = "      " if outline else "    "
            trim_attr = (f' data-trim-after="false" '
                         f'data-min-jump-mm="{self.no_trim_jump_mm}"'
                         if self.no_trim[i] else "")
            closed_column = (method == "column" and rails is not None
                             and all(len(r) > 2 and np.hypot(
                                 r[0][0] - r[-1][0], r[0][1] - r[-1][1]) < 0.05
                                     for r in rails))
            if closed_column:
                # A loop has no exposed terminal and needs no edge-to-edge
                # zigzag underlay.  Center walk stabilizes it without laying
                # a second set of penetrations directly under both satin
                # rails — important at tight inside curves and cusps.
                fine_attr = ' data-satin-underlay="center"'
            elif method == "column" and w < cfg.narrow_col_mm:
                fine_attr = (f' data-satin-underlay="center" '
                             f'data-pull-comp-mm="{cfg.narrow_pull_comp_mm}"')
            elif method == "column" and w < cfg.contour_col_mm:
                # A full zigzag underlay was the remaining source of endpoint
                # piles in traced wordmarks. Center walk supports ordinary
                # columns without laying another satin-like pass under every
                # junction; this matches the validated fill-to-satin tiers.
                fine_attr = ' data-satin-underlay="center"'
            elif method == "column":
                fine_attr = ' data-satin-underlay="center+contour"'
            else:
                fine_attr = ""
            # Ink/Stitch's requested spacing is measured along the centerline,
            # while the audit measures same-rail advance. The outside rail of
            # a broad curve stretches a nominal 0.40mm pitch to ~0.45mm. Ask
            # for 0.38mm only on broad columns; keeping narrow satin at 0.40mm
            # avoids pushing tight lettering over the 1mm penetration ceiling.
            spacing_attr = (
                f' data-satin-spacing-mm="{cfg.wide_satin_spacing_mm:.2f}"'
                if method == "column" and w >= cfg.contour_col_mm else ""
            )
            if method == "bean":
                mm = [self.to_mm(p[1], p[0]) for p in pts]
                d = (self.catmull_bezier_d(mm) if len(mm) > 2 else
                     "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in mm))
                sw = min(0.9, max(0.35, w))
                svg.append(
                    f'{ind}<path id="line{i}" style="fill:none;stroke:#000000;'
                    f'stroke-width:{sw:.2f};stroke-linecap:round" '
                    f'data-stroke-method="bean_stitch" '
                    f'data-bean-repeats="{2 if w > 0.5 else 1}" '
                    f'data-smoothed="true"{trim_attr} d="{d}"/>'
                )
            elif converted:
                # Branch fill + rungs; stitch_cli/fillsatin.py turns these into
                # satin columns via Ink/Stitch fill_to_satin, then auto_satin
                # routes the glyph. Trim policy is stamped there, post-routing.
                svg.append(
                    f'{ind}<path id="line{i}" class="satin-fill" '
                    f'data-col-w-mm="{extra["col_w"]:.2f}" '
                    f'style="fill:#000000;stroke:none" d="{extra["outline_d"]}"/>'
                )
                for ri, (a, b) in enumerate(extra["rungs"]):
                    svg.append(
                        f'{ind}<path id="line{i}r{ri}" class="rung" '
                        f'data-for="line{i}" '
                        f'style="fill:none;stroke:#00ff00;stroke-width:0.2" '
                        f'd="M {a[0]:.2f} {a[1]:.2f} L {b[0]:.2f} {b[1]:.2f}"/>'
                    )
                # Invisible centerline: if fill_to_satin refuses this branch
                # (it silently skips some degenerate thin shapes) fillsatin.py
                # promotes this into a bean hairline instead of an auto_fill.
                cl = [self.to_mm(p[1], p[0]) for p in pts]
                cld = (self.catmull_bezier_d(cl) if len(cl) > 2 else
                       "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in cl))
                svg.append(
                    f'{ind}<path id="line{i}fb" class="satin-fallback" '
                    f'data-for="line{i}" data-col-w-mm="{extra["col_w"]:.2f}" '
                    f'style="fill:none;stroke:none" d="{cld}"/>'
                )
            else:
                r1, r2 = rails
                d = self.catmull_bezier_d(r1) + " " + self.catmull_bezier_d(r2)
                svg.append(
                    f'{ind}<path id="line{i}" style="fill:none;stroke:#000000;'
                    f'stroke-width:0.26" '
                    f'inkstitch:satin_column="true"{trim_attr}{fine_attr}'
                    f'{spacing_attr} d="{d}"/>'
                )
            # In outline mode auto_satin re-routes the columns inside a glyph,
            # so a precomputed connector only stays valid between two beans.
            emit_conn = self.connector[i] is not None and (
                not outline or (method == "bean" and i + 1 < len(self.ordered)
                                and self.ordered[i + 1][2] == "bean"))
            if emit_conn:
                cm = [self.to_mm(p[1], p[0]) for p in self.connector[i]]
                cd = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in cm)
                svg.append(
                    f'{ind}<path id="travel{i}" style="fill:none;stroke:#000000;'
                    f'stroke-width:0.25" '
                    f'data-stroke-method="running_stitch" data-trim-after="false" '
                    f'data-min-jump-mm="{self.no_trim_jump_mm}" d="{cd}"/>'
                )
        if open_glyph is not None:
            svg.append("    </g>")
        svg += ["  </g>", "</svg>"]
        if outline:
            self._write_ink_rle(str(out_path) + ".ink.rle")
        with open(out_path, "w") as f:
            f.write("\n".join(svg))
        print(f"wrote {out_path}  ({W_MM:.1f} x {H_MM:.1f} mm, "
              f"{len(self.ordered)} paths)")

    def _write_ink_rle(self, path):
        """Downsampled ink mask as run-length text for stitch_cli/fillsatin.py
        (the project venv has no image libs, so no PNG). Max-pooled to
        ~0.25mm/px so thin ink never vanishes. Header:
        W H ds x0 y0 mm_per_px margin_mm; then one line of start:len runs
        per grid row."""
        ds = max(1, int(round(0.25 * self.px_per_mm)))
        H, W = self.ink.shape
        Hc, Wc = H - H % ds, W - W % ds
        small = (self.ink[:Hc, :Wc]
                 .reshape(Hc // ds, ds, Wc // ds, ds).max(axis=(1, 3)))
        lines = [f"{small.shape[1]} {small.shape[0]} {ds} {self.x0} {self.y0} "
                 f"{self.mm_per_px:.8f} {self.cfg.margin_mm}"]
        for row in small:
            edges_ = np.flatnonzero(np.diff(
                np.concatenate([[0], row.astype(np.int8), [0]])))
            lines.append(" ".join(f"{s}:{e - s}"
                                  for s, e in zip(edges_[::2], edges_[1::2])))
        with open(path, "w") as f:
            f.write("\n".join(lines))
        print(f"wrote {path}")

    def coverage_report(self):
        """Rasterize what the pieces cover and diff against the ink. Tune
        against these numbers, not a stitch preview — 1mm junction holes are
        invisible at preview resolution."""
        cov = Image.new("1", (self.ink.shape[1], self.ink.shape[0]), 0)
        cd = ImageDraw.Draw(cov)
        mask_cov = np.zeros(self.ink.shape, bool)
        for pts, w, method, rails, extra in self.ordered:
            if method == "column" and extra is not None and "mask" in extra:
                r0, c0, sub = extra["mask"]     # exact branch fill coverage
                mask_cov[r0:r0 + sub.shape[0], c0:c0 + sub.shape[1]] |= sub
            elif method == "column":
                poly = ([self.px(p) for p in rails[0]]
                        + [self.px(p) for p in rails[1][::-1]])
                cd.polygon(poly, fill=1)
            else:
                cd.line([(p[1], p[0]) for p in pts], fill=1,
                        width=max(1, int(round(max(0.45, w) * self.px_per_mm))))
        covered = np.array(cov, bool) | mask_cov
        self.missed = self.ink & ~covered
        spill = covered & ~self.ink
        counters = ndimage.binary_fill_holes(self.ink) & ~self.ink
        print(f"coverage: {100 * (self.ink & covered).sum() / self.ink.sum():.2f}% "
              f"of ink stitched, "
              f"spill {100 * spill.sum() / self.ink.sum():.2f}% of ink area, "
              f"counter intrusion "
              f"{100 * (covered & counters).sum() / max(1, counters.sum()):.2f}% "
              f"of counter area")
        lbl, nlab = ndimage.label(self.missed)
        if nlab:
            sizes = ndimage.sum(self.missed, lbl, range(1, nlab + 1))
            order = np.argsort(sizes)[::-1][:8]
            shown = False
            for i in order:
                if sizes[i] * self.mm_per_px ** 2 < 0.15:
                    break
                if not shown:
                    print("  largest gaps (mm^2 @ x,y mm from design origin):")
                    shown = True
                cy_, cx_ = ndimage.center_of_mass(self.missed, lbl, i + 1)
                gx, gy = self.to_mm(cx_, cy_)
                print(f"    {sizes[i] * self.mm_per_px ** 2:6.2f} mm^2 "
                      f"at ({gx:6.1f}, {gy:5.1f})")

    def write_finish_png(self, path):
        """As-sewn render: filled rail polygons, the honest view of the sewn
        edge. (A needle-path render scallops every column edge with bite ends
        and reads jagged even when the rails are straight.)"""
        ss = 2
        big = Image.new("1", (self.ink.shape[1] * ss, self.ink.shape[0] * ss), 0)
        bd = ImageDraw.Draw(big)
        for pts, w, method, rails, extra in self.ordered:
            if method == "column" and extra is not None and "mask" in extra:
                r0, c0, sub = extra["mask"]
                up = np.kron(sub, np.ones((ss, ss), bool))
                arr = np.array(big, bool)
                arr[r0 * ss:r0 * ss + up.shape[0],
                    c0 * ss:c0 * ss + up.shape[1]] |= up
                big = Image.fromarray(arr)
                bd = ImageDraw.Draw(big)
            elif method == "column":
                bd.polygon([tuple(v * ss for v in self.px(p)) for p in rails[0]]
                           + [tuple(v * ss for v in self.px(p))
                              for p in rails[1][::-1]], fill=1)
            else:
                bd.line([(p[1] * ss, p[0] * ss) for p in pts], fill=1,
                        width=max(1, int(round(max(0.45, w) * self.px_per_mm * ss))),
                        joint="curve")
        fin = Image.fromarray((255 - np.array(big, np.uint8) * 235)).convert("L")
        fin = fin.resize((self.ink.shape[1], self.ink.shape[0]), Image.LANCZOS)
        fin.crop((max(0, self.x0 - 10), max(0, self.y0 - 10),
                  self.x1 + 10, self.y1 + 10)).save(path)
        print(f"wrote {path}")

    def write_debug_png(self, path):
        """Rails + centerlines over the ink, uncovered ink in red. Run
        coverage_report() first so the missed mask exists."""
        base = np.stack([(225 - self.ink * 45).astype(np.uint8)] * 3, axis=-1)
        if self.missed is not None:
            base[self.missed] = (255, 90, 90)
        dbg = Image.fromarray(base)
        draw = ImageDraw.Draw(dbg)
        for pts, w, method, rails, extra in self.ordered:
            if method == "column" and extra is not None and "rungs" in extra:
                for a, b in extra["rungs"]:
                    draw.line([self.px(a), self.px(b)], fill=(0, 170, 0), width=3)
                draw.line([(p[1], p[0]) for p in pts], fill=(0, 90, 255), width=1)
            elif method == "column":
                for rail in rails:
                    draw.line([self.px(p) for p in rail], fill=(255, 130, 0), width=2)
                draw.line([(p[1], p[0]) for p in pts], fill=(0, 90, 255), width=1)
            else:
                draw.line([(p[1], p[0]) for p in pts], fill=(230, 30, 200), width=2)
        dbg.crop((max(0, self.x0 - 10), max(0, self.y0 - 10),
                  self.x1 + 10, self.y1 + 10)).save(path)
        print(f"wrote {path}")
