"""Atomic machine-artifact bundles and freshness manifests."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Iterable

import pyembroidery


ARTIFACT_SUFFIXES = (
    ".manifest.json",
    ".preview.png",
    ".preview.svg",
    ".tuned.svg",
    ".qa.png",
    ".pes",
    ".dst",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_for_artifact(path: Path) -> Path:
    """Return the adjacent manifest belonging to any standard bundle file."""
    for suffix in ARTIFACT_SUFFIXES:
        if path.name.endswith(suffix):
            basename = path.name[:-len(suffix)]
            return path.with_name(f"{basename}.manifest.json")
    return path.with_name(f"{path.stem}.manifest.json")


def write_machine_derivatives(pes: Path, directory: Path, name: str) -> dict[str, Path]:
    """Create DST and both previews from one finalized in-memory pattern."""
    pattern = pyembroidery.read(str(pes))
    dst = directory / f"{name}.dst"
    preview_svg = directory / f"{name}.preview.svg"
    preview_png = directory / f"{name}.preview.png"
    pyembroidery.write_dst(pattern, str(dst))
    with preview_svg.open("wb") as handle:
        pyembroidery.write_svg(pattern, handle)
    with preview_png.open("wb") as handle:
        pyembroidery.write_png(pattern, handle)
    return {
        "pes": pes,
        "dst": dst,
        "preview_svg": preview_svg,
        "preview_png": preview_png,
    }


def write_manifest(
    path: Path,
    *,
    source: Path,
    preset_name: str,
    audit_profile: str | None,
    metrics: dict,
    audit_failures: list[str],
    preflight: dict,
    compiled_objects: list[dict],
    artifacts: dict[str, Path],
    visual_qa: dict | None = None,
) -> Path:
    artifact_entries = {
        key: {
            "file": artifact.name,
            "bytes": artifact.stat().st_size,
            "sha256": sha256(artifact),
        }
        for key, artifact in artifacts.items()
    }
    payload = {
        "schema": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "sha256": sha256(source),
        },
        "preset": preset_name,
        "audit_profile": audit_profile,
        "audit": {
            "passed": not audit_failures,
            "failures": audit_failures,
            "metrics": metrics,
        },
        "preflight": preflight,
        "compiled_objects": compiled_objects,
        "visual_qa": visual_qa,
        "artifacts": artifact_entries,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def promote_bundle(stage_dir: Path, output_dir: Path, names: Iterable[str],
                   *, manifest_name: str) -> None:
    """Promote a staged bundle, writing the manifest commit marker last."""
    output_dir.mkdir(parents=True, exist_ok=True)
    names = list(names)
    for name in names:
        if name == manifest_name:
            continue
        source = stage_dir / name
        if source.exists():
            os.replace(source, output_dir / name)
    manifest = stage_dir / manifest_name
    if not manifest.exists():
        raise RuntimeError("artifact bundle has no manifest commit marker")
    os.replace(manifest, output_dir / manifest_name)


def verify_manifest(
    manifest_path: Path,
    *,
    verify_source: bool = True,
) -> tuple[dict, list[str]]:
    payload = json.loads(manifest_path.read_text())
    failures: list[str] = []
    if payload.get("schema") != 1:
        failures.append(f"unsupported manifest schema {payload.get('schema')!r}")
    source_entry = payload.get("source", {})
    if verify_source and source_entry.get("path"):
        source = Path(source_entry["path"])
        if not source.exists():
            failures.append(f"source: missing {source}")
        else:
            if source.stat().st_size != source_entry.get("bytes"):
                failures.append(f"source: byte-size mismatch for {source.name}")
            if sha256(source) != source_entry.get("sha256"):
                failures.append(f"source: SHA-256 mismatch for {source.name}")
    for key, entry in payload.get("artifacts", {}).items():
        artifact = manifest_path.parent / entry["file"]
        if not artifact.exists():
            failures.append(f"{key}: missing {artifact.name}")
            continue
        if artifact.stat().st_size != entry["bytes"]:
            failures.append(f"{key}: byte-size mismatch for {artifact.name}")
        actual = sha256(artifact)
        if actual != entry["sha256"]:
            failures.append(f"{key}: SHA-256 mismatch for {artifact.name}")
    return payload, failures


def copy_final_tuned_svg(source: Path, destination: Path) -> Path:
    shutil.copy2(source, destination)
    return destination
