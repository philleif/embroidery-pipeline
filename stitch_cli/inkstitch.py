"""Shell out to Ink/Stitch to convert a tuned SVG into a .pes file.

Ink/Stitch ships as an Inkscape extension. On Inkscape 1.4+, the standard
install drops the extension under the user config dir
(`~/Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/inkstitch/`)
and the executable lives inside the bundled `inkstitch.app`. The exact
invocation has shifted across releases; we keep the call site small here
so it's easy to update.

Strategy:
1. Locate the binary, configurable via the STITCH_INKSTITCH_BIN env var.
2. Invoke with --extension=zip --format-pes=true and capture stdout, since
   the current binary writes the zip archive to stdout and ignores any
   --output= argument.
3. Unzip and rename the resulting .pes to the requested output name.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def _find_inkstitch_bin() -> str:
    override = os.environ.get("STITCH_INKSTITCH_BIN")
    if override:
        return override
    on_path = shutil.which("inkstitch")
    if on_path:
        return on_path
    candidates = [
        # Inkscape 1.4+ user-config extensions dir (current default install).
        Path.home()
        / "Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/inkstitch/inkstitch.app/Contents/MacOS/inkstitch",
        # Older system-wide install (pre-1.4 Inkscape extensions layout).
        Path("/Applications/Inkscape.app/Contents/Resources/extensions/inkstitch/bin/inkstitch"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise RuntimeError(
        "Ink/Stitch binary not found. Set STITCH_INKSTITCH_BIN, install Ink/Stitch, "
        "or put `inkstitch` on PATH."
    )


def svg_to_pes(tuned_svg: Path, output_pes: Path, timeout_s: float = 600.0) -> Path:
    """Run Ink/Stitch on tuned_svg, return path to the produced .pes file."""
    binary = _find_inkstitch_bin()
    output_pes.parent.mkdir(parents=True, exist_ok=True)

    # A unique, automatically-cleaned workdir prevents a prior failed build's
    # zip/member from being mistaken for current output. The final replace is
    # atomic, so a failed conversion also leaves the last known-good PES intact.
    with tempfile.TemporaryDirectory(
        prefix=f".inkstitch-{output_pes.stem}-", dir=output_pes.parent
    ) as td:
        workdir = Path(td)
        zip_out = workdir / "out.zip"
        cmd = [
            binary,
            "--extension=zip",
            "--format-pes=true",
            str(tuned_svg),
        ]
        # Ink/Stitch writes the zip archive to stdout; on a digitizing error it
        # prints a traceback to stderr and sometimes still exits 0. Validate the
        # archive and surface Ink/Stitch's message instead of a BadZipFile.
        try:
            with zip_out.open("wb") as zf_out:
                proc = subprocess.run(
                    cmd, stdout=zf_out, stderr=subprocess.PIPE, timeout=timeout_s
                )
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr or b""
            stderr_tail = stderr.decode(errors="replace").strip()[-2000:]
            raise RuntimeError(
                f"Ink/Stitch conversion timed out after {timeout_s:g}s for "
                f"{tuned_svg.name}\n--- Ink/Stitch said ---\n"
                f"{stderr_tail or '(no stderr)'}"
            ) from None
        stderr_tail = proc.stderr.decode(errors="replace").strip()[-2000:]

        try:
            zf = zipfile.ZipFile(zip_out)
        except zipfile.BadZipFile:
            raise RuntimeError(
                "Ink/Stitch produced no embroidery output — likely a digitizing "
                f"error in {tuned_svg.name} (e.g. a degenerate satin column).\n"
                f"--- Ink/Stitch said ---\n{stderr_tail or '(no stderr)'}"
            ) from None
        with zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise RuntimeError(f"Ink/Stitch produced a corrupt zip member: {bad_member}")
            pes_members = [n for n in zf.namelist() if n.lower().endswith(".pes")]
            if not pes_members:
                raise RuntimeError(f"Ink/Stitch produced no .pes inside its output zip")
            produced = workdir / "result.pes"
            produced.write_bytes(zf.read(pes_members[0]))
        produced.replace(output_pes)
    return output_pes


def run_effect(
    extension: str,
    input_svg: Path,
    output_svg: Path,
    params: dict[str, str | bool] | None = None,
    ids: list[str] | None = None,
    timeout_s: float = 180.0,
) -> Path:
    """Run an Ink/Stitch *effect* extension headless.

    Effect extensions (e.g. ``fill_to_satin``, ``auto_satin``) read an SVG and
    write the modified SVG to stdout. ``extension`` is the internal short
    name (the value Ink/Stitch's ``--extension=`` flag expects). ``params``
    become ``--key=value`` flags (booleans serialize to ``true``/``false``).
    ``ids`` become repeated ``--id=<elemid>`` flags to limit the effect to
    specific elements (mirrors Inkscape's selection mechanism).

    CAUTION: selection-based effects (fill_to_satin, auto_satin, ...) HANG
    forever when called with no ``ids`` — headless there is no Inkscape
    selection to fall back on — hence the hard ``timeout_s``. Error text goes
    to stderr while exit stays 0 and stdout empty, so an empty result raises
    with that stderr instead of silently writing an empty file.
    """
    selection_effects = {"fill_to_satin", "auto_satin", "stroke_to_satin"}
    if extension in selection_effects and not ids:
        raise ValueError(
            f"Ink/Stitch effect {extension!r} requires at least one selected element id"
        )
    binary = _find_inkstitch_bin()
    output_svg.parent.mkdir(parents=True, exist_ok=True)

    cmd = [binary, f"--extension={extension}"]
    if ids:
        cmd.extend(f"--id={i}" for i in ids)
    if params:
        for k, v in params.items():
            if isinstance(v, bool):
                v = "true" if v else "false"
            cmd.append(f"--{k}={v}")
    cmd.append(str(input_svg))

    try:
        res = subprocess.run(cmd, check=True, capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"inkstitch --extension={extension} timed out after {timeout_s:g}s; "
            "simplify the selected compound paths or use a pre-digitized satin font"
        ) from None
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode(errors="replace").strip()[-2000:]
        raise RuntimeError(
            f"inkstitch --extension={extension} exited {exc.returncode}"
            f"\n--- Ink/Stitch said ---\n{stderr or '(no stderr)'}"
        ) from None
    if not res.stdout.strip():
        raise RuntimeError(
            f"inkstitch --extension={extension} produced no output"
            + (f": {res.stderr.decode(errors='replace').strip()}"
               if res.stderr.strip() else " (no stderr)"))
    output_svg.write_bytes(res.stdout)
    return output_svg
