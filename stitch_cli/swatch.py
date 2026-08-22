"""Build a stitch-treatment test swatch from a logo SVG.

The swatch is one PES that stacks the same letterforms several times — each
    row digitized with a different fill treatment — so the operator can sew one
    patch, photograph it, and pick the treatment that doesn't come out 'choppy.'

The recipe is baked in (it matches the questions answered when the user
requested this), not parameterized: 4 rows of "LOSERS" at logo size and 2
rows of the phone number, each row tagged with a different stitch method.
Edit `ROW_RECIPE` below to remix.

Mechanics:

- Each subject (LOSERS = path9+path10 inside text5; phone = text6) is
  extracted from `designs/DL-logo.svg` by *wrapping in a <g> with the composed
  parent transform chain* — paths' `d` data is never touched. The baked
  scale(0.43238663) preserves the logo's real sewn size.
- Fill-method rows are tagged with `data-stitch-method` / `data-fill-angle`,
  honored by the tuning.py extension that reads those attrs.
- Automatic satin conversion is deliberately excluded from this legacy swatch:
  Ink/Stitch 3.2.2 can take minutes or fail to return on these compound glyph
  outlines. Use `stitch lettering-swatch` for the production satin comparison.
- Tick marks (N short running-stitch dashes beside row N) make rows
  identifiable in a photo.
"""

from __future__ import annotations

import copy
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from . import inkstitch


SVG_NS = "http://www.w3.org/2000/svg"
INKSTITCH_NS = "http://inkstitch.org/namespace"
WRITE_NSMAP = {None: SVG_NS, "inkstitch": INKSTITCH_NS}

# Hoop / canvas — match designs/DL-logo.svg.
CANVAS_MM = 101.6  # 4 inch
FIELD_MM = 128.0   # PE900 stitchable width (field is 130 x 180mm)
ROW_GAP_MM = 5.0
TICK_LEN_MM = 3.0
TICK_PITCH_MM = 1.4
TICK_MARGIN_MM = 3.0  # gap between ticks block and the row content

# Composed parent transforms inside designs/DL-logo.svg:
#   g6    = translate(-1.7698,24.6797) scale(0.43238663)
#   text5 = translate(5.59 30.03)         (parent of path9, path10, ...)
#   text6 carries its own translate(45.22 111.64)
LOSERS_TRANSFORM = (
    "translate(-1.7698,24.6797) scale(0.43238663) translate(5.59,30.03)"
)
PHONE_TRANSFORM = (
    "translate(-1.7698,24.6797) scale(0.43238663) translate(45.22,111.64)"
)
LOSERS_PATH_IDS = ("path9", "path10")
PHONE_PATH_ID = "text6"


def _qn(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


@dataclass(frozen=True)
class Row:
    """One row of the swatch."""

    subject: str           # 'losers' or 'phone'
    label: str             # short human label for the printed legend
    fill_method: str | None = None  # 'auto_fill' or 'contour_fill'
    fill_angle: str | None = None   # degrees as a string, e.g. '45'
    satin: bool = False             # if True, run fill_to_satin


# Baked recipe — matches the choices the user picked when this was requested.
ROW_RECIPE: tuple[Row, ...] = (
    Row("losers", "1. LOSERS auto-fill (baseline)", fill_method="auto_fill"),
    Row("losers", "2. LOSERS contour-fill",         fill_method="contour_fill"),
    Row("losers", "3. LOSERS auto-fill @90°",       fill_method="auto_fill", fill_angle="90"),
    Row("losers", "4. LOSERS auto-fill @45°",       fill_method="auto_fill", fill_angle="45"),
    Row("phone",  "5. (917)-524-7853 auto-fill",    fill_method="auto_fill"),
    # fill_to_satin on the phone's single, many-glyph compound path does not
    # terminate reliably in Ink/Stitch 3.2.2. Compare contour fill here; for
    # production satin phone text, use `stitch lettering` with Small Font.
    Row("phone",  "6. (917)-524-7853 contour-fill", fill_method="contour_fill"),
)


def _new_svg(width_mm: float = CANVAS_MM, height_mm: float = CANVAS_MM) -> etree._Element:
    """Fresh root <svg> with mm sizing and the inkstitch namespace declared."""
    return etree.Element(
        _qn(SVG_NS, "svg"),
        nsmap=WRITE_NSMAP,
        attrib={
            "width": f"{width_mm}mm",
            "height": f"{height_mm}mm",
            "viewBox": f"0 0 {width_mm} {height_mm}",
            "version": "1.1",
        },
    )


def _build_subject(logo_root: etree._Element, subject: str) -> etree._Element:
    """Extract a subject from the logo as a <g> with its composed parent transform."""
    if subject == "losers":
        ids, transform = LOSERS_PATH_IDS, LOSERS_TRANSFORM
    elif subject == "phone":
        ids, transform = (PHONE_PATH_ID,), PHONE_TRANSFORM
    else:
        raise ValueError(f"unknown subject: {subject!r}")

    group = etree.Element(_qn(SVG_NS, "g"), attrib={"transform": transform})
    for path_id in ids:
        src = logo_root.find(f".//{{{SVG_NS}}}path[@id='{path_id}']")
        if src is None:
            raise RuntimeError(f"path id={path_id!r} not found in logo")
        node = copy.deepcopy(src)
        # Phone path carries its own transform in the source; the wrapper now
        # holds the full composed chain, so strip the leaf transform.
        if subject == "phone":
            node.attrib.pop("transform", None)
        # Always set explicit black fill — tuning.py will re-tint to the preset.
        node.set("fill", "#000000")
        group.append(node)
    return group


def _measure(group: etree._Element) -> tuple[float, float, float, float]:
    """Return (x_mm, y_mm, w_mm, h_mm) by handing a temp SVG to Inkscape.

    Inkscape returns CSS pixels at the document's default 96 dpi; with our
    canvas declared as `width=Wmm viewBox="0 0 W W"`, 1 viewBox unit = 1mm,
    so px / (96/25.4) = mm.
    """
    svg = _new_svg()
    svg.append(copy.deepcopy(group))
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tf:
        tf.write(etree.tostring(svg, xml_declaration=True, encoding="utf-8"))
        tmp = Path(tf.name)
    try:
        def q(flag: str) -> float:
            res = subprocess.run(
                ["inkscape", flag, str(tmp)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            return float(res.stdout.strip())
        px_per_mm = 96.0 / 25.4
        return (
            q("--query-x") / px_per_mm,
            q("--query-y") / px_per_mm,
            q("--query-width") / px_per_mm,
            q("--query-height") / px_per_mm,
        )
    finally:
        tmp.unlink(missing_ok=True)


def _satin_convert(subject_group: etree._Element) -> etree._Element:
    """Run fill_to_satin on this one subject and return the converted <g>
    with `inkstitch:satin_column='true'` stamped on every <path>."""
    subject_clone = copy.deepcopy(subject_group)
    subject_clone.set("id", "_swatch_subject")
    selected_ids: list[str] = []
    for i, path in enumerate(subject_clone.iter(_qn(SVG_NS, "path"))):
        path_id = path.get("id") or f"_swatch_path_{i}"
        path.set("id", path_id)
        selected_ids.append(path_id)
    if not selected_ids:
        raise RuntimeError("satin swatch subject contains no paths")
    svg = _new_svg()
    svg.append(subject_clone)

    with tempfile.TemporaryDirectory() as td:
        in_svg = Path(td) / "in.svg"
        out_svg = Path(td) / "out.svg"
        with in_svg.open("wb") as f:
            f.write(etree.tostring(svg, xml_declaration=True, encoding="utf-8"))
        inkstitch.run_effect(
            "fill_to_satin",
            in_svg,
            out_svg,
            ids=selected_ids,
            params={
                "keep_originals": False,
                "center": True,
                "contour": False,
                "zigzag": False,
                "pull_compensation_mm": "0",
                "skip_end_section": False,
            },
            timeout_s=120,
        )
        converted_root = etree.parse(str(out_svg)).getroot()

    # Find our subject group in the output. Effect extensions usually preserve
    # ids; fall back to the first top-level <g>, then to wrapping all loose
    # paths in a fresh group with our original transform.
    converted_group = converted_root.find(
        f".//{{{SVG_NS}}}g[@id='_swatch_subject']"
    )
    if converted_group is None:
        converted_group = converted_root.find(_qn(SVG_NS, "g"))
    if converted_group is None:
        converted_group = etree.Element(
            _qn(SVG_NS, "g"),
            attrib={"transform": subject_group.get("transform", "")},
        )
        for p in converted_root.iter(_qn(SVG_NS, "path")):
            converted_group.append(copy.deepcopy(p))

    paths = list(converted_group.iter(_qn(SVG_NS, "path")))
    if not paths:
        raise RuntimeError("fill_to_satin produced no <path> elements")
    for p in paths:
        p.set(_qn(INKSTITCH_NS, "satin_column"), "true")

    # Detach from the parsed tree and clean up the temp id.
    parent = converted_group.getparent()
    if parent is not None:
        parent.remove(converted_group)
    converted_group.attrib.pop("id", None)
    return converted_group


def _tick_marks(n: int, x: float, y_center: float) -> etree._Element | None:
    """N short stacked dashes as one running-stitch path."""
    if n <= 0:
        return None
    total_h = (n - 1) * TICK_PITCH_MM
    y0 = y_center - total_h / 2
    d = " ".join(
        f"M {x:.3f} {y0 + i * TICK_PITCH_MM:.3f} h {TICK_LEN_MM:.3f}"
        for i in range(n)
    )
    return etree.Element(
        _qn(SVG_NS, "path"),
        attrib={
            "d": d,
            "fill": "none",
            "stroke": "#000000",
            "stroke-width": "0.3",
        },
    )


def build_swatch(logo_svg: Path, output_svg: Path) -> Path:
    """Build the swatch SVG at `output_svg` from `logo_svg`. Returns output_svg."""
    logo_root = etree.parse(str(logo_svg)).getroot()

    base_losers = _build_subject(logo_root, "losers")
    base_phone = _build_subject(logo_root, "phone")
    losers_bbox = _measure(base_losers)
    phone_bbox = _measure(base_phone)

    # Build per-row group + bbox. Non-satin rows reuse the base bbox; satin
    # rows are re-measured because fill_to_satin can shift extents slightly.
    rows_built: list[tuple[Row, etree._Element, tuple[float, float, float, float]]] = []
    for r in ROW_RECIPE:
        base = base_losers if r.subject == "losers" else base_phone
        base_bbox = losers_bbox if r.subject == "losers" else phone_bbox
        if r.satin:
            group = _satin_convert(base)
            bbox = _measure(group)
        else:
            group = copy.deepcopy(base)
            for p in group.iter(_qn(SVG_NS, "path")):
                if r.fill_method:
                    p.set("data-stitch-method", r.fill_method)
                if r.fill_angle is not None:
                    p.set("data-fill-angle", r.fill_angle)
            bbox = base_bbox
        rows_built.append((r, group, bbox))

    total_h = sum(h for _, _, (_, _, _, h) in rows_built) + ROW_GAP_MM * (len(rows_built) - 1)
    max_w = max(w for _, _, (_, _, w, _) in rows_built)
    if total_h > FIELD_MM:
        raise RuntimeError(f"swatch height {total_h:.1f}mm exceeds {FIELD_MM}mm stitch field")
    if max_w > FIELD_MM:
        raise RuntimeError(f"widest row {max_w:.1f}mm exceeds {FIELD_MM}mm stitch field")

    out = _new_svg()
    swatch_g = etree.SubElement(out, _qn(SVG_NS, "g"), attrib={"id": "swatch"})

    cursor_y = (CANVAS_MM - total_h) / 2.0
    for i, (r, group, (x, y, w, h)) in enumerate(rows_built):
        row_origin_x = (CANVAS_MM - w) / 2.0 - x
        row_origin_y = cursor_y - y
        placement = etree.SubElement(
            swatch_g,
            _qn(SVG_NS, "g"),
            attrib={
                "id": f"row-{i+1}",
                "transform": f"translate({row_origin_x:.3f},{row_origin_y:.3f})",
                "data-row-label": r.label,
            },
        )
        placement.append(group)

        tick_x = (CANVAS_MM - w) / 2.0 - TICK_MARGIN_MM - TICK_LEN_MM
        tick_y_center = cursor_y + h / 2.0
        ticks = _tick_marks(i + 1, tick_x, tick_y_center)
        if ticks is not None:
            swatch_g.append(ticks)

        cursor_y += h + ROW_GAP_MM

    output_svg.parent.mkdir(parents=True, exist_ok=True)
    etree.ElementTree(out).write(
        str(output_svg),
        xml_declaration=True,
        encoding="utf-8",
        standalone=True,
    )
    return output_svg


def row_legend() -> str:
    """Human-readable legend for printing alongside the swatch."""
    return "\n".join(r.label for r in ROW_RECIPE)
