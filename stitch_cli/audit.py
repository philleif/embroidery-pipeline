"""Quality audit for a finished .pes, scored against professionally digitized work.

The bands below are not invented — they are measured across the satin-heavy
files in ``out/pro-samples/`` (Philip Bjerknes' Cobble Hills, Desperate, Yearn,
and his version of the Paris Review masthead). Anything outside a band is
something a professional file does not do.

Two metrics do most of the discriminating, and they say the same thing from
different directions: **p10 stitch length** and **the narrowest satin width**.
Pro files simply contain no thin columns — every one of them floors around
1.0-1.5mm, which is why they carry almost no sub-0.8mm stitches. Our traces
tend to chase the artwork's hairlines below what thread can hold, and every
downstream symptom (beading, micro-stitches, thread breaks) follows from that.

Pure stdlib + pyembroidery on purpose, so it runs in the project venv.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import pyembroidery as pe

STITCH = 0
TRIM = getattr(pe, "TRIM", 2)

# (low, high, unit, why) — None means unbounded on that side.
BANDS = {
    "density":     (55.0, 155.0, "stitches/cm^2", "pro corpus 64-146"),
    # Whole-bounds density can hide a knot inside a sparse/outline design.
    # Scan 1mm cells at every 0.1mm phase so a hotspot cannot escape by
    # straddling a bin boundary or change score when the design is translated.
    # The pro corpus peaks at 9-15; the visibly
    # clumped hearts regression measured 25.
    "peak_1mm":    (None, 15.0, "penetrations in any 1mm cell", "pro corpus 9-15"),
    "satin_score": (68.0, None, "%", "pro corpus 70-79 on satin lettering"),
    "short_pct":   (None, 6.5, "% of stitches < 0.8mm", "pro corpus 1.8-5.5"),
    "p10_len":     (0.92, None, "mm", "pro corpus 0.98-1.08"),
    "satin_pitch": (0.30, 0.44, "mm", "pro corpus 0.32-0.40"),
    "satin_w10":   (1.00, None, "mm", "pro corpus 1.04-1.53 — the width floor"),
    "trims_per_1k": (None, 9.0, "trims per 1000 stitches", "pro corpus 3.6-8.3"),
}

# Opt-in per-design-class bands, enforced on top of BANDS via audit(profile=...).
# The default audit stays advisory on these metrics because they are meaningless
# on bean-heavy or fill-only designs (same caveat QUALITY-BAR.md applies to
# satin_pitch). Numbers measured on the Bjerknes Paris Review PES vs ours:
# clump 26 vs 53%, run 15.8 vs 5.5%, pitch_p90 0.41 vs 1.02, blocks 17 vs 23.
PROFILES = {
    "satin-wordmark": {
        "clump_pct":          (None, 35.0, "% penetrations near non-adjacent", "pro 26"),
        "pitch_p90":          (None, 0.50, "mm", "pro tail is tight: p90 0.41"),
        "pitch_over_08_pct":  (None, 3.0, "% of advances > 0.8mm", "pro ~0.5"),
        "run_pct":            (10.0, 25.0, "% stitches in underlay/travel runs", "pro 15.8"),
        "blocks":             (None, 25.0, "trim-separated stitch runs", "pro = 1/glyph (17)"),
    },
    # Closed satin borders intentionally surround large empty counters, so
    # whole-bounds density and the wordmark reversal ratio are not meaningful.
    # The actual safety/quality signals remain enforced.
    "satin-outline": {},
    "satin-lettering": {},
    "fill": {},
    "running": {},
    "mixed": {},
}

# Base-band subsets by design technique. peak_1mm is universal: regardless of
# technique, too many penetrations in one tiny area are a clump/thread-break
# risk. The other subsets avoid calling healthy fill/running work failed merely
# because it does not resemble satin lettering.
PROFILE_BASE_KEYS = {
    "satin-wordmark": set(BANDS),
    "satin-lettering": set(BANDS),
    "satin-outline": {
        "peak_1mm", "short_pct", "p10_len", "satin_pitch", "satin_w10",
        "trims_per_1k",
    },
    "fill": {"density", "peak_1mm", "short_pct", "p10_len", "trims_per_1k"},
    "running": {"peak_1mm", "trims_per_1k"},
    "mixed": {"peak_1mm", "trims_per_1k"},
}


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def measure(path: Path) -> dict:
    pat = pe.read(str(path))
    pts, trims = [], 0
    blocks, cur = [], []  # stitch runs separated by any non-stitch command
    for x, y, c in pat.stitches:
        c &= 0xFF
        if c == STITCH:
            pts.append((x / 10.0, y / 10.0))
            cur.append((x / 10.0, y / 10.0))
        else:
            if c == TRIM:
                trims += 1
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)

    if not pts:
        raise RuntimeError(f"{path} contains no stitch penetrations")

    # Quality metrics describe the sewn design, not positioning-only JUMPs.
    # `pattern.bounds()` includes phantom jumps inserted by `stitch offset`,
    # which used to make an unchanged design appear larger and less dense.
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w_mm = max(xs) - min(xs)
    h_mm = max(ys) - min(ys)
    area = max(w_mm * h_mm / 100.0, 1e-6)

    # Segment, satin-reversal, and thread-length calculations must never bridge
    # a JUMP/TRIM/COLOR_CHANGE.  The old flattened calculation treated the hop
    # from one object to the next as a sewn segment and could invent a reversal,
    # a huge satin width, and extra thread at every boundary.
    seg, widths, advances = [], [], []
    reversals = 0
    direction_pairs = 0
    for blk in blocks:
        bseg, unit = [], []
        for i in range(1, len(blk)):
            dx = blk[i][0] - blk[i - 1][0]
            dy = blk[i][1] - blk[i - 1][1]
            d = math.hypot(dx, dy)
            bseg.append(d)
            unit.append((dx / d, dy / d) if d > 0 else (0.0, 0.0))
        seg.extend(bseg)
        direction_pairs += max(0, len(unit) - 1)
        for i in range(len(unit) - 1):
            dot = unit[i][0] * unit[i + 1][0] + unit[i][1] * unit[i + 1][1]
            if dot < -0.5:
                reversals += 1
                # A satin bite's segment length approximates column width; the
                # distance over two stitches is its advance along the column.
                if bseg[i] > 0.4 and bseg[i + 1] > 0.4:
                    widths.append(bseg[i])
                    advances.append(math.hypot(blk[i + 2][0] - blk[i][0],
                                               blk[i + 2][1] - blk[i][1]))

    nz = [d for d in seg if d > 0]

    # Worst local penetration stack. PES coordinates are quantized to 0.1mm,
    # so all ten phases on both axes are exhaustive and translation-invariant
    # at file resolution. This catches independently capped satin fragments
    # converging on one cusp even when average stitches/cm² is perfectly sane.
    peak_1mm = 0
    peak_cell: tuple[float, float] | None = None
    for ix in range(10):
        for iy in range(10):
            ox, oy = ix / 10.0, iy / 10.0
            cells = Counter((math.floor(x - ox), math.floor(y - oy))
                            for x, y in pts)
            if cells:
                (cx, cy), count = cells.most_common(1)[0]
                if count > peak_1mm:
                    peak_1mm = count
                    peak_cell = (cx + ox, cy + oy)

    peak_blocks: list[tuple[int, int]] = []
    if peak_cell is not None:
        px, py = peak_cell
        for block_idx, block in enumerate(blocks, 1):
            count = sum(px <= x < px + 1 and py <= y < py + 1
                        for x, y in block)
            if count:
                peak_blocks.append((block_idx, count))

    # --- block-aware metrics (never cross a trim/jump boundary) ---
    # clump_pct: % of penetrations with another penetration within 0.3mm that
    # is not a near neighbor in stitch order — stacked/knotted thread.
    ordered_pts: list[tuple[float, float, int, int]] = []
    for block_idx, blk in enumerate(blocks):
        ordered_pts.extend((x, y, block_idx, seq_idx)
                           for seq_idx, (x, y) in enumerate(blk))
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (x, y, _, _) in enumerate(ordered_pts):
        grid.setdefault((int(x // 0.3), int(y // 0.3)), []).append(i)
    clumped = 0
    for i, (x, y, block_idx, seq_idx) in enumerate(ordered_pts):
        gx, gy = int(x // 0.3), int(y // 0.3)
        hit = False
        for cx in (gx - 1, gx, gx + 1):
            for cy in (gy - 1, gy, gy + 1):
                for j in grid.get((cx, cy), ()):
                    xj, yj, other_block, other_seq = ordered_pts[j]
                    non_adjacent = (other_block != block_idx or
                                    abs(other_seq - seq_idx) > 4)
                    if non_adjacent and (xj - x) ** 2 + (yj - y) ** 2 <= 0.09:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                break
        clumped += hit

    # run_pct: % of stitches inside straight-ish passages of >=4 consecutive
    # low-turn segments — underlay and in-block travel. Pro satin lettering
    # carries 12-16% of these; bare satin carries almost none.
    # pitch (block-aware): advance between successive same-rail penetrations
    # of a satin bite, for the tail metrics the median-only satin_pitch hides.
    run_st = 0
    badv = []
    for blk in blocks:
        cur_run = 0
        for i in range(1, len(blk) - 1):
            v1 = (blk[i][0] - blk[i - 1][0], blk[i][1] - blk[i - 1][1])
            v2 = (blk[i + 1][0] - blk[i][0], blk[i + 1][1] - blk[i][1])
            l1 = math.hypot(*v1)
            l2 = math.hypot(*v2)
            dot = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2) if l1 > 0 and l2 > 0 else 0.0
            if dot > 0.3:
                cur_run += 1
            else:
                if cur_run >= 4:
                    run_st += cur_run
                cur_run = 0
                if dot < -0.5 and l1 > 0.4 and l2 > 0.4:
                    badv.append(math.hypot(blk[i + 1][0] - blk[i - 1][0],
                                           blk[i + 1][1] - blk[i - 1][1]))
        if cur_run >= 4:
            run_st += cur_run

    return {
        "size_mm": (w_mm, h_mm),
        "stitches": len(pts),
        "thread_m": sum(seg) / 1000.0,
        "trims": trims,
        "density": len(pts) / area,
        "peak_1mm": peak_1mm,
        "peak_1mm_location": peak_cell,
        "peak_1mm_blocks": peak_blocks,
        "satin_score": 100.0 * reversals / max(1, direction_pairs),
        "short_pct": 100.0 * sum(1 for d in nz if d < 0.8) / max(1, len(nz)),
        "p10_len": _pct(nz, 10),
        "satin_pitch": _pct(advances, 50) if len(advances) > 50 else float("nan"),
        "satin_w10": _pct(widths, 10) if len(widths) > 50 else float("nan"),
        "satin_w50": _pct(widths, 50) if len(widths) > 50 else float("nan"),
        "satin_w90": _pct(widths, 90) if len(widths) > 50 else float("nan"),
        # Per-1k rates become noisy on tiny motifs (two necessary glyph trims
        # in 200 stitches looks worse than the pro ceiling). A 500-stitch
        # denominator floor still catches genuinely fragmented minis while
        # preventing one or two object boundaries from dominating the score.
        "trims_per_1k": 1000.0 * trims / max(500, len(pts)),
        "blocks": len(blocks),
        "clump_pct": 100.0 * clumped / max(1, len(pts)),
        "run_pct": 100.0 * run_st / max(1, len(pts)),
        "pitch_p90": _pct(badv, 90) if len(badv) > 50 else float("nan"),
        "pitch_over_08_pct": (100.0 * sum(1 for a in badv if a > 0.8) / len(badv)
                              if len(badv) > 50 else float("nan")),
    }


def audit(path: Path, verbose: bool = True,
          profile: str | None = None) -> tuple[dict, list[str]]:
    """Measure and score. Returns (metrics, failures).

    ``profile`` names an entry in PROFILES whose bands are enforced on top of
    BANDS; without one, the profile metrics are printed as advisory [info]
    rows so bean/fill designs are never failed on satin-only numbers.
    """
    m = measure(path)
    failures = []
    bands = dict(BANDS)
    advisory = set()
    if profile is not None:
        if profile not in PROFILES:
            raise KeyError(f"unknown audit profile {profile!r} "
                           f"(have: {', '.join(sorted(PROFILES))})")
        bands = {key: value for key, value in BANDS.items()
                 if key in PROFILE_BASE_KEYS[profile]}
        bands.update(PROFILES[profile])
    else:
        for pbands in PROFILES.values():
            for key, spec in pbands.items():
                if key not in bands:
                    bands[key] = spec
                    advisory.add(key)
    if verbose:
        w, h = m["size_mm"]
        print(f"{path.name}")
        print(f"  {w:.1f} x {h:.1f} mm   {m['stitches']} stitches   "
              f"{m['thread_m']:.1f} m thread   {m['trims']} trims   "
              f"{m['blocks']} blocks")
        if not math.isnan(m["satin_w10"]):
            print(f"  satin width  p10 {m['satin_w10']:.2f} / p50 {m['satin_w50']:.2f} "
                  f"/ p90 {m['satin_w90']:.2f} mm  "
                  f"(contrast {m['satin_w90'] / max(m['satin_w10'], 1e-6):.1f}:1)")
        if m["peak_1mm_location"] is not None:
            x, y = m["peak_1mm_location"]
            contributors = ", ".join(
                f"{block}:{count}" for block, count in m["peak_1mm_blocks"]
            )
            print(f"  local peak cell x={x:.1f}..{x + 1:.1f}, "
                  f"y={y:.1f}..{y + 1:.1f} mm"
                  f"  (block:penetrations {contributors})")
    for key, (lo, hi, unit, why) in bands.items():
        v = m.get(key)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        bad = (lo is not None and v < lo) or (hi is not None and v > hi)
        if bad and key not in advisory:
            failures.append(key)
        if verbose:
            rng = (f"{lo:g}-{hi:g}" if lo is not None and hi is not None
                   else (f">={lo:g}" if lo is not None else f"<={hi:g}"))
            tag = "info" if key in advisory else ("FAIL" if bad else " ok ")
            print(f"  [{tag}] {key:17s} {v:7.2f}  "
                  f"target {rng:9s} {unit}   ({why})")
    if verbose:
        print(f"  => {'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}")
    return m, failures
