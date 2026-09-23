"""Post-Ink/Stitch assertions for critical SVG objects.

SVG topology checks cannot prove that Ink/Stitch interpreted a path as
intended.  A malformed satin may compile successfully while collapsing to a
thin strip.  Critical objects opt in with ``data-qa-*`` bounds on their source
path; this module compiles each annotated object in isolation and checks the
actual stitch bounds and count.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import tempfile

from lxml import etree

from . import convert, inkstitch


SVG_NS = "http://www.w3.org/2000/svg"

GEOMETRY_TAGS = {
    f"{{{SVG_NS}}}{name}"
    for name in (
        "path", "rect", "circle", "ellipse", "line", "polyline",
        "polygon", "text", "use",
    )
}


@dataclass(frozen=True)
class Assertion:
    element_id: str
    role: str
    min_width_mm: float | None = None
    max_width_mm: float | None = None
    min_height_mm: float | None = None
    max_height_mm: float | None = None
    min_stitches: int | None = None
    max_stitches: int | None = None


@dataclass(frozen=True)
class Result:
    element_id: str
    role: str
    width_mm: float
    height_mm: float
    stitches: int
    failures: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _optional_float(elem: etree._Element, name: str) -> float | None:
    value = elem.get(name)
    return float(value) if value not in (None, "") else None


def _optional_int(elem: etree._Element, name: str) -> int | None:
    value = elem.get(name)
    return int(value) if value not in (None, "") else None


def collect_assertions(svg_path: Path) -> list[Assertion]:
    root = etree.parse(str(svg_path)).getroot()
    assertions: list[Assertion] = []
    for elem in root.iter(f"{{{SVG_NS}}}path"):
        attrs = (
            "data-qa-min-width-mm", "data-qa-max-width-mm",
            "data-qa-min-height-mm", "data-qa-max-height-mm",
            "data-qa-min-stitches", "data-qa-max-stitches",
        )
        if not any(elem.get(name) is not None for name in attrs):
            continue
        element_id = elem.get("id")
        if not element_id:
            raise ValueError("compiled-object QA path requires a stable id")
        assertions.append(Assertion(
            element_id=element_id,
            role=elem.get("data-qa-role") or element_id,
            min_width_mm=_optional_float(elem, "data-qa-min-width-mm"),
            max_width_mm=_optional_float(elem, "data-qa-max-width-mm"),
            min_height_mm=_optional_float(elem, "data-qa-min-height-mm"),
            max_height_mm=_optional_float(elem, "data-qa-max-height-mm"),
            min_stitches=_optional_int(elem, "data-qa-min-stitches"),
            max_stitches=_optional_int(elem, "data-qa-max-stitches"),
        ))
    return assertions


def check_assertion(assertion: Assertion, *, width_mm: float, height_mm: float,
                    stitches: int) -> Result:
    failures: list[str] = []
    values = {
        "width": width_mm,
        "height": height_mm,
    }
    for key, value in values.items():
        minimum = getattr(assertion, f"min_{key}_mm")
        maximum = getattr(assertion, f"max_{key}_mm")
        if minimum is not None and value < minimum:
            failures.append(f"{key} {value:.2f}mm is below {minimum:.2f}mm")
        if maximum is not None and value > maximum:
            failures.append(f"{key} {value:.2f}mm exceeds {maximum:.2f}mm")
    if assertion.min_stitches is not None and stitches < assertion.min_stitches:
        failures.append(f"{stitches} stitches is below {assertion.min_stitches}")
    if assertion.max_stitches is not None and stitches > assertion.max_stitches:
        failures.append(f"{stitches} stitches exceeds {assertion.max_stitches}")
    return Result(
        assertion.element_id, assertion.role, width_mm, height_mm, stitches,
        tuple(failures),
    )


def _write_isolated_svg(source: Path, output: Path, element_id: str) -> None:
    tree = etree.parse(str(source))
    root = tree.getroot()
    target = next(
        (elem for elem in root.iter() if elem.get("id") == element_id), None
    )
    if target is None:
        raise ValueError(f"compiled-object QA target {element_id!r} not found")
    keep = target
    # deepcopy is not sufficient by itself because group transforms and root
    # document settings are part of the object's geometry.  Retain the full
    # tree and remove every other stitchable geometry node instead.
    for elem in list(root.iter()):
        if elem.tag not in GEOMETRY_TAGS or elem is keep:
            continue
        parent = elem.getparent()
        if parent is not None:
            parent.remove(elem)
    tree.write(str(output), encoding="utf-8", xml_declaration=False)


def validate_compiled_objects(tuned_svg: Path, work_dir: Path) -> list[Result]:
    assertions = collect_assertions(tuned_svg)
    if not assertions:
        return []
    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix=".compiled-qa-", dir=work_dir) as td:
        qa_dir = Path(td)
        for assertion in assertions:
            isolated_svg = qa_dir / f"{assertion.element_id}.svg"
            isolated_pes = qa_dir / f"{assertion.element_id}.pes"
            _write_isolated_svg(tuned_svg, isolated_svg, assertion.element_id)
            inkstitch.svg_to_pes(isolated_svg, isolated_pes)
            summary = convert.describe(isolated_pes)
            bounds = summary["stitch_bounds_mm"]
            if not bounds:
                results.append(Result(
                    assertion.element_id, assertion.role, 0.0, 0.0, 0,
                    ("compiled object contains no stitch penetrations",),
                ))
                continue
            width = bounds[2] - bounds[0]
            height = bounds[3] - bounds[1]
            results.append(check_assertion(
                assertion, width_mm=width, height_mm=height,
                stitches=summary["stitch_count"],
            ))
    return results


def format_results(results: list[Result]) -> list[str]:
    if not results:
        return ["compiled-object QA: no annotated objects"]
    lines = [f"compiled-object QA: {len(results)} annotated object(s)"]
    for result in results:
        status = "FAIL" if result.failures else " ok "
        lines.append(
            f"  [{status}] {result.role} ({result.element_id}): "
            f"{result.width_mm:.2f} x {result.height_mm:.2f}mm, "
            f"{result.stitches} stitches"
        )
        lines.extend(f"         {failure}" for failure in result.failures)
    return lines
