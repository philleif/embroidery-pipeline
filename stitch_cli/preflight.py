"""Structural SVG preflight for embroidery geometry.

Ink/Stitch accepts a broad range of SVG path layouts, but some ambiguous
layouts compile without an error and produce the wrong stitches.  This module
checks the invariants the project relies on before the expensive conversion:

* satin columns have two usable rails;
* declared direction rungs cross both rails and stay off shared cap endpoints;
* high-node rails do not silently become hundreds of implicit rungs;
* generated travel paths still begin at the previous object's exit and end at
  the next object's entry after job-specific geometry edits.

The parser deliberately has no heavyweight geometry dependency.  It flattens
the SVG path commands used by the checked-in designs into polylines and then
performs segment intersection and endpoint-distance checks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import re
from typing import Iterable

from lxml import etree


SVG_NS = "http://www.w3.org/2000/svg"
INKSTITCH_NS = "http://inkstitch.org/namespace"
SATIN_ATTR = f"{{{INKSTITCH_NS}}}satin_column"

_TOKEN = re.compile(
    r"[AaCcHhLlMmQqSsTtVvZz]|"
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)
_PARAMS = {
    "M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4,
    "Q": 4, "T": 2, "A": 7, "Z": 0,
}


@dataclass(frozen=True)
class Subpath:
    points: tuple[tuple[float, float], ...]
    nodes: int

    @property
    def start(self) -> tuple[float, float]:
        return self.points[0]

    @property
    def end(self) -> tuple[float, float]:
        return self.points[-1]

    @property
    def length(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.points, self.points[1:]))


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warning"
    code: str
    element_id: str
    message: str


@dataclass
class Report:
    issues: list[Issue]
    satin_columns: int = 0
    explicit_rungs: int = 0
    connectors: int = 0

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    def to_dict(self) -> dict:
        return {
            "satin_columns": self.satin_columns,
            "explicit_rungs": self.explicit_rungs,
            "connectors": self.connectors,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "issues": [asdict(issue) for issue in self.issues],
        }


def _point(x: float, y: float, relative: bool,
           current: tuple[float, float]) -> tuple[float, float]:
    return (x + current[0], y + current[1]) if relative else (x, y)


def _curve_points_cubic(p0, p1, p2, p3, steps: int = 12):
    for i in range(1, steps + 1):
        t = i / steps
        u = 1.0 - t
        yield (
            u ** 3 * p0[0] + 3 * u * u * t * p1[0]
            + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1]
            + 3 * u * t * t * p2[1] + t ** 3 * p3[1],
        )


def _curve_points_quad(p0, p1, p2, steps: int = 10):
    for i in range(1, steps + 1):
        t = i / steps
        u = 1.0 - t
        yield (
            u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
        )


def parse_path(d: str) -> list[Subpath]:
    """Flatten common SVG path commands into subpath polylines.

    Elliptical arcs are conservatively represented by their endpoint.  Satin
    rails in the repository use lines, quadratics, or cubics; accepting arcs
    here still lets endpoint/connector checks operate instead of rejecting an
    otherwise valid decorative running path.
    """
    tokens = _TOKEN.findall(d or "")
    i = 0
    command: str | None = None
    current = (0.0, 0.0)
    start = (0.0, 0.0)
    points: list[tuple[float, float]] = []
    subpaths: list[Subpath] = []
    nodes = 0
    last_cubic_control: tuple[float, float] | None = None
    last_quad_control: tuple[float, float] | None = None

    def finish() -> None:
        nonlocal points, nodes
        if points:
            deduped = [points[0]]
            for value in points[1:]:
                if math.dist(value, deduped[-1]) > 1e-9:
                    deduped.append(value)
            if len(deduped) >= 2:
                subpaths.append(Subpath(tuple(deduped), nodes))
        points = []
        nodes = 0

    while i < len(tokens):
        if tokens[i].isalpha():
            command = tokens[i]
            i += 1
            if command.upper() == "Z":
                if points and math.dist(points[-1], start) > 1e-9:
                    points.append(start)
                nodes += 1
                current = start
                last_cubic_control = last_quad_control = None
                continue
        if command is None:
            raise ValueError("SVG path data begins without a command")
        upper = command.upper()
        count = _PARAMS[upper]
        if count == 0:
            command = None
            continue
        if i + count > len(tokens) or any(t.isalpha() for t in tokens[i:i + count]):
            raise ValueError(f"incomplete SVG {command} command")
        values = [float(v) for v in tokens[i:i + count]]
        i += count
        relative = command.islower()

        if upper == "M":
            target = _point(values[0], values[1], relative, current)
            if points:
                finish()
            points = [target]
            current = start = target
            nodes = 1
            # Subsequent coordinate pairs after moveto are implicit lineto.
            command = "l" if relative else "L"
        elif upper == "L":
            current = _point(values[0], values[1], relative, current)
            points.append(current)
            nodes += 1
        elif upper == "H":
            x = values[0] + current[0] if relative else values[0]
            current = (x, current[1])
            points.append(current)
            nodes += 1
        elif upper == "V":
            y = values[0] + current[1] if relative else values[0]
            current = (current[0], y)
            points.append(current)
            nodes += 1
        elif upper == "C":
            c1 = _point(values[0], values[1], relative, current)
            c2 = _point(values[2], values[3], relative, current)
            target = _point(values[4], values[5], relative, current)
            points.extend(_curve_points_cubic(current, c1, c2, target))
            current = target
            last_cubic_control = c2
            last_quad_control = None
            nodes += 1
        elif upper == "S":
            c1 = (current if last_cubic_control is None else
                  (2 * current[0] - last_cubic_control[0],
                   2 * current[1] - last_cubic_control[1]))
            c2 = _point(values[0], values[1], relative, current)
            target = _point(values[2], values[3], relative, current)
            points.extend(_curve_points_cubic(current, c1, c2, target))
            current = target
            last_cubic_control = c2
            last_quad_control = None
            nodes += 1
        elif upper == "Q":
            control = _point(values[0], values[1], relative, current)
            target = _point(values[2], values[3], relative, current)
            points.extend(_curve_points_quad(current, control, target))
            current = target
            last_quad_control = control
            last_cubic_control = None
            nodes += 1
        elif upper == "T":
            control = (current if last_quad_control is None else
                       (2 * current[0] - last_quad_control[0],
                        2 * current[1] - last_quad_control[1]))
            target = _point(values[0], values[1], relative, current)
            points.extend(_curve_points_quad(current, control, target))
            current = target
            last_quad_control = control
            last_cubic_control = None
            nodes += 1
        elif upper == "A":
            target = _point(values[5], values[6], relative, current)
            points.append(target)
            current = target
            nodes += 1

        if upper not in ("C", "S"):
            last_cubic_control = None
        if upper not in ("Q", "T"):
            last_quad_control = None

    finish()
    return subpaths


def _cross(a, b) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _intersection_fraction(rail: Subpath, rung: Subpath,
                           tolerance: float) -> float | None:
    """Return arc-length fraction where a rung first crosses a rail."""
    rail_segments = list(zip(rail.points, rail.points[1:]))
    rung_segments = list(zip(rung.points, rung.points[1:]))
    rail_lengths = [math.dist(a, b) for a, b in rail_segments]
    total = sum(rail_lengths)
    walked = 0.0
    for (p, p2), seg_len in zip(rail_segments, rail_lengths):
        r = (p2[0] - p[0], p2[1] - p[1])
        for q, q2 in rung_segments:
            s = (q2[0] - q[0], q2[1] - q[1])
            denom = _cross(r, s)
            qp = (q[0] - p[0], q[1] - p[1])
            if abs(denom) > 1e-10:
                t = _cross(qp, s) / denom
                u = _cross(qp, r) / denom
                if -1e-8 <= t <= 1 + 1e-8 and -1e-8 <= u <= 1 + 1e-8:
                    return (walked + min(1.0, max(0.0, t)) * seg_len) / max(total, 1e-9)
            # Rounded SVG coordinates can leave a microscopic miss at the
            # intended intersection. Endpoint-to-segment distance is enough
            # for that case and avoids a geometry dependency.
            for candidate, fraction in ((q, 0.0), (q2, 1.0)):
                if seg_len <= 1e-12:
                    continue
                t = ((candidate[0] - p[0]) * r[0]
                     + (candidate[1] - p[1]) * r[1]) / (seg_len * seg_len)
                t = min(1.0, max(0.0, t))
                nearest = (p[0] + t * r[0], p[1] + t * r[1])
                if math.dist(candidate, nearest) <= tolerance:
                    return (walked + t * seg_len) / max(total, 1e-9)
        walked += seg_len
    return None


def _document_mm_per_unit(root: etree._Element) -> float:
    viewbox = [float(v) for v in (root.get("viewBox") or "").replace(",", " ").split()]
    width = (root.get("width") or "").strip()
    match = re.fullmatch(r"([-+0-9.eE]+)mm", width)
    if len(viewbox) == 4 and match and viewbox[2] > 0:
        return float(match.group(1)) / viewbox[2]
    return 1.0


def _is_satin(elem: etree._Element) -> bool:
    return (elem.get(SATIN_ATTR) or "").lower() == "true"


def _is_travel(elem: etree._Element) -> bool:
    return ((elem.get("id") or "").startswith("travel")
            or (elem.get("data-role") or "").lower() == "travel")


def _is_stitchable(elem: etree._Element) -> bool:
    style = elem.get("style") or ""
    cls = elem.get("class") or ""
    return cls not in ("rung", "satin-fallback") and (
        _is_satin(elem) or "stroke:none" not in style or "fill:none" not in style
    )


def _entry_exit(elem: etree._Element) -> tuple[tuple[float, float], tuple[float, float]]:
    subpaths = parse_path(elem.get("d") or "")
    if not subpaths:
        raise ValueError("path has no usable geometry")
    if _is_satin(elem) and len(subpaths) >= 2:
        entry = ((subpaths[0].start[0] + subpaths[1].start[0]) / 2,
                 (subpaths[0].start[1] + subpaths[1].start[1]) / 2)
        exit_ = ((subpaths[0].end[0] + subpaths[1].end[0]) / 2,
                 (subpaths[0].end[1] + subpaths[1].end[1]) / 2)
        return entry, exit_
    return subpaths[0].start, subpaths[-1].end


def validate_svg(path: Path) -> Report:
    tree = etree.parse(str(path))
    root = tree.getroot()
    mm_per_unit = _document_mm_per_unit(root)
    strict_satin = (root.get("data-require-explicit-rungs") or "").lower() == "true"
    strict_connectors = (root.get("data-validate-connectors") or "").lower() == "true"
    issues: list[Issue] = []
    report = Report(issues)
    paths = list(root.iter(f"{{{SVG_NS}}}path"))

    for index, elem in enumerate(paths):
        if not _is_satin(elem):
            continue
        report.satin_columns += 1
        element_id = elem.get("id") or f"path[{index}]"
        try:
            subpaths = parse_path(elem.get("d") or "")
        except (ValueError, KeyError) as exc:
            issues.append(Issue("error", "invalid_path", element_id, str(exc)))
            continue
        if len(subpaths) < 2:
            issues.append(Issue(
                "error", "missing_rails", element_id,
                f"satin column has {len(subpaths)} usable subpath(s); expected two rails",
            ))
            continue
        rail1, rail2 = subpaths[:2]
        if min(rail1.length, rail2.length) * mm_per_unit < 0.4:
            issues.append(Issue(
                "error", "degenerate_rail", element_id,
                "one or both satin rails are shorter than 0.4mm",
            ))

        # Same-direction rails make the two cap centers correspond. Opposite
        # directions are legal SVG but a common cause of twisted satin.
        closed = (math.dist(rail1.start, rail1.end) * mm_per_unit < 0.05
                  and math.dist(rail2.start, rail2.end) * mm_per_unit < 0.05)
        direct = math.dist(rail1.start, rail2.start) + math.dist(rail1.end, rail2.end)
        crossed = math.dist(rail1.start, rail2.end) + math.dist(rail1.end, rail2.start)
        if not closed and crossed + 0.15 / mm_per_unit < direct:
            issues.append(Issue(
                "error" if strict_satin else "warning",
                "opposite_rail_direction", element_id,
                "rails run in opposite directions and may twist the satin column",
            ))

        rungs = subpaths[2:]
        report.explicit_rungs += len(rungs)
        declared = elem.get("data-direction-rungs")
        if declared is not None:
            try:
                expected = int(declared)
            except ValueError:
                issues.append(Issue("error", "invalid_rung_count", element_id,
                                    f"data-direction-rungs={declared!r} is not an integer"))
            else:
                if expected != len(rungs):
                    issues.append(Issue(
                        "error", "rung_count_mismatch", element_id,
                        f"declares {expected} rungs but path contains {len(rungs)}",
                    ))
        if not rungs:
            implicit_nodes = max(rail1.nodes, rail2.nodes)
            if strict_satin:
                issues.append(Issue(
                    "error", "implicit_rungs_forbidden", element_id,
                    f"no explicit direction rungs; {implicit_nodes} paired rail nodes "
                    "would become implicit Ink/Stitch rungs",
                ))
            elif implicit_nodes > 20:
                issues.append(Issue(
                    "warning", "dense_implicit_rungs", element_id,
                    f"{implicit_nodes} paired rail nodes will become implicit rungs; "
                    "fit sparse rails and add explicit direction guides",
                ))
            continue
        if len(rungs) < 3:
            issues.append(Issue(
                "error" if strict_satin else "warning", "too_few_rungs", element_id,
                f"only {len(rungs)} explicit rung(s); use at least three for stable direction",
            ))

        # Generated SVG coordinates are rounded to 0.01mm and a rung is
        # intentionally extended beyond the rail.  A 0.12mm near-miss window
        # still stays far below thread width while robustly identifying the
        # intended intersection at tight curved caps.
        tolerance = 0.12 / mm_per_unit
        # Exact cap intersections are dangerous; an interior guide may cross a
        # highly curved/tapered rail slightly before its selected knot.  Keep
        # an 0.08mm physical exclusion zone, which still catches shared cap
        # endpoints while allowing that harmless geometric lead-in.
        endpoint_margin_mm = 0.08
        for rung_index, rung in enumerate(rungs, 1):
            f1 = _intersection_fraction(rail1, rung, tolerance)
            f2 = _intersection_fraction(rail2, rung, tolerance)
            if f1 is None or f2 is None:
                issues.append(Issue(
                    "error", "rung_misses_rail", element_id,
                    f"rung {rung_index} does not intersect both rails",
                ))
                continue
            margin1 = endpoint_margin_mm / max(rail1.length * mm_per_unit, 1e-9)
            margin2 = endpoint_margin_mm / max(rail2.length * mm_per_unit, 1e-9)
            if (f1 <= margin1 or f1 >= 1 - margin1
                    or f2 <= margin2 or f2 >= 1 - margin2):
                issues.append(Issue(
                    "error", "rung_on_cap_endpoint", element_id,
                    f"rung {rung_index} touches a rail cap; keep direction rungs "
                    "strictly interior so Ink/Stitch cannot classify the cap as a rail",
                ))

    stitchable = [elem for elem in paths if _is_stitchable(elem)]
    for index, connector in enumerate(stitchable):
        if not _is_travel(connector):
            continue
        report.connectors += 1
        element_id = connector.get("id") or "travel"
        previous = next((e for e in reversed(stitchable[:index]) if not _is_travel(e)), None)
        following = next((e for e in stitchable[index + 1:] if not _is_travel(e)), None)
        if previous is None or following is None:
            issues.append(Issue("error", "orphan_connector", element_id,
                                "travel path has no stitchable object on both sides"))
            continue
        try:
            _, previous_exit = _entry_exit(previous)
            following_entry, _ = _entry_exit(following)
            connector_entry, connector_exit = _entry_exit(connector)
        except ValueError as exc:
            issues.append(Issue("error", "invalid_connector", element_id, str(exc)))
            continue
        tolerance_mm = float(connector.get("data-connector-tolerance-mm") or 1.5)
        start_gap = math.dist(previous_exit, connector_entry) * mm_per_unit
        end_gap = math.dist(connector_exit, following_entry) * mm_per_unit
        if start_gap > tolerance_mm or end_gap > tolerance_mm:
            issues.append(Issue(
                "error" if strict_connectors else "warning",
                "stale_connector", element_id,
                f"connector endpoint mismatch: {start_gap:.2f}mm from "
                f"{previous.get('id') or 'previous'} exit, {end_gap:.2f}mm from "
                f"{following.get('id') or 'next'} entry (limit {tolerance_mm:.2f}mm)",
            ))

    return report


def format_report(report: Report) -> Iterable[str]:
    yield (f"SVG preflight: {report.satin_columns} satin columns, "
           f"{report.explicit_rungs} explicit rungs, "
           f"{report.connectors} routed connectors")
    for issue in report.issues:
        yield f"  [{issue.level.upper():7s}] {issue.element_id}: {issue.message} ({issue.code})"
    yield (f"  => {len(report.errors)} error(s), "
           f"{len(report.warnings)} warning(s)")
