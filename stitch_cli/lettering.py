"""Lettering pipeline — generate PES directly from Ink/Stitch's bundled
satin-digitized fonts instead of auto-filling vector outlines.

For typography we don't want auto-fill or fill_to_satin on Arial Black
outlines — those are general-purpose tools and they look unprofessional
on letterforms. Ink/Stitch ships ~150 fonts that are hand-digitized as
proper satin columns; we drive those via the `batch_lettering` output
extension (which is fully CLI-friendly and supports `--trim=glyph` for
between-letter trims so the machine doesn't leave visible jump threads).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
import math
from pathlib import Path

import pyembroidery

from . import inkstitch


# Minimal SVG that satisfies batch_lettering's "input SVG" requirement —
# the lettering itself comes from --text, not from the document.
_EMPTY_SVG = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<svg xmlns="http://www.w3.org/2000/svg" '
    b'width="100mm" height="100mm" viewBox="0 0 100 100"/>'
)


def list_fonts() -> list[str]:
    """Return the display names of Ink/Stitch's bundled satin fonts.

    Reads each font's font.json next to the inkstitch binary so users can
    discover valid --font values (e.g. "Manuskript Gothisch", "Barstitch Bold").
    """
    import json

    binary = Path(inkstitch._find_inkstitch_bin())
    # .../inkstitch.app/Contents/MacOS/inkstitch → .../Contents/Resources/fonts
    fonts_dir = binary.parent.parent / "Resources" / "fonts"
    names: list[str] = []
    if not fonts_dir.is_dir():
        return names
    for font_json in sorted(fonts_dir.glob("*/font.json")):
        try:
            data = json.loads(font_json.read_text(encoding="utf-8"))
            name = data.get("name")
            if name:
                names.append(name)
        except (ValueError, OSError):
            continue
    return names


def make_lettering_pes(
    text: str,
    font: str,
    output_pes: Path,
    scale: int = 100,
    trim: str = "glyph",
    text_align: str = "center",
) -> Path:
    """Run Ink/Stitch `batch_lettering` to produce a single-line PES.

    The font is the Ink/Stitch font *name* (e.g. "Barstitch Bold"), not a
    file path. `scale` is a percentage (font's own min_scale / max_scale).
    `trim` ∈ {"off", "line", "word", "glyph"} — "glyph" inserts a trim
    after every letter so the machine cuts jumps cleanly.
    """
    if not text:
        raise ValueError("lettering text cannot be empty")
    if not font:
        raise ValueError("lettering font cannot be empty")
    if scale <= 0:
        raise ValueError(f"lettering scale must be > 0, got {scale}")
    if trim not in {"off", "line", "word", "glyph"}:
        raise ValueError(f"unknown trim mode {trim!r}")

    binary = inkstitch._find_inkstitch_bin()
    output_pes.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        in_svg = Path(td) / "in.svg"
        in_svg.write_bytes(_EMPTY_SVG)
        zip_out = Path(td) / "out.zip"
        cmd = [
            binary,
            "--extension=batch_lettering",
            f"--text={text}",
            f"--font={font}",
            f"--scale={scale}",
            f"--trim={trim}",
            f"--text-align={text_align}",
            "--file-formats=pes",
            str(in_svg),
        ]
        try:
            with zip_out.open("wb") as f:
                proc = subprocess.run(
                    cmd, stdout=f, stderr=subprocess.PIPE, timeout=300
                )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Ink/Stitch batch lettering timed out after 300s for {text!r}"
            ) from None
        stderr_tail = proc.stderr.decode(errors="replace").strip()[-2000:]
        if proc.returncode:
            raise RuntimeError(
                f"Ink/Stitch batch lettering exited {proc.returncode} for {text!r}"
                f"\n--- Ink/Stitch said ---\n{stderr_tail or '(no stderr)'}"
            )
        try:
            zf = zipfile.ZipFile(zip_out)
        except zipfile.BadZipFile:
            raise RuntimeError(
                "Ink/Stitch batch lettering produced no valid archive"
                f"\n--- Ink/Stitch said ---\n{stderr_tail or '(no stderr)'}"
            ) from None
        with zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise RuntimeError(f"batch_lettering produced corrupt member {bad_member!r}")
            pes_members = [n for n in zf.namelist() if n.lower().endswith(".pes")]
            if not pes_members:
                raise RuntimeError(
                    f"batch_lettering wrote no .pes (zip contents: {zf.namelist()})"
                )
            # batch_lettering writes one .pes per line; we generate one line at a
            # time, so a single member is expected.
            zf.extract(pes_members[0], td)
            produced = Path(td) / pes_members[0]
            with tempfile.NamedTemporaryFile(
                prefix=f".{output_pes.stem}-", suffix=".pes", dir=output_pes.parent,
                delete=False,
            ) as temp_file:
                temp_output = Path(temp_file.name)
            try:
                shutil.copyfile(produced, temp_output)
                temp_output.replace(output_pes)
            finally:
                temp_output.unlink(missing_ok=True)
    return output_pes


def _pattern_stitch_bounds(pattern) -> tuple[float, float, float, float] | None:
    points = [
        (x, y) for x, y, command in pattern.stitches
        if command & pyembroidery.COMMAND_MASK == pyembroidery.STITCH
    ]
    if not points:
        return None
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def merge_pes_vertical(
    pes_files: list[Path],
    output_pes: Path,
    gap_mm: float | list[float] = 8.0,
    hoop_mm: float = 100.0,
) -> Path:
    """Stack multiple PES patterns vertically, anchored so the merged bbox
    starts at (0, 0) and extends positive — the convention Ink/Stitch's own
    `svg_to_pes` produces and which the Brother PE900 places correctly
    centered in the hoop (PEC encodes stitches as deltas; the machine reads
    the PEC bbox header for placement).

    All patterns are remapped to share a single thread (the first
    pattern's) so the result is one continuous color block.

    `gap_mm` can be a single float (uniform gap) or a list of `n-1` floats
    (per-pair gap, useful when e.g. a phone-number line should sit further
    below a wordmark than the wordmark lines sit from each other).

    pyembroidery uses 0.1mm units internally; conversion is handled here.
    """
    if not pes_files:
        raise ValueError("merge_pes_vertical needs at least one input PES")
    patterns = [pyembroidery.read(str(p)) for p in pes_files]
    if any(not pattern.threadlist for pattern in patterns):
        raise RuntimeError("every lettering PES must contain thread metadata")
    bounds = [_pattern_stitch_bounds(pat) for pat in patterns]
    if any(bound is None for bound in bounds):
        raise RuntimeError("every lettering PES must contain stitch penetrations")
    heights = [b[3] - b[1] for b in bounds]
    widths = [b[2] - b[0] for b in bounds]

    if isinstance(gap_mm, (int, float)):
        gaps = [float(gap_mm) * 10] * max(0, len(patterns) - 1)
    else:
        if len(gap_mm) != len(patterns) - 1:
            raise ValueError(
                f"gap_mm list length {len(gap_mm)} must equal n_patterns-1 = {len(patterns)-1}"
            )
        gaps = [float(g) * 10 for g in gap_mm]
    if any(not math.isfinite(gap) or gap < 0 for gap in gaps):
        raise ValueError("all lettering gaps must be finite and >= 0")
    if not math.isfinite(hoop_mm) or hoop_mm <= 0:
        raise ValueError(f"hoop_mm must be finite and > 0, got {hoop_mm!r}")

    total_h = sum(heights) + sum(gaps)
    max_w = max(widths)
    hoop = hoop_mm * 10
    if total_h > hoop:
        raise RuntimeError(
            f"merged height {total_h/10:.1f}mm exceeds {hoop_mm}mm stitch field"
        )
    if max_w > hoop:
        raise RuntimeError(
            f"widest row {max_w/10:.1f}mm exceeds {hoop_mm}mm stitch field"
        )

    out = pyembroidery.EmbPattern()
    out.threadlist.append(patterns[0].threadlist[0])

    # Anchor the overall bbox at (0, 0): each row centered horizontally
    # within the widest row's span, rows stacked top-to-bottom from y=0.
    cursor_y = 0
    for i, (pat, (minx, miny, maxx, maxy), w, h) in enumerate(zip(patterns, bounds, widths, heights)):
        dx = (max_w - w) / 2 - minx  # row spans (max_w - w)/2 to (max_w + w)/2
        dy = cursor_y - miny
        first_stitch = next(
            s for s in pat.stitches
            if s[2] & pyembroidery.COMMAND_MASK == pyembroidery.STITCH
        )
        # Explicitly move with the needle up before each row. Without this, a
        # source whose first command is STITCH can sew a diagonal connector
        # from the preceding row (or from design origin for row one).
        if i:
            out.add_command(pyembroidery.TRIM)
        out.add_stitch_absolute(
            pyembroidery.JUMP, first_stitch[0] + dx, first_stitch[1] + dy
        )
        for s in pat.stitches:
            cmd = s[2] & pyembroidery.COMMAND_MASK
            if cmd == pyembroidery.END:
                continue  # End is added once at the very end.
            if cmd in (pyembroidery.COLOR_CHANGE, pyembroidery.STOP):
                continue  # merged lettering is deliberately one physical thread
            out.add_stitch_absolute(s[2], s[0] + dx, s[1] + dy)
        cursor_y += h
        if i < len(gaps):
            cursor_y += gaps[i]
    out.add_command(pyembroidery.END)

    output_pes.parent.mkdir(parents=True, exist_ok=True)
    pyembroidery.write_pes(out, str(output_pes), {"version": "6"})
    return output_pes


def build_lettering_swatch(output_pes: Path) -> Path:
    """Build the DL-logo lettering swatch — one row of LOSERS in Barstitch
    Bold + one row of the phone number in Ink/Stitch Small Font, both with
    per-glyph trims, merged into a single centered PES.
    """
    workdir = output_pes.parent / f".lettering-{output_pes.stem}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        losers_pes = make_lettering_pes(
            text="LOSERS",
            font="Barstitch Bold",
            scale=50,           # base 23mm → ~13mm cap height; close to logo
            output_pes=workdir / "losers.pes",
        )
        phone_pes = make_lettering_pes(
            text="(917)-524-7853",
            font="Ink/Stitch Small Font",
            scale=135,          # base 5.08mm → ~6.9mm digit height
            output_pes=workdir / "phone.pes",
        )
        merge_pes_vertical([losers_pes, phone_pes], output_pes, gap_mm=8.0)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return output_pes


def build_lettering_logo(output_pes: Path) -> Path:
    """Build the full DL-logo: DESPERATE / LOSERS / (917)-524-7853, all
    properly satin-digitized via Ink/Stitch's batch_lettering with
    per-glyph trims, stacked and centered on the 4×4" canvas.

    Layout — three lines, tight gap between wordmark lines and a larger
    gap before the phone-number row (mirrors the original logo's spacing).
    """
    workdir = output_pes.parent / f".lettering-{output_pes.stem}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        desperate_pes = make_lettering_pes(
            text="DESPERATE",
            font="Barstitch Bold",
            scale=50,
            output_pes=workdir / "desperate.pes",
        )
        losers_pes = make_lettering_pes(
            text="LOSERS",
            font="Barstitch Bold",
            scale=50,
            output_pes=workdir / "losers.pes",
        )
        phone_pes = make_lettering_pes(
            text="(917)-524-7853",
            font="Ink/Stitch Small Font",
            scale=135,
            output_pes=workdir / "phone.pes",
        )
        merge_pes_vertical(
            [desperate_pes, losers_pes, phone_pes],
            output_pes,
            gap_mm=[3.5, 9.0],  # tight between wordmark lines; breathing room above phone
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return output_pes
