"""Convert an SVG with live <text> elements into a single-stroke,
hoop-sized SVG that the from-svg pipeline can turn into a running-stitch PES.

Inkscape ships the Hershey Text extension (public-domain plotter fonts)
which replaces every text element with single-line vector strokes. We
drive it headless, then rebuild the result as a 100mm canvas with each
path tagged so tuning.py classifies it as running stitch (optionally
bean stitch for ~3x visual weight on textured fabric like twill).

The Hershey extension expects an SVG with explicit width/height
attributes; many designer-exported SVGs only carry a viewBox, so we
inject them, treating user-units as mm.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path

from lxml import etree


# Inkscape --query-all returns bbox values in document px (96 DPI). When the
# SVG width is in mm, this lets us convert back to viewBox user units.
_PX_PER_UNIT = {"": 1.0, "px": 1.0, "mm": 96 / 25.4, "cm": 96 / 2.54,
                "in": 96.0, "pt": 96 / 72, "pc": 16.0}


def _viewbox_units_per_px(svg_root) -> float:
    vb = svg_root.get("viewBox").split()
    vb_w = float(vb[2])
    m = re.match(r"([\d.]+)\s*([a-z]*)", svg_root.get("width", ""))
    if not m:
        return 1.0
    val, unit = float(m.group(1)), m.group(2).lower()
    return vb_w / (val * _PX_PER_UNIT.get(unit, 1.0))


SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_LABEL = "{http://www.inkscape.org/namespaces/inkscape}label"

DEFAULT_FONT = "EMSReadability"     # clean modern humanist sans, our default
DEFAULT_MAX_DIM_MM = 95.0           # leaves ~2.5mm margin on a 100mm canvas
DEFAULT_CANVAS_MM = 128.0           # PE900 field is 130mm across; 1mm margin each side


def _find_hershey() -> tuple[str, str]:
    """Return (python_binary, hershey_script) using Inkscape's bundled Python.
    Path can be overridden via STITCH_INKSCAPE_RESOURCES env var."""
    override = os.environ.get("STITCH_INKSCAPE_RESOURCES")
    base = Path(override) if override else Path("/Applications/Inkscape.app/Contents/Resources")
    py = base / "bin/python3"
    script = base / "share/inkscape/extensions/hershey.py"
    if not py.exists() or not script.exists():
        raise RuntimeError(
            f"Hershey extension not found under {base}. Install Inkscape or set "
            "STITCH_INKSCAPE_RESOURCES to its Resources directory."
        )
    return str(py), str(script)


def _ensure_width_height(src: Path, dst: Path) -> None:
    """Hershey requires width/height attrs on the root SVG. Inject them
    from the viewBox if missing, treating user-units as mm."""
    tree = etree.parse(str(src))
    root = tree.getroot()
    if root.get("width") and root.get("height"):
        shutil.copyfile(src, dst)
        return
    vb = root.get("viewBox")
    if not vb:
        raise RuntimeError(f"{src} has neither viewBox nor width/height")
    parts = vb.split()
    if len(parts) != 4:
        raise RuntimeError(f"{src} has malformed viewBox: {vb!r}")
    _, _, w, h = parts
    root.set("width", f"{w}mm")
    root.set("height", f"{h}mm")
    tree.write(str(dst), xml_declaration=True, encoding="utf-8", standalone=True)


def _query_bboxes(svg: Path) -> dict[str, tuple[float, float, float, float]]:
    """Use Inkscape's --query-all to get (x, y, w, h) for every id'd element."""
    result = subprocess.run(
        ["inkscape", "--query-all", str(svg)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    bboxes: dict[str, tuple[float, float, float, float]] = {}
    for line in result.stdout.splitlines():
        parts = line.split(",")
        if len(parts) != 5:
            continue
        try:
            bboxes[parts[0]] = tuple(float(p) for p in parts[1:])  # type: ignore[assignment]
        except ValueError:
            pass
    return bboxes


def _run_hershey(src: Path, dst: Path, font: str) -> None:
    py, script = _find_hershey()
    cmd = [py, script, "--tab=render", f"--fontface={font}", "--preserve=false", str(src)]
    # Inkscape's extension expects to be invoked from its extensions dir so
    # relative imports of bundled font modules resolve.
    with dst.open("wb") as f:
        subprocess.run(
            cmd, check=True, stdout=f, cwd=Path(script).parent, timeout=180
        )


# --- corner-preserving centerline smoothing ---------------------------------
# Hershey fonts define glyphs as coarse polylines (~1.5mm segments at our sizes).
# A running/bean stitch traces them faithfully, so round glyphs (O S G C 8 9 6)
# read as visible polygons — worst on the small phone-number line. Shortening the
# stitch can't fix this (it just stacks tiny stitches along the same facets); the
# limiting factor is the font's vertex count. So we spline through the gentle
# runs and resample finely, while leaving sharp corners (L E H I N, 2 4 7, the
# dashes, B/D/R bowl-to-spine joints) untouched. Paths we rewrite are tagged
# data-smoothed="true" so tuning.py knows to keep the running tolerance tight
# enough that Ink/Stitch doesn't decimate the new curvature back out.
SMOOTH_SPACING_MM = 0.5    # target along-curve resample pitch in canvas mm
SMOOTH_CORNER_DEG = 42.0   # exterior turn above this is treated as a hard corner


def _ident() -> tuple[float, ...]:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)  # a,b,c,d,e,f


def _mat_mul(m: tuple, n: tuple) -> tuple:
    """Compose affines so a point is transformed by m(n(point))."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (
        a * A + c * B, b * A + d * B,
        a * C + c * D, b * C + d * D,
        a * E + c * F + e, b * E + d * F + f,
    )


def _parse_transform(s: str | None) -> tuple:
    if not s:
        return _ident()
    m = _ident()
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s):
        nums = [float(v) for v in re.split(r"[\s,]+", args.strip()) if v]
        if name == "translate":
            t = (1.0, 0.0, 0.0, 1.0, nums[0], nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale":
            sx = nums[0]
            t = (sx, 0.0, 0.0, nums[1] if len(nums) > 1 else sx, 0.0, 0.0)
        elif name == "matrix":
            t = tuple(nums)
        else:  # rotate/skew don't change average scale enough to matter here
            t = _ident()
        m = _mat_mul(m, t)
    return m


def _avg_scale(m: tuple) -> float:
    a, b, c, d = m[:4]
    return math.sqrt(abs(a * d - b * c))


def _polyline_subpaths(d: str) -> list[list[tuple[float, float]]] | None:
    """Parse an absolute M/L polyline into subpaths. Returns None for anything
    with curves/arcs/relative commands so we safely skip smoothing on it."""
    if not re.fullmatch(r"[ML0-9eE.,+\-\s]*", d.strip()):
        return None
    tokens = re.findall(r"[ML]|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", d)
    subs: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] | None = None
    j = 0
    while j < len(tokens):
        t = tokens[j]
        if t in ("M", "L"):
            if j + 2 >= len(tokens) + 1 or j + 1 >= len(tokens):
                return None
            cur_pt = (float(tokens[j + 1]), float(tokens[j + 2]))
            if t == "M":
                cur = [cur_pt]
                subs.append(cur)
            else:
                if cur is None:
                    return None
                cur.append(cur_pt)
            j += 3
        else:  # implicit lineto coordinate pair (SVG spec after an M/L)
            if cur is None or j + 1 >= len(tokens):
                return None
            cur.append((float(tokens[j]), float(tokens[j + 1])))
            j += 2
    return subs


def _subpaths_to_d(subs: list[list[tuple[float, float]]]) -> str:
    parts = []
    for pts in subs:
        parts.append("M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in pts))
    return " ".join(parts)


def _turn_angle(p0, p1, p2) -> float:
    ax, ay = p1[0] - p0[0], p1[1] - p0[1]
    bx, by = p2[0] - p1[0], p2[1] - p1[1]
    na, nb = math.hypot(ax, ay), math.hypot(bx, by)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    cosv = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(cosv))


def _catmull(p0, p1, p2, p3, t):
    t2, t3 = t * t, t * t * t

    def comp(a, b, c, d):
        return 0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2
                      + (-a + 3 * b - 3 * c + d) * t3)

    return (comp(p0[0], p1[0], p2[0], p3[0]), comp(p0[1], p1[1], p2[1], p3[1]))


def _smooth_polyline(pts, corner_deg, spacing):
    """Catmull-Rom through gentle vertex runs; keep sharp corners crisp."""
    n = len(pts)
    if n < 3:
        return pts[:]
    is_corner = ([True]
                 + [_turn_angle(pts[i - 1], pts[i], pts[i + 1]) > corner_deg
                    for i in range(1, n - 1)]
                 + [True])
    out = [pts[0]]
    i = 0
    while i < n - 1:
        j = i + 1
        while j < n - 1 and not is_corner[j]:
            j += 1
        seg = pts[i:j + 1]
        if len(seg) <= 2:
            out.append(pts[j])
        else:
            ext = [seg[0]] + seg + [seg[-1]]
            for k in range(1, len(ext) - 2):
                a, b, c, dd = ext[k - 1], ext[k], ext[k + 1], ext[k + 2]
                seglen = math.hypot(c[0] - b[0], c[1] - b[1])
                steps = max(1, int(round(seglen / spacing)))
                for s in range(1, steps + 1):
                    out.append(_catmull(a, b, c, dd, s / steps))
        i = j
    ded = [out[0]]
    for p in out[1:]:
        if math.hypot(p[0] - ded[-1][0], p[1] - ded[-1][1]) > 1e-4:
            ded.append(p)
    return ded


def smooth_svg_paths(
    root: etree._Element,
    spacing_mm: float = SMOOTH_SPACING_MM,
    corner_deg: float = SMOOTH_CORNER_DEG,
) -> int:
    """In-place: round the curved runs of every absolute-polyline <path> while
    preserving sharp corners. Resample pitch is held at ~spacing_mm in canvas
    units by dividing by each path's composed (ancestor × own) transform scale.
    Tags rewritten paths data-smoothed="true". Returns how many were smoothed."""
    count = 0

    def walk(elem: etree._Element, m: tuple) -> None:
        nonlocal count
        m = _mat_mul(m, _parse_transform(elem.get("transform")))
        if etree.QName(elem).localname == "path" and elem.get("d"):
            subs = _polyline_subpaths(elem.get("d"))
            eff = _avg_scale(m)
            if subs is not None and eff > 1e-9:
                local_spacing = spacing_mm / eff
                new_subs = [_smooth_polyline(s, corner_deg, local_spacing) for s in subs]
                if any(len(b) != len(a) for a, b in zip(subs, new_subs)):
                    elem.set("d", _subpaths_to_d(new_subs))
                    elem.set("data-smoothed", "true")
                    count += 1
        for ch in elem:
            walk(ch, m)

    walk(root, _ident())
    return count


def prepare_hershey_svg(
    source_svg: Path,
    output_svg: Path,
    font: str = DEFAULT_FONT,
    bean_stitch: bool = True,
    chainstitch: bool = False,
    smooth: bool = True,
    max_dim_mm: float = DEFAULT_MAX_DIM_MM,
    canvas_mm: float = DEFAULT_CANVAS_MM,
) -> Path:
    """Render every <text> in source_svg as single-stroke Hershey paths,
    then rebuild as a canvas_mm × canvas_mm SVG with the design scaled
    so its longer side is max_dim_mm and centered. Each generated path
    gets `fill:none; stroke:#000000` plus a `data-stroke-method` honored by
    tuning.py: `chainstitch` (faux chain look) takes precedence when set,
    else `bean_stitch` when `bean_stitch` is true, else plain running stitch.

    When `smooth` is true (default) a corner-preserving spline pass rounds the
    curved glyph runs so circles/bowls don't read as polygons — see
    smooth_svg_paths. Pass smooth=False for the raw Hershey polylines.
    """
    with tempfile.TemporaryDirectory() as td:
        sized = Path(td) / "sized.svg"
        rendered = Path(td) / "hershey.svg"
        _ensure_width_height(source_svg, sized)
        _run_hershey(sized, rendered, font)

        src_tree = etree.parse(str(rendered))
        src_root = src_tree.getroot()
        vb_parts = src_root.get("viewBox").split()
        src_w, src_h = float(vb_parts[2]), float(vb_parts[3])

        # Auto-recenter each rendered text line horizontally. The source's
        # per-text x-offsets center each line in the *original* font's
        # metrics (e.g. Sathu); after swapping to Hershey the widths
        # change, so we measure each Hershey-labeled group's rendered bbox
        # and prepend a translate to center it within the source width.
        hershey_groups = [
            g for g in src_root.iter(f"{{{SVG_NS}}}g")
            if "Hershey" in g.get(INKSCAPE_LABEL, "")
        ]
        for i, g in enumerate(hershey_groups):
            g.set("id", f"hershey-line-{i}")
        measured = Path(td) / "measured.svg"
        src_tree.write(str(measured), xml_declaration=True, encoding="utf-8", standalone=True)
        bboxes = _query_bboxes(measured)
        uu_per_px = _viewbox_units_per_px(src_root)
        for g in hershey_groups:
            bbox = bboxes.get(g.get("id"))
            if not bbox:
                continue
            x_uu = bbox[0] * uu_per_px
            w_uu = bbox[2] * uu_per_px
            shift_x = (src_w / 2) - (x_uu + w_uu / 2)
            own = g.get("transform", "")
            g.set("transform", f"translate({shift_x:.3f},0) {own}".strip())

        # Scale to fit the longer side into max_dim_mm, then center on canvas.
        scale = min(max_dim_mm / src_w, max_dim_mm / src_h)
        dx = (canvas_mm - src_w * scale) / 2
        dy = (canvas_mm - src_h * scale) / 2

        new_root = etree.Element(
            f"{{{SVG_NS}}}svg",
            nsmap={None: SVG_NS},
            attrib={
                "width": f"{canvas_mm}mm",
                "height": f"{canvas_mm}mm",
                "viewBox": f"0 0 {canvas_mm} {canvas_mm}",
            },
        )
        outer = etree.SubElement(
            new_root,
            f"{{{SVG_NS}}}g",
            attrib={"transform": f"translate({dx:.3f},{dy:.3f}) scale({scale:.6f})"},
        )

        for g in src_root.iter(f"{{{SVG_NS}}}g"):
            if "Hershey" not in g.get(INKSCAPE_LABEL, ""):
                continue
            new_g = deepcopy(g)
            new_g.attrib.pop(INKSCAPE_LABEL, None)
            # Compose any ancestor <g> transforms so the result is positioned
            # the same way regardless of how nested it was in the source.
            ancestor_xforms = []
            anc = g.getparent()
            while anc is not None and anc is not src_root:
                xf = anc.get("transform")
                if xf:
                    ancestor_xforms.append(xf)
                anc = anc.getparent()
            if ancestor_xforms:
                composed = " ".join(reversed(ancestor_xforms))
                own = new_g.get("transform", "")
                new_g.set("transform", f"{composed} {own}".strip())
            for p in new_g.iter(f"{{{SVG_NS}}}path"):
                # Hershey puts fill:none / stroke on the parent <g>; tuning.py
                # classifies per-path, so stamp the style directly here.
                existing = p.get("style", "")
                kept = ";".join(
                    s for s in existing.split(";")
                    if s.strip() and not s.strip().lower().startswith(("fill:", "stroke:"))
                )
                p.set("style", "fill:none;stroke:#000000" + (";" + kept if kept else ""))
                if chainstitch:
                    p.set("data-stroke-method", "chainstitch")
                elif bean_stitch:
                    p.set("data-stroke-method", "bean_stitch")
            outer.append(new_g)

    if smooth:
        smooth_svg_paths(new_root)

    output_svg.parent.mkdir(parents=True, exist_ok=True)
    etree.ElementTree(new_root).write(
        str(output_svg), xml_declaration=True, encoding="utf-8", standalone=True
    )
    return output_svg
