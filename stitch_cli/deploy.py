"""Validate and drop a .pes onto a FAT32 USB stick the PE900 will read, and print the
load-out checklist for the operator.

The PE900 wants files at the root of the volume with a restricted filename.
This module treats deployment as the final safety gate: parse the PES, enforce
machine limits, copy atomically, and verify the bytes that landed on the stick.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import audit as audit_mod
from . import artifacts
from . import convert
from .materials import ResolvedPreset


HOOP_MAX_X_MM = 130.0
HOOP_MAX_Y_MM = 180.0
PE900_MAX_STITCHES = 200_000
_PES_FILENAME = re.compile(r"[A-Za-z0-9_-]+\.pes", re.IGNORECASE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_fat32(device_path: Path) -> bool:
    """Best-effort filesystem check on macOS via diskutil."""
    try:
        result = subprocess.run(
            ["diskutil", "info", str(device_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    for line in result.stdout.splitlines():
        if "File System Personality" in line or "Type (Bundle)" in line:
            lowered = line.lower()
            # exFAT contains the substring "fat" but is not FAT32.
            if "fat32" in lowered or "ms-dos fat" in lowered or "msdos" in lowered:
                return True
    return False


def deploy(
    pes_path: Path,
    device: Path,
    preset: ResolvedPreset | None = None,
    *,
    allow_audit_failures: bool = False,
) -> Path:
    """Copy pes_path to the root of `device`, return the destination path.

    Prints a materials checklist if a preset was supplied so the operator
    knows which thread to load first.
    """
    if not pes_path.exists():
        raise FileNotFoundError(pes_path)
    if not pes_path.is_file():
        raise RuntimeError(f"{pes_path} is not a regular file")
    if not _PES_FILENAME.fullmatch(pes_path.name):
        raise ValueError(
            f"PE900 filename {pes_path.name!r} is unsafe; use only A-Z, a-z, "
            "0-9, '-' and '_' with a .pes extension"
        )
    if not device.exists() or not device.is_dir():
        raise NotADirectoryError(f"{device} is not a mounted volume")

    # A from-svg production bundle carries an adjacent manifest.  Verify it
    # before parsing or copying so a stale PES, a partially promoted bundle,
    # or a source changed since the last build cannot reach the USB stick.
    # Direct/legacy PES generators do not all emit manifests yet, so absence
    # remains an explicit warning rather than an unconditional failure.
    manifest = artifacts.manifest_for_artifact(pes_path)
    if manifest.exists():
        payload, manifest_failures = artifacts.verify_manifest(manifest)
        pes_entries = [
            entry for entry in payload.get("artifacts", {}).values()
            if entry.get("file") == pes_path.name
        ]
        if not pes_entries:
            manifest_failures.append(
                f"manifest does not record machine file {pes_path.name}"
            )
        if manifest_failures:
            raise RuntimeError(
                "refusing to deploy a stale or incomplete artifact bundle: "
                + "; ".join(manifest_failures)
            )
        print(f"verified build manifest: {manifest.name}")
    else:
        print(
            "WARNING: no adjacent build manifest; source freshness and bundle "
            "integrity could not be verified"
        )

    summary = convert.describe(pes_path)
    bounds = summary["bounds_mm"]
    if not summary["stitch_count"]:
        raise RuntimeError(f"{pes_path} contains no stitch penetrations")
    if summary["stitch_count"] > PE900_MAX_STITCHES:
        raise RuntimeError(
            f"{summary['stitch_count']} stitches exceeds the PE900 limit of "
            f"{PE900_MAX_STITCHES}"
        )
    if bounds:
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        if width > HOOP_MAX_X_MM or height > HOOP_MAX_Y_MM:
            raise RuntimeError(
                f"design is {width:.1f}x{height:.1f}mm, exceeding the PE900 "
                f"{HOOP_MAX_X_MM:.0f}x{HOOP_MAX_Y_MM:.0f}mm field"
            )
    if preset is not None and len(preset.threads) > 1:
        if summary["thread_count"] != len(preset.threads):
            raise RuntimeError(
                f"PES has {summary['thread_count']} color blocks but preset "
                f"{preset.name!r} lists {len(preset.threads)} threads"
            )

    # Deployment is the final machine boundary. The source technique may be
    # unknown here, so enforce only the universal mixed-profile checks: local
    # penetration hotspots and pathological trim fragmentation. A known-unsafe
    # file requires an explicit command-line override rather than a warning
    # that can scroll past while copying to the USB stick.
    quality, failures = audit_mod.audit(
        pes_path, verbose=False, profile="mixed"
    )
    if failures and not allow_audit_failures:
        details = []
        if "peak_1mm" in failures:
            details.append(
                f"peak_1mm={quality['peak_1mm']:.0f} "
                f"(limit {audit_mod.BANDS['peak_1mm'][1]:g})"
            )
        if "trims_per_1k" in failures:
            details.append(
                f"trims_per_1k={quality['trims_per_1k']:.1f} "
                f"(limit {audit_mod.BANDS['trims_per_1k'][1]:g})"
            )
        raise RuntimeError(
            "refusing to deploy PES that fails universal quality checks: "
            + ", ".join(details or failures)
            + "; rebuild it or pass --allow-audit-failures for a deliberate "
              "scrap-only test"
        )
    if failures:
        print(
            "WARNING: deploying despite quality failures: "
            + ", ".join(failures)
        )

    if not _is_fat32(device):
        print(f"WARNING: {device} does not look like FAT32. The PE900 may not read it.")

    dest = device / pes_path.name
    if shutil.disk_usage(device).free < pes_path.stat().st_size:
        raise RuntimeError(f"not enough free space on {device}")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{pes_path.stem}-", suffix=".tmp", dir=device, delete=False
        ) as temp_file:
            temp_path = Path(temp_file.name)
        shutil.copy2(pes_path, temp_path)
        # Windows requires write access when flushing a file handle.
        with temp_path.open("r+b") as copied:
            os.fsync(copied.fileno())
        temp_path.replace(dest)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    try:
        sync_binary = shutil.which("sync")
        if sync_binary:
            subprocess.run([sync_binary], check=False, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timed out while flushing writes to {device}") from None
    if dest.stat().st_size != pes_path.stat().st_size or _sha256(dest) != _sha256(pes_path):
        raise RuntimeError(f"verification failed after copying {pes_path.name} to {device}")

    print(f"\ncopied and verified {pes_path.name} -> {dest}")
    if preset is not None:
        print("\n--- LOAD-OUT CHECKLIST ---")
        print(f"preset: {preset.name}")
        print(f"fabric: {preset.fabric.id}  ({preset.fabric.notes})")
        print(f"needle: {preset.needle.id}  ({preset.needle.size}, {preset.needle.point})")
        print(f"stabilizer: {preset.fabric.stabilizer}")
        for i, thread in enumerate(preset.threads, 1):
            print(
                f"thread #{i}: {thread.brand} {thread.line} {thread.code}  "
                f"({thread.hex}, {thread.weight}wt {thread.fiber}) — {thread.notes}"
            )
        print("\n--- AT THE MACHINE ---")
        print("1. Insert USB stick.")
        print("2. Press the USB icon on the touchscreen.")
        print("3. Select the design.")
        print("4. Hoop the fabric with the listed stabilizer.")
        print("5. Thread the listed needle with thread #1.")
        print("6. Press the green Start button.")
        print("7. When the machine pauses for a color change, swap to the next thread.\n")
    return dest
