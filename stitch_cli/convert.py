"""pyembroidery-based read/write helpers.

Used as a finalization pass on .pes files produced by Ink/Stitch or
PEmbroider, and as the implementation of `stitch convert` for arbitrary
input formats (DST, EXP, JEF, VP3, XXX → PES v6).
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import pyembroidery

if TYPE_CHECKING:
    from .materials import ResolvedPreset


PES_WRITE_OPTIONS = {"version": "6"}


def _set_thread_metadata(target, source) -> None:
    target.brand = source.brand
    target.chart = source.brand
    target.catalog_number = source.code
    target.description = (
        f"{source.line} {source.code}".strip() if source.line else source.code
    )
    rgb = int(source.hex.lstrip("#"), 16)
    target.set_color((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)


def _stitch_bounds(pattern) -> tuple[float, float, float, float] | None:
    points = [
        (x, y)
        for x, y, command in pattern.stitches
        if command & pyembroidery.COMMAND_MASK == pyembroidery.STITCH
    ]
    if not points:
        return None
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def normalize_pes(
    input_path: Path,
    output_path: Path | None = None,
    preset: "ResolvedPreset | None" = None,
) -> Path:
    """Read any pyembroidery-supported file, write a PES v6 with clean metadata.

    If ``preset`` is provided, overwrite the threadlist with each preset
    thread's brand / code / description / hex so the PE900 displays e.g.
    "Madeira 1023" — matching the spool actually loaded — instead of the
    closest Brother-palette name pyembroidery picks by color-distance.
    The machine respects the file's thread brand when the on-screen
    "thread brand" setting is left at "Original" (the default).
    """
    output_path = output_path or input_path
    pattern = pyembroidery.read(str(input_path))

    if preset is not None and preset.threads:
        if not pattern.threadlist:
            pattern.add_thread(preset.threads[0].hex)
        if len(preset.threads) == 1:
            # A one-color material preset intentionally makes every color
            # block use the same physical spool, including repeated blocks.
            for target in pattern.threadlist:
                _set_thread_metadata(target, preset.threads[0])
        else:
            if len(pattern.threadlist) != len(preset.threads):
                raise RuntimeError(
                    f"PES contains {len(pattern.threadlist)} color blocks but preset "
                    f"{preset.name!r} specifies {len(preset.threads)} threads. "
                    "List one preset thread per color block, in sew order."
                )
            for target, source in zip(pattern.threadlist, preset.threads):
                _set_thread_metadata(target, source)

    pyembroidery.write_pes(pattern, str(output_path), PES_WRITE_OPTIONS)
    return output_path


def offset_pes(
    input_path: Path,
    output_path: Path,
    x_mm: float = 0.0,
    y_mm: float = 0.0,
    hoop_x_mm: float = 130.0,
    hoop_y_mm: float = 180.0,
) -> Path:
    """Shift the visible design within the hoop by (x_mm, y_mm).

    The machine ignores a PES file's absolute origin and always centers
    the bbox in the hoop. So we can't move the design just by translating
    every stitch — pyembroidery's writer renormalizes and the machine
    would re-center anyway. Instead we extend the bbox in the *opposite*
    direction of the desired shift by inserting a single phantom JUMP
    stitch; when the machine centers the enlarged bbox, the visible
    content lands offset by the requested amount.

    Constraint: ``visible_dimension + 2 * abs(offset)`` must fit within
    the hoop on each axis, so the achievable offset is
    ``(hoop_axis - design_dim) / 2`` mm. The PE900 field is 130mm across
    by 180mm deep, so there is far more room front-to-back than sideways.
    """
    pattern = pyembroidery.read(str(input_path))
    if not pattern.stitches:
        raise RuntimeError(f"{input_path} has no stitches")
    visible_bounds = _stitch_bounds(pattern)
    if visible_bounds is None:
        raise RuntimeError(f"{input_path} has no stitch penetrations")
    min_x, min_y, max_x, max_y = visible_bounds  # 0.1mm units
    w_mm = (max_x - min_x) / 10.0
    h_mm = (max_y - min_y) / 10.0

    if w_mm + 2 * abs(x_mm) > hoop_x_mm:
        raise RuntimeError(
            f"x offset {x_mm:+.1f}mm too large: design width {w_mm:.1f}mm + "
            f"2*|offset| = {w_mm + 2 * abs(x_mm):.1f}mm exceeds the {hoop_x_mm}mm field. "
            f"Max x offset for this design: ±{(hoop_x_mm - w_mm) / 2:.1f}mm"
        )
    if h_mm + 2 * abs(y_mm) > hoop_y_mm:
        raise RuntimeError(
            f"y offset {y_mm:+.1f}mm too large: design height {h_mm:.1f}mm + "
            f"2*|offset| = {h_mm + 2 * abs(y_mm):.1f}mm exceeds the {hoop_y_mm}mm field. "
            f"Max y offset for this design: ±{(hoop_y_mm - h_mm) / 2:.1f}mm"
        )

    dx_units = int(round(x_mm * 10))
    dy_units = int(round(y_mm * 10))
    # Phantom point goes in the *opposite* direction of the desired shift.
    # Extending the bbox by 2*|offset| on that side gives 1*|offset| of
    # visible shift after the machine re-centers.
    phantom_x = (min_x - 2 * dx_units) if dx_units > 0 else (max_x - 2 * dx_units)
    phantom_y = (min_y - 2 * dy_units) if dy_units > 0 else (max_y - 2 * dy_units)

    new_pattern = pyembroidery.EmbPattern()
    new_pattern.threadlist.extend(pattern.threadlist)
    first = next(
        s for s in pattern.stitches
        if s[2] & pyembroidery.COMMAND_MASK == pyembroidery.STITCH
    )
    # Two leading JUMPs: out to the bbox-extending point, then back to the
    # actual first stitch location. JUMPs don't sew but do widen the bbox.
    new_pattern.add_stitch_absolute(pyembroidery.JUMP, phantom_x, phantom_y)
    new_pattern.add_stitch_absolute(pyembroidery.JUMP, first[0], first[1])
    for s in pattern.stitches:
        cmd = s[2] & pyembroidery.COMMAND_MASK
        if cmd == pyembroidery.END:
            new_pattern.add_command(s[2])
        else:
            new_pattern.add_stitch_absolute(s[2], s[0], s[1])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pyembroidery.write_pes(new_pattern, str(output_path), PES_WRITE_OPTIONS)
    return output_path


def scale_pes(input_path: Path, output_path: Path, factor: float) -> Path:
    """Geometrically scale every stitch by ``factor`` (preserving threads).

    Used to trim a design to an exact target size after generation. Coordinates
    are 0.1mm units. A small factor (near 1.0) is safe; large factors also shift
    stitch density, so prefer getting close via the source (e.g. a font scale)
    and using this only for the final fit."""
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError(f"scale factor must be finite and > 0, got {factor!r}")
    pattern = pyembroidery.read(str(input_path))
    out = pyembroidery.EmbPattern()
    out.threadlist.extend(pattern.threadlist)
    for s in pattern.stitches:
        cmd = s[2] & pyembroidery.COMMAND_MASK
        if cmd == pyembroidery.END:
            out.add_command(s[2])
        else:
            out.add_stitch_absolute(
                s[2], int(round(s[0] * factor)), int(round(s[1] * factor))
            )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pyembroidery.write_pes(out, str(output_path), PES_WRITE_OPTIONS)
    return output_path


def describe(input_path: Path) -> dict:
    """Return a small summary for diagnostics."""
    pattern = pyembroidery.read(str(input_path))
    bounds = pattern.bounds()
    stitch_bounds = _stitch_bounds(pattern)
    commands = Counter(
        command & pyembroidery.COMMAND_MASK
        for _, _, command in pattern.stitches
    )
    return {
        "stitch_count": commands[pyembroidery.STITCH],
        "command_count": len(pattern.stitches),
        "jump_count": commands[pyembroidery.JUMP],
        "trim_count": commands[pyembroidery.TRIM],
        "color_change_count": (
            commands[pyembroidery.COLOR_CHANGE] + commands[pyembroidery.STOP]
        ),
        "thread_count": len(pattern.threadlist),
        "bounds_mm": tuple(round(v / 10, 2) for v in bounds) if bounds else None,
        "stitch_bounds_mm": (
            tuple(round(v / 10, 2) for v in stitch_bounds) if stitch_bounds else None
        ),
        "extras": dict(pattern.extras) if pattern.extras else {},
    }
