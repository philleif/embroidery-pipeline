"""Load and resolve the materials inventory (threads, needles, fabrics, presets).

A `ResolvedPreset` is the flattened, ready-to-apply form: it bundles the chosen
fabric and needle objects with the ordered thread list and the digitization
parameters, so callers don't have to re-resolve cross-references.
"""

from __future__ import annotations

import os
import tomllib
import math
import re
from dataclasses import dataclass
from pathlib import Path


MATERIALS_DIR = Path(__file__).resolve().parent.parent / "materials"


def default_materials_dir() -> Path:
    """Use an explicit inventory, a project inventory, or shipped defaults."""
    override = os.environ.get("STITCH_MATERIALS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    local = Path.cwd() / "materials"
    if local.is_dir():
        return local
    if MATERIALS_DIR.is_dir():
        return MATERIALS_DIR
    return Path(__file__).resolve().parent / "data" / "materials"

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_SATIN_UNDERLAYS = {"none", "center-walk", "center-walk+zigzag", "center-walk+contour"}
# Ink/Stitch exposes fill underlay as a sparse fill grid, not independent
# edge-walk/zigzag switches.  Keep the existing preset labels as compatible
# policy names; both non-"none" forms enable that sparse grid.
_FILL_UNDERLAYS = {"none", "edge-walk", "edge-walk+zigzag"}


@dataclass(frozen=True)
class Thread:
    id: str
    brand: str
    line: str
    code: str
    hex: str
    weight: int
    fiber: str
    notes: str = ""


@dataclass(frozen=True)
class Needle:
    id: str
    size: str
    point: str
    notes: str = ""


@dataclass(frozen=True)
class Fabric:
    id: str
    construction: str
    weight_gsm: int
    stretch: str
    stabilizer: str
    pull_comp_mm: float
    notes: str = ""


@dataclass(frozen=True)
class ResolvedPreset:
    name: str
    fabric: Fabric
    needle: Needle
    threads: tuple[Thread, ...]
    row_spacing_mm: float
    max_stitch_length_mm: float
    pull_compensation_mm: float
    satin_underlay: str
    fill_underlay: str
    # Nominal zig-zag spacing. Wide traced columns may request 0.38mm locally
    # because Ink/Stitch's measured same-rail pitch grows on broad curves.
    satin_spacing_mm: float = 0.40
    # Fill underlay row pitch. Legacy presets derive it as 3x the fill row
    # spacing; the pro fills (Zenbul ring, CHGL napkin, 2026-09) run a
    # perpendicular tatami underlay at 1.0-1.6 mm regardless of top density.
    fill_underlay_row_spacing_mm: float | None = None
    # Zig-zag underlay pitch along a satin column. Ink/Stitch default is 3.0,
    # our legacy 2.0; the pro Acre wordmark (3-5 mm columns) uses ~1.2.
    zigzag_underlay_spacing_mm: float = 2.0
    # Satin border sewn over every fill edge after the fill (0 = none). The
    # pro fills hide the tatami edge under a 1.8 mm (Zenbul) / 2.9 mm (CHGL)
    # satin run with its own centre walk.
    fill_border_mm: float = 0.0
    # Running/bean defaults for the bean recipe. None / 2 keep the legacy
    # tuning behaviour (SMOOTH_RUNNING_LENGTH_MM, quintuple bean).
    running_stitch_length_mm: float | None = None
    bean_repeats: int = 2
    # Longest unsplit satin zig-zag (Ink/Stitch max_stitch_length on the
    # column). Legacy 4.0; pro Acre sews 5 mm columns whole.
    satin_max_stitch_mm: float = 4.0
    notes: str = ""


def _load_table(path: Path, key: str) -> list[dict]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    return data.get(key, [])


def _index_records(records: list[dict], field: str, kind: str) -> dict[str, dict]:
    """Index inventory rows while rejecting missing or duplicate identifiers."""
    indexed: dict[str, dict] = {}
    for i, record in enumerate(records, 1):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{kind} record #{i} has no non-empty {field!r}")
        if value in indexed:
            raise ValueError(f"duplicate {kind} {field} {value!r}")
        indexed[value] = record
    return indexed


def _bean_repeats(value: object, name: str) -> int:
    """Ink/Stitch bean_stitch_repeats: 1 = triple, 2 = quintuple, 3 = 7 passes."""
    try:
        repeats = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"preset {name!r} bean_repeats must be 1, 2 or 3; got {value!r}")
    if repeats not in (1, 2, 3):
        raise ValueError(f"preset {name!r} bean_repeats must be 1, 2 or 3; got {value!r}")
    return repeats


def _positive(value: object, label: str, *, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be numeric, got {value!r}") from None
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        relation = ">= 0" if allow_zero else "> 0"
        raise ValueError(f"{label} must be finite and {relation}, got {value!r}")
    return number


def load_threads(materials_dir: Path | None = None) -> dict[str, Thread]:
    materials_dir = materials_dir if materials_dir is not None else default_materials_dir()
    rows = _index_records(
        _load_table(materials_dir / "threads.toml", "thread"), "id", "thread"
    )
    result: dict[str, Thread] = {}
    for thread_id, raw in rows.items():
        thread = Thread(**raw)
        if not _HEX_COLOR.fullmatch(thread.hex):
            raise ValueError(
                f"thread {thread_id!r} hex must be #RRGGBB, got {thread.hex!r}"
            )
        _positive(thread.weight, f"thread {thread_id!r} weight")
        result[thread_id] = thread
    return result


def load_needles(materials_dir: Path | None = None) -> dict[str, Needle]:
    materials_dir = materials_dir if materials_dir is not None else default_materials_dir()
    rows = _index_records(
        _load_table(materials_dir / "needles.toml", "needle"), "id", "needle"
    )
    result = {needle_id: Needle(**raw) for needle_id, raw in rows.items()}
    for needle_id, needle in result.items():
        if not needle.size.strip() or not needle.point.strip():
            raise ValueError(f"needle {needle_id!r} needs non-empty size and point")
    return result


def load_fabrics(materials_dir: Path | None = None) -> dict[str, Fabric]:
    materials_dir = materials_dir if materials_dir is not None else default_materials_dir()
    rows = _index_records(
        _load_table(materials_dir / "fabrics.toml", "fabric"), "id", "fabric"
    )
    result = {fabric_id: Fabric(**raw) for fabric_id, raw in rows.items()}
    for fabric_id, fabric in result.items():
        _positive(fabric.weight_gsm, f"fabric {fabric_id!r} weight_gsm")
        _positive(fabric.pull_comp_mm, f"fabric {fabric_id!r} pull_comp_mm", allow_zero=True)
    return result


def load_presets_raw(materials_dir: Path | None = None) -> list[dict]:
    materials_dir = materials_dir if materials_dir is not None else default_materials_dir()
    return _load_table(materials_dir / "presets.toml", "preset")


def resolve_preset(name: str, materials_dir: Path | None = None) -> ResolvedPreset:
    materials_dir = materials_dir if materials_dir is not None else default_materials_dir()
    threads_by_id = load_threads(materials_dir)
    needles_by_id = load_needles(materials_dir)
    fabrics_by_id = load_fabrics(materials_dir)
    presets = load_presets_raw(materials_dir)
    presets_by_name = _index_records(presets, "name", "preset")

    raw = presets_by_name.get(name)
    if raw is None:
        available = ", ".join(presets_by_name)
        raise KeyError(f"preset {name!r} not found. available: {available}")

    def reference(table: dict, key: str, kind: str):
        ref = raw.get(key)
        if ref not in table:
            raise ValueError(f"preset {name!r} references unknown {kind} {ref!r}")
        return table[ref]

    thread_ids = raw.get("threads")
    if not isinstance(thread_ids, list) or not thread_ids:
        raise ValueError(f"preset {name!r} must contain at least one thread id")
    missing_threads = [tid for tid in thread_ids if tid not in threads_by_id]
    if missing_threads:
        raise ValueError(
            f"preset {name!r} references unknown thread(s): {', '.join(map(repr, missing_threads))}"
        )

    satin_underlay = raw.get("satin_underlay")
    if satin_underlay not in _SATIN_UNDERLAYS:
        raise ValueError(
            f"preset {name!r} satin_underlay must be one of "
            f"{', '.join(sorted(_SATIN_UNDERLAYS))}; got {satin_underlay!r}"
        )
    fill_underlay = raw.get("fill_underlay")
    if fill_underlay not in _FILL_UNDERLAYS:
        raise ValueError(
            f"preset {name!r} fill_underlay must be one of "
            f"{', '.join(sorted(_FILL_UNDERLAYS))}; got {fill_underlay!r}"
        )

    fabric = reference(fabrics_by_id, "fabric", "fabric")
    return ResolvedPreset(
        name=raw["name"],
        fabric=fabric,
        needle=reference(needles_by_id, "needle", "needle"),
        threads=tuple(threads_by_id[tid] for tid in thread_ids),
        row_spacing_mm=_positive(raw.get("row_spacing_mm"), f"preset {name!r} row_spacing_mm"),
        max_stitch_length_mm=_positive(
            raw.get("max_stitch_length_mm"), f"preset {name!r} max_stitch_length_mm"
        ),
        pull_compensation_mm=_positive(
            fabric.pull_comp_mm, f"fabric {fabric.id!r} pull_comp_mm", allow_zero=True
        ),
        satin_underlay=satin_underlay,
        fill_underlay=fill_underlay,
        satin_spacing_mm=_positive(
            raw.get("satin_spacing_mm", 0.40), f"preset {name!r} satin_spacing_mm"
        ),
        fill_underlay_row_spacing_mm=(
            None if raw.get("fill_underlay_row_spacing_mm") is None else _positive(
                raw.get("fill_underlay_row_spacing_mm"),
                f"preset {name!r} fill_underlay_row_spacing_mm")
        ),
        zigzag_underlay_spacing_mm=_positive(
            raw.get("zigzag_underlay_spacing_mm", 2.0),
            f"preset {name!r} zigzag_underlay_spacing_mm"),
        fill_border_mm=_positive(
            raw.get("fill_border_mm", 0.0), f"preset {name!r} fill_border_mm",
            allow_zero=True),
        running_stitch_length_mm=(
            None if raw.get("running_stitch_length_mm") is None else _positive(
                raw.get("running_stitch_length_mm"),
                f"preset {name!r} running_stitch_length_mm")
        ),
        bean_repeats=_bean_repeats(raw.get("bean_repeats", 2), name),
        satin_max_stitch_mm=_positive(
            raw.get("satin_max_stitch_mm", 4.0), f"preset {name!r} satin_max_stitch_mm"),
        notes=raw.get("notes", ""),
    )


def list_preset_names(materials_dir: Path | None = None) -> list[str]:
    materials_dir = materials_dir if materials_dir is not None else default_materials_dir()
    return list(_index_records(load_presets_raw(materials_dir), "name", "preset"))
