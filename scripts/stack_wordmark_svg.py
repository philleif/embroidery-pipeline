"""Reflow a two-word traced SVG from one line into a centered stack.

The input paths must use absolute M/L/C commands, as emitted by trace_lib.py.
Paths are assigned to the first or second word by their horizontal center
relative to the source viewBox midpoint. By default the operation only
translates objects. `--target-content-width-mm` uniformly scales the rails;
the downstream strict PES audit is therefore mandatory after that option.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from lxml import etree


SVG_NS = "http://www.w3.org/2000/svg"
NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def path_bbox(path: etree._Element) -> tuple[float, float, float, float]:
    d = path.get("d", "")
    unsupported = set(re.findall(r"[A-Za-z]", d)) - {"M", "L", "C", "E"}
    if unsupported:
        raise ValueError(
            f"{path.get('id', '<unnamed>')} uses unsupported commands: "
            f"{', '.join(sorted(unsupported))}"
        )
    nums = [float(value) for value in NUMBER_RE.findall(d)]
    if len(nums) < 2 or len(nums) % 2:
        raise ValueError(f"cannot read coordinate pairs from {path.get('id')}")
    xs = nums[0::2]
    ys = nums[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def union(boxes: list[tuple[float, float, float, float]]):
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--gap-mm", type=float, default=3.0)
    ap.add_argument("--margin-mm", type=float, default=1.5)
    ap.add_argument(
        "--target-content-width-mm",
        type=float,
        default=None,
        help="uniformly scale the stacked lettering to this finished sewn width",
    )
    ap.add_argument(
        "--pull-comp-mm",
        type=float,
        default=0.2,
        help="per-edge pull compensation applied downstream (default: 0.2mm)",
    )
    cli = ap.parse_args()

    if cli.gap_mm < 0:
        raise ValueError("--gap-mm cannot be negative")
    if cli.margin_mm < 0:
        raise ValueError("--margin-mm cannot be negative")
    if cli.pull_comp_mm < 0:
        raise ValueError("--pull-comp-mm cannot be negative")
    if cli.target_content_width_mm is not None and cli.target_content_width_mm <= 0:
        raise ValueError("--target-content-width-mm must be positive")
    if (cli.target_content_width_mm is not None
            and cli.target_content_width_mm <= 2 * cli.pull_comp_mm):
        raise ValueError("target width must exceed pull compensation on both edges")
    if cli.input.resolve() == cli.output.resolve():
        raise ValueError("input and output must be different files")

    tree = etree.parse(str(cli.input))
    root = tree.getroot()
    viewbox = [float(value) for value in root.get("viewBox", "").split()]
    if len(viewbox) != 4:
        raise ValueError("input SVG needs a four-number viewBox")
    split_x = viewbox[0] + viewbox[2] / 2.0

    paths = root.xpath(".//svg:path", namespaces={"svg": SVG_NS})
    transformed = [path.get("id", "<unnamed>") for path in paths if path.get("transform")]
    if transformed:
        raise ValueError(
            "input paths already have transforms; flatten them before stacking: "
            + ", ".join(transformed[:5])
        )
    measured = [(path, path_bbox(path)) for path in paths]
    top = [(path, box) for path, box in measured
           if (box[0] + box[2]) / 2.0 < split_x]
    bottom = [(path, box) for path, box in measured
              if (box[0] + box[2]) / 2.0 >= split_x]
    if not top or not bottom:
        raise ValueError("could not identify paths on both sides of the SVG")

    top_box = union([box for _, box in top])
    bottom_box = union([box for _, box in bottom])
    top_center = (top_box[0] + top_box[2]) / 2.0
    bottom_center = (bottom_box[0] + bottom_box[2]) / 2.0
    bottom_dx = top_center - bottom_center
    bottom_dy = top_box[3] + cli.gap_mm - bottom_box[1]

    moved_bottom = (
        bottom_box[0] + bottom_dx,
        bottom_box[1] + bottom_dy,
        bottom_box[2] + bottom_dx,
        bottom_box[3] + bottom_dy,
    )
    content = union([top_box, moved_bottom])
    content_width = content[2] - content[0]
    # Satin pull compensation expands both outside edges. Subtract it before
    # scaling so a requested 101.6mm design finishes at 101.6mm rather than
    # 102.0mm under the hat-twill preset's 0.2mm compensation.
    rail_target_width = (
        cli.target_content_width_mm - 2 * cli.pull_comp_mm
        if cli.target_content_width_mm else None
    )
    scale = rail_target_width / content_width if rail_target_width else 1.0
    origin_dx = cli.margin_mm - scale * content[0]
    origin_dy = cli.margin_mm - scale * content[1]

    for path, _ in top:
        path.set(
            "transform",
            f"translate({origin_dx:.3f} {origin_dy:.3f}) scale({scale:.6f})",
        )
    for path, _ in bottom:
        path.set(
            "transform",
            f"translate({origin_dx + scale * bottom_dx:.3f} "
            f"{origin_dy + scale * bottom_dy:.3f}) scale({scale:.6f})",
        )

    width = content_width * scale + 2 * cli.margin_mm
    height = (content[3] - content[1]) * scale + 2 * cli.margin_mm
    root.set("width", f"{width:.2f}mm")
    root.set("height", f"{height:.2f}mm")
    root.set("viewBox", f"0 0 {width:.2f} {height:.2f}")
    root.set("data-layout", "stacked")

    cli.output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(cli.output), encoding="utf-8", xml_declaration=False)
    print(
        f"stacked {len(top)} top paths + {len(bottom)} bottom paths; "
        f"move lower word ({bottom_dx:.2f}, {bottom_dy:.2f}) mm; "
        f"scale {scale:.4f}x"
    )
    if cli.target_content_width_mm:
        print(
            f"  rail width {rail_target_width:.1f}mm + "
            f"{cli.pull_comp_mm:.1f}mm/edge pull = "
            f"{cli.target_content_width_mm:.1f}mm sewn"
        )
    print(f"  -> {cli.output} ({width:.1f} x {height:.1f} mm)")


if __name__ == "__main__":
    main()
