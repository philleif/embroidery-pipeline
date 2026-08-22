"""Run a PEmbroider Processing sketch and pick up its .pes output.

Processing ships a `processing-java` CLI. We run the sketch with the preset
exposed as environment variables so generative code can read the materials
context and adjust density/length.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .materials import ResolvedPreset


def _find_processing_bin() -> str:
    override = os.environ.get("STITCH_PROCESSING_BIN")
    if override:
        return override
    on_path = shutil.which("processing-java")
    if on_path:
        return on_path
    mac_app = Path("/Applications/Processing.app/Contents/MacOS/processing-java")
    if mac_app.exists():
        return str(mac_app)
    raise RuntimeError(
        "processing-java not found. Install Processing 4 and ensure processing-java is on PATH, "
        "or set STITCH_PROCESSING_BIN."
    )


def run_sketch(
    sketch_dir: Path,
    preset: ResolvedPreset,
    output_pes: Path,
    timeout_s: float = 600.0,
) -> Path:
    """Run a PEmbroider sketch. The sketch must write a .pes into its own folder."""
    binary = _find_processing_bin()
    sketch_dir = sketch_dir.resolve()
    if not sketch_dir.is_dir():
        raise NotADirectoryError(sketch_dir)
    output_pes.parent.mkdir(parents=True, exist_ok=True)

    # Processing sketches commonly overwrite a fixed filename. Snapshot all
    # candidates before launch so a failed/no-op run can never silently deploy
    # yesterday's PES merely because it is still the newest file in the folder.
    before = {
        p.resolve(): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sketch_dir.glob("*.pes")
    }

    env = os.environ.copy()
    env.update(
        {
            "STITCH_PRIMARY_COLOR_HEX": preset.threads[0].hex,
            "STITCH_ROW_SPACING_MM": str(preset.row_spacing_mm),
            "STITCH_MAX_STITCH_LENGTH_MM": str(preset.max_stitch_length_mm),
            "STITCH_PULL_COMP_MM": str(preset.pull_compensation_mm),
            "STITCH_PRESET_NAME": preset.name,
        }
    )

    cmd = [binary, f"--sketch={sketch_dir}", "--run"]
    try:
        subprocess.run(cmd, check=True, env=env, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Processing sketch {sketch_dir} timed out after {timeout_s:g}s"
        ) from None

    candidates = []
    for candidate in sketch_dir.glob("*.pes"):
        signature = (candidate.stat().st_mtime_ns, candidate.stat().st_size)
        if before.get(candidate.resolve()) != signature:
            candidates.append(candidate)
    candidates.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    if not candidates:
        raise RuntimeError(
            f"sketch {sketch_dir} produced no new or modified .pes file; "
            "refusing to reuse a stale artifact"
        )
    if candidates[0].resolve() != output_pes.resolve():
        shutil.copy2(candidates[0], output_pes)
    return output_pes
