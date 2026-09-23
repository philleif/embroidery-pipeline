"""Route the traced CARROLL GARDENS outline into satin-column source.

Second half of the outline-mode flow (docs/QUALITY-BAR.md): fill_to_satin
per branch, auto_satin per glyph, then stamp the satin settings measured off
the pro corpus. Split from the tracer so a settings change does not force a
re-route, and a re-route does not silently change the settings.

  scripts/trace_carroll_gardens.py   -> designs/carroll-gardens-4.5in-outline.svg
  this script                        -> designs/carroll-gardens-4.5in.svg
  stitch from-svg --preset hat-twill-satin
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from lxml import etree

from stitch_cli import fillsatin, preflight


REPO = Path(__file__).resolve().parent.parent
SVG_NS = "http://www.w3.org/2000/svg"
INKSTITCH_NS = "http://inkstitch.org/namespace"

SOURCE = REPO / "designs" / "carroll-gardens-4.5in-outline.svg"
OUTPUT = REPO / "designs" / "carroll-gardens-4.5in.svg"
REFERENCE = "artwork/carroll-gardens-library-inspector.png"


def remove_ambiguous_rungs(path: Path) -> int:
    """Drop auto-generated rungs that touch a cap or miss either rail."""
    removed = 0
    for _ in range(16):
        report = preflight.validate_svg(path)
        bad: dict[str, set[int]] = {}
        unexpected = []
        for issue in report.errors:
            match = re.search(r"rung (\d+)", issue.message)
            if issue.code in {"rung_on_cap_endpoint", "rung_misses_rail"} and match:
                bad.setdefault(issue.element_id, set()).add(int(match.group(1)))
            else:
                unexpected.append(issue)
        if unexpected:
            details = ", ".join(
                f"{issue.element_id}:{issue.code}" for issue in unexpected
            )
            raise RuntimeError(f"unhandled satin preflight errors: {details}")
        if not bad:
            return removed

        tree = etree.parse(str(path))
        root = tree.getroot()
        by_id: dict[str, list[etree._Element]] = {}
        for element in root.iter(f"{{{SVG_NS}}}path"):
            if element.get("id"):
                by_id.setdefault(element.get("id"), []).append(element)
        for element_id, rung_numbers in bad.items():
            for element in by_id[element_id]:
                subpaths = re.findall(r"[Mm][^Mm]*", element.get("d") or "")
                for rung_number in sorted(rung_numbers, reverse=True):
                    subpath_index = rung_number + 1
                    if 0 <= subpath_index < len(subpaths):
                        subpaths.pop(subpath_index)
                        removed += 1
                element.set("d", " ".join(part.strip() for part in subpaths))
        tree.write(str(path), xml_declaration=True, encoding="utf-8")
    raise RuntimeError("ambiguous satin rungs remained after cleanup passes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--output", type=Path, default=OUTPUT)
    ap.add_argument("--spacing-mm", type=float, default=0.40)
    ap.add_argument("--max-stitch-mm", type=float, default=5.0,
                    help="pro Acre sews 5mm bold columns unsplit; a split "
                         "column halves its reversal share")
    ap.add_argument("--underlay-repeats", choices=("1", "2"), default="2",
                    help="keep 2 — the return pass leaves the needle at the "
                         "column start so the hop to the next object stays "
                         "under the 3mm jump threshold (BEACH BABY: -41 trims)")
    ap.add_argument("--underlay-length-mm", default="1.2")
    ap.add_argument("--pull-comp-mm", default="0.10",
                    help="the traced outline already sits just inside the ink, "
                         "so the preset's fabric-derived 0.20mm over-fattens "
                         "these columns and closes the condensed counters")
    ap.add_argument("--max-float-mm", type=float, default=5.0,
                    help="clamp data-min-jump-mm so a travel gap longer than "
                         "this becomes an unsewn jump rather than one long "
                         "sewn float; auto_satin reorders columns after the "
                         "tracer set 24mm and can open a gap it then sews")
    ap.add_argument("--preserve-order", action="store_true")
    ap.add_argument("--restamp-only", action="store_true")
    args = ap.parse_args()

    if not 0.40 <= args.spacing_mm <= 0.44:
        raise ValueError("satin spacing must stay between 0.40 and 0.44 mm")
    if not 4.0 <= args.max_stitch_mm <= 7.0:
        raise ValueError("satin max stitch must stay between 4.0 and 7.0 mm")

    if not args.restamp_only:
        fillsatin.convert_outline_svg(args.source, args.output,
                                      preserve_order=args.preserve_order)
    elif not args.output.exists():
        raise FileNotFoundError(f"cannot restamp missing route: {args.output}")

    tree = etree.parse(str(args.output))
    root = tree.getroot()
    root.set("data-audit-profile", "satin-wordmark")
    root.set("data-reference-image", REFERENCE)
    root.attrib.pop("data-require-explicit-rungs", None)
    root.attrib.pop("data-validate-connectors", None)
    for group in root.iter(f"{{{SVG_NS}}}g"):
        group.attrib.pop("data-satin-mode", None)

    clamped = 0
    for path in root.iter(f"{{{SVG_NS}}}path"):
        current = (path.get("data-min-jump-mm") or "").strip()
        if current and float(current) > args.max_float_mm:
            path.set("data-min-jump-mm", f"{args.max_float_mm:g}")
            clamped += 1

    satin_count = 0
    for path in root.iter(f"{{{SVG_NS}}}path"):
        if (path.get(f"{{{INKSTITCH_NS}}}satin_column") or "").lower() != "true":
            continue
        path.set("data-satin-underlay", "center")
        path.set("data-satin-underlay-repeats", args.underlay_repeats)
        path.set("data-satin-underlay-length-mm", args.underlay_length_mm)
        path.set("data-satin-spacing-mm", f"{args.spacing_mm:.2f}")
        path.set("data-satin-max-stitch-mm", f"{args.max_stitch_mm:.1f}")
        path.set("data-satin-split-method", "staggered")
        path.set("data-satin-split-staggers", "4")
        # Short-stitch OFF. It insets every other penetration to thin the
        # inside of a tight curve; at these column widths it fires almost
        # everywhere and serrates both rails instead. Measured on this design
        # at the tuned 0.22mm: satin rail turn p90 165 degrees.
        path.set("data-satin-short-stitch-mm", "0")
        if not (path.get("data-pull-comp-mm") or "").strip():
            path.set("data-pull-comp-mm", args.pull_comp_mm)
        path.attrib.pop("data-satin-random-split-phase", None)
        path.attrib.pop("data-satin-center-repeats", None)
        satin_count += 1

    tree.write(str(args.output), xml_declaration=True, encoding="utf-8",
               pretty_print=False)
    removed = remove_ambiguous_rungs(args.output)
    print(f"prepared {args.output} with {satin_count} routed satin sections, "
          f"{args.spacing_mm:.2f} mm spacing, {args.max_stitch_mm:.1f} mm split "
          f"limit; clamped {clamped} jump limits, removed {removed} ambiguous rungs")


if __name__ == "__main__":
    main()
