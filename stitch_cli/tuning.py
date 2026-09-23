"""SVG tuning pass: stamp Ink/Stitch parameters onto each path based on a preset.

Source SVGs in `designs/` stay human-edited and lightweight. Right before
handing the file to Ink/Stitch, this module produces a derived `*.tuned.svg`
in the output directory with the right inkstitch:* attributes filled in for
the chosen materials preset.

Convention used here:
- Any path styled with a non-`none` fill is treated as a fill region.
- Any path styled with stroke and `fill: none` is treated as either a satin
  column (when it carries an `inkstitch:satin_column="true"` marker) or a
  running stitch outline.
- Stroke/fill colors on the source SVG default to the preset's first thread.
  Multi-color designs select a preset thread with
  `data-thread="<thread_id>"` on a path or any ancestor group.
"""

from __future__ import annotations

import re

from pathlib import Path

from lxml import etree

from .materials import ResolvedPreset


SVG_NS = "http://www.w3.org/2000/svg"
INKSTITCH_NS = "http://inkstitch.org/namespace"
NSMAP = {"svg": SVG_NS, "inkstitch": INKSTITCH_NS}

# Bump when Ink/Stitch's expected schema version changes — keeps the
# "Unversioned Ink/Stitch SVG file detected" dialog from firing in
# headless runs. Mirrors the value Ink/Stitch's bundled assets use.
INKSTITCH_SVG_VERSION = "3"

# Stitch-quality defaults grounded in Ink/Stitch's own bundled satin fonts
# (e.g. Resources/fonts/chopin), which are professionally digitized reference
# data. Analysing pro-digitized hat logos (out/pro-samples) vs our output showed
# our auto_fill produced ~47% sub-0.8mm stitches (vs ~2% for the pros) and no
# lock stitches — these settings close most of that gap.
#
# min_stitch_length_mm merges needle-killing micro-stitches that pile up where
# auto_fill rows hit narrow letter strokes. min_jump_stitch_length_mm keeps
# short hops between nearby shapes as running stitches instead of trims, so we
# don't leave a thread tail at every glyph. force_lock_stitches ties off every
# object so nothing unravels.
MIN_STITCH_LEN_MM = "0.8"
MIN_JUMP_STITCH_LEN_MM = "3"
RUNNING_TOLERANCE_MM = "0.2"   # smoother running/travel stitching on curves

# Paths the Hershey pass has spline-smoothed (data-smoothed="true") carry extra
# vertices that describe a true arc. The default 0.2mm tolerance / 2.0mm length
# would decimate most of them straight back to the faceted original, so smoothed
# running paths get a tighter tolerance + shorter max length to preserve the
# curvature. Costs ~+27% stitches on monoline text, no rise in micro-stitches.
SMOOTH_RUNNING_TOLERANCE_MM = "0.1"
SMOOTH_RUNNING_LENGTH_MM = "1.2"   # 1.5 read loose on twill ribs (gossip-line v10)

# Satin density + underlay, from the chopin font (tuned for small lettering).
# 0.30 was measured against the pro-sample files and our own known-good sew-outs
# and it is ~25% denser than any of them: cobble-hills, desperate and yearn all
# advance 0.400mm per stitch. 0.30 sews fine on columns 1.3mm and wider (the
# Makeout lockup), but on finer work it packs the column into a raised cord.
SATIN_ZIGZAG_SPACING_MM = "0.40"   # satin stitch spacing along the column
SATIN_SHORT_STITCH_MM = "0.22"     # thin out stacked stitches in tight curves
SATIN_MAX_STITCH_MM = "4.0"        # split long satin floats so they can't snag

# "Chain stitch" imitation (data-stroke-method="chainstitch"). The PE900 is a
# lockstitch machine and physically cannot form a real chain loop — that needs
# a chenille/chain-stitch head. We fake the look with a narrow zigzag (ric-rac)
# run: the alternating arms read as chain links / a braided cord on twill, and
# it's the closest single-pass, fully-automated approximation that comes off a
# hooped file with no special setup. Width is kept cord-narrow; spacing is left
# open enough that the individual "links" stay legible instead of blurring into
# a solid satin band. (Two other tiers of the same effect, documented for the
# operator: route the path through data-stitch-method="satin" for a smoother
# raised rope, or use mirrored bobbin work with heavy thread for true loop
# texture — neither is automatable as a stroke knob here.)
CHAIN_ZIGZAG_WIDTH_MM = "1.8"      # cord width of the faux chain
CHAIN_ZIGZAG_SPACING_MM = "1.2"    # along-path pitch; lower = denser braid


def _qn(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def _style_to_dict(style: str) -> dict[str, str]:
    return dict(
        (k.strip(), v.strip())
        for k, v in (pair.split(":", 1) for pair in style.split(";") if ":" in pair)
    )


def _dict_to_style(props: dict[str, str]) -> str:
    return ";".join(f"{k}:{v}" for k, v in props.items())


def _classify(elem: etree._Element) -> str:
    """Return one of: 'fill', 'satin', 'satin_stroke', 'running', 'skip'."""
    style = _style_to_dict(elem.get("style", ""))
    fill = (style.get("fill") or elem.get("fill") or "#000000").lower()
    stroke = (style.get("stroke") or elem.get("stroke") or "none").lower()
    method = (elem.get("data-stitch-method") or "").lower()

    # Outline-mode helper paths: rungs are direction guides (a stray one
    # would sew a green bar), satin-fallback is an invisible centerline the
    # fillsatin pass either promotes to a bean or leaves inert.
    if (elem.get("class") or "") in ("rung", "satin-fallback"):
        return "skip"
    # Case-insensitive: Ink/Stitch's own fill_to_satin writes "True".
    if (elem.get(_qn(INKSTITCH_NS, "satin_column")) or "").lower() == "true":
        return "satin"
    # Opt-in satin route: a stroked centerline (with width) flagged
    # data-stitch-method="satin" is converted to a real satin column by a
    # stroke_to_satin pre-pass in the pipeline, not stamped as a fill/running
    # here. Good for monoline display logos, borders, and single-stroke text
    # (the Hershey route) — the automatic way to get pro-style satin edges.
    if method == "satin" and stroke not in ("none", "") and fill in ("none", ""):
        return "satin_stroke"
    if fill not in ("none", ""):
        return "fill"
    if stroke not in ("none", ""):
        return "running"
    return "skip"


def _set_inkstitch(elem: etree._Element, key: str, value: str) -> None:
    elem.set(_qn(INKSTITCH_NS, key), value)


def _stamp_svg_version(root: etree._Element) -> None:
    """Add <inkstitch:inkstitch_svg_version> in <metadata> so Ink/Stitch
    treats the file as up-to-date and skips its 'Unversioned' dialog."""
    metadata = root.find(_qn(SVG_NS, "metadata"))
    if metadata is None:
        metadata = etree.SubElement(root, _qn(SVG_NS, "metadata"))
    tag = _qn(INKSTITCH_NS, "inkstitch_svg_version")
    version_el = metadata.find(tag)
    if version_el is None:
        version_el = etree.SubElement(metadata, tag)
    version_el.text = INKSTITCH_SVG_VERSION


def _stamp_globals(root: etree._Element) -> None:
    """Stamp document-level Ink/Stitch settings on the root <svg>.

    min_stitch_length / min_jump_stitch_length are global cleanup thresholds;
    setting them on the root applies them everywhere (we also set them per-path
    for robustness, matching how Ink/Stitch's own fonts carry them)."""
    root.set(_qn(INKSTITCH_NS, "min_stitch_length_mm"), MIN_STITCH_LEN_MM)
    root.set(_qn(INKSTITCH_NS, "min_jump_stitch_length_mm"), MIN_JUMP_STITCH_LEN_MM)


def _retint(elem: etree._Element, hex_color: str) -> None:
    style = _style_to_dict(elem.get("style", ""))
    if "fill" in style and style["fill"].lower() not in ("none", ""):
        style["fill"] = hex_color
    if "stroke" in style and style["stroke"].lower() not in ("none", ""):
        style["stroke"] = hex_color
    if style:
        elem.set("style", _dict_to_style(style))
    if elem.get("fill") and elem.get("fill").lower() not in ("none", ""):
        elem.set("fill", hex_color)
    if elem.get("stroke") and elem.get("stroke").lower() not in ("none", ""):
        elem.set("stroke", hex_color)


def _thread_color(elem: etree._Element, preset: ResolvedPreset) -> str:
    """Resolve an inherited data-thread id to a color in this preset."""
    thread_id: str | None = None
    cursor: etree._Element | None = elem
    while cursor is not None:
        value = (cursor.get("data-thread") or "").strip()
        if value:
            thread_id = value
            break
        cursor = cursor.getparent()
    if thread_id is None:
        return preset.threads[0].hex
    by_id = {thread.id: thread for thread in preset.threads}
    if thread_id not in by_id:
        elem_id = elem.get("id") or "<unidentified path>"
        raise ValueError(
            f"{elem_id} requests data-thread={thread_id!r}, but preset "
            f"{preset.name!r} contains: {', '.join(by_id) or '(none)'}"
        )
    return by_id[thread_id].hex


def _satin_underlay_name(value: str) -> str:
    """Normalize material-policy labels to the compact per-path labels."""
    aliases = {
        "none": "none",
        "center": "center",
        "center-walk": "center",
        "center+zigzag": "center+zigzag",
        "center-walk+zigzag": "center+zigzag",
        "center+contour": "center+contour",
        "center-walk+contour": "center+contour",
    }
    normalized = aliases.get(value.strip().lower())
    if normalized is None:
        raise ValueError(
            f"unknown satin underlay {value!r}; expected none, center, "
            "center+zigzag, or center+contour"
        )
    return normalized


def collect_satin_stroke_ids(svg_path: Path) -> list[str]:
    """Return the ids of paths flagged for the stroke_to_satin pre-pass.

    Run on a tuned SVG (after tune_svg has assigned ids) so the pipeline can
    hand exactly these elements to Ink/Stitch's stroke_to_satin effect."""
    tree = etree.parse(str(svg_path))
    ids: list[str] = []
    for path in tree.getroot().iter(_qn(SVG_NS, "path")):
        if _classify(path) == "satin_stroke":
            pid = path.get("id")
            if pid:
                ids.append(pid)
    return ids


def collect_outline_groups(svg_path: Path) -> list[str]:
    """Return the ids of outline-mode glyph groups (emitted by trace_lib's
    --satin-mode outline). Non-empty means the design needs the
    fillsatin.convert_outline_svg pre-pass before tuning."""
    tree = etree.parse(str(svg_path))
    return [g.get("id") for g in tree.getroot().iter(_qn(SVG_NS, "g"))
            if g.get("data-satin-mode") == "fill" and g.get("id")]


def audit_profile_for_svg(svg_path: Path) -> str | None:
    """Return an explicit or safely inferred PES audit profile.

    The root `data-audit-profile` is authoritative. Otherwise, pure fills and
    running work can be identified without ambiguity; mixed work receives the
    universal hotspot/routing checks. A pure satin file keeps the stricter
    default bands, while outline-mode wordmark groups are known satin inputs
    even though their pre-conversion geometry is temporarily filled.
    """
    tree = etree.parse(str(svg_path))
    root = tree.getroot()
    explicit = (root.get("data-audit-profile") or "").strip()
    if explicit:
        return explicit
    if any(g.get("data-satin-mode") == "fill"
           for g in root.iter(_qn(SVG_NS, "g"))):
        return "satin-wordmark"
    kinds = {
        _classify(path) for path in root.iter(_qn(SVG_NS, "path"))
    } - {"skip"}
    if kinds == {"fill"}:
        return "fill"
    if kinds == {"running"}:
        return "running"
    if kinds and kinds <= {"satin", "satin_stroke"}:
        # Pure satin used to return None, which applied the base bands but
        # left wordmark-specific clump, pitch-tail, run, and block checks only
        # advisory. Treat unmarked pure satin as lettering-grade by default;
        # decorative borders must explicitly opt into satin-outline.
        return "satin-wordmark"
    if len(kinds) > 1:
        return "mixed"
    return None


FILL_BORDER_WALK_LENGTH_MM = "1.5"   # pro centre walk under the border: 1.5-1.9 mm


def _split_subpaths(d: str) -> list[str]:
    """Split a path ``d`` into its subpaths. Only safe when every subpath opens
    with an absolute M (our tracers always emit that); a relative ``m`` after
    the first subpath would lose its origin, so such paths stay whole."""
    parts = [part.strip() for part in re.split(r"(?=[M])", d) if part.strip()]
    if len(parts) > 1 and not any(re.search(r"(?<![A-Za-z])m", part[1:]) for part in parts):
        return parts
    return [d]


def _add_fill_border(fill_path: etree._Element, width_mm: float, spacing: str) -> None:
    """Sew a satin run over the edge of a fill, the way the pro Zenbul/CHGL
    files do: a running-stitch centre walk along the outline, then a zig-zag
    of ``width_mm`` centred on the same outline. Both are inserted right after
    the fill so they sew on top of it. Each subpath of the fill (outer edge,
    every hole) gets its own walk+zig-zag pair ending in a trim, so the border
    never floats across the fill between two edges."""
    parent = fill_path.getparent()
    if parent is None or not fill_path.get("d"):
        return
    style = _style_to_dict(fill_path.get("style") or "")
    color = style.get("fill") or "#000000"
    base_id = fill_path.get("id") or "fill"
    index = list(parent).index(fill_path)
    transform = fill_path.get("transform")
    for n, sub_d in enumerate(_split_subpaths(fill_path.get("d"))):
        suffix = f"-{n}" if n else ""
        walk = etree.Element(_qn(SVG_NS, "path"))
        walk.set("id", f"{base_id}-border-walk{suffix}")
        walk.set("style", f"fill:none;stroke:{color};stroke-width:0.3")
        zig = etree.Element(_qn(SVG_NS, "path"))
        zig.set("id", f"{base_id}-border{suffix}")
        zig.set("style", f"fill:none;stroke:{color};stroke-width:{width_mm:.2f}")
        for elem in (walk, zig):
            elem.set("d", sub_d)
            if transform:
                elem.set("transform", transform)
            _set_inkstitch(elem, "min_stitch_length_mm", MIN_STITCH_LEN_MM)
            _set_inkstitch(elem, "min_jump_stitch_length_mm", MIN_JUMP_STITCH_LEN_MM)
        # The walk runs straight on into the zig-zag (same start point): no
        # trim and no lock between them. The zig-zag ends the block with both.
        _set_inkstitch(walk, "stroke_method", "running_stitch")
        _set_inkstitch(walk, "running_stitch_length_mm", FILL_BORDER_WALK_LENGTH_MM)
        _set_inkstitch(walk, "running_stitch_tolerance_mm", RUNNING_TOLERANCE_MM)
        _set_inkstitch(walk, "trim_after", "false")
        _set_inkstitch(walk, "force_lock_stitches", "false")
        _set_inkstitch(zig, "stroke_method", "zigzag_stitch")
        _set_inkstitch(zig, "zigzag_spacing_mm", spacing)
        _set_inkstitch(zig, "pull_compensation_mm", "0")
        _set_inkstitch(zig, "trim_after", "true")
        _set_inkstitch(zig, "force_lock_stitches", "true")
        parent.insert(index + 1 + 2 * n, walk)
        parent.insert(index + 2 + 2 * n, zig)


def tune_svg(
    source_path: Path,
    output_path: Path,
    preset: ResolvedPreset,
) -> Path:
    """Read source_path, stamp inkstitch:* params per preset, write to output_path."""
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(source_path), parser)
    root = tree.getroot()

    # Make sure the inkstitch namespace is declared so attributes serialize cleanly.
    if "inkstitch" not in (root.nsmap or {}):
        existing = dict(root.nsmap or {})
        existing["inkstitch"] = INKSTITCH_NS
        new_root = etree.Element(root.tag, nsmap=existing, attrib=root.attrib)
        for child in root:
            new_root.append(child)
        tree = etree.ElementTree(new_root)
        root = new_root

    _stamp_svg_version(root)
    _stamp_globals(root)

    if not preset.threads:
        raise ValueError(f"preset {preset.name!r} has no threads")
    satin_spacing = f"{getattr(preset, 'satin_spacing_mm', 0.40):.2f}"
    row_spacing = f"{preset.row_spacing_mm:.2f}"
    max_stitch_len = f"{preset.max_stitch_length_mm:.2f}"
    pull_comp = f"{preset.pull_compensation_mm:.2f}"
    # Fill underlay pitch: preset value if it has one, else the legacy 3x row
    # spacing. Both pro fills measured (2026-09) run it at 1.0-1.6 mm.
    underlay_spacing = f"{preset.fill_underlay_row_spacing_mm or preset.row_spacing_mm * 3:.2f}"
    zigzag_underlay_spacing = f"{getattr(preset, 'zigzag_underlay_spacing_mm', 2.0):.2f}"
    preset_fill_border = float(getattr(preset, "fill_border_mm", 0.0) or 0.0)
    fill_borders: list[tuple[etree._Element, float]] = []
    preset_satin_underlay = _satin_underlay_name(preset.satin_underlay)
    fill_underlay_enabled = preset.fill_underlay != "none"

    satin_idx = 0
    for path in root.iter(_qn(SVG_NS, "path")):
        kind = _classify(path)
        if kind == "skip":
            continue

        _retint(path, _thread_color(path, preset))

        if kind == "satin_stroke":
            # Leave geometry + stroke-width untouched for the stroke_to_satin
            # pre-pass; just ensure a stable id so the pass can target it. The
            # resulting satin column is parameterized on the re-tune pass.
            if not path.get("id"):
                path.set("id", f"satinstroke{satin_idx}")
                satin_idx += 1
            continue

        # Per-path opt-out: data-trim-after="false" suppresses the trim so
        # Ink/Stitch connects straight on to the next object. Only worth it
        # when the hop is short *and* runs under later stitching — a traced
        # wordmark can be 200+ separate columns, and trimming after every one
        # means 200 machine trims and 200 tails to clip by hand (pro files of
        # this size carry ~30). The caller owns that judgement; default is on.
        trim_after = "false" if (path.get("data-trim-after") or "").lower() == "false" else "true"
        # Suppressing the trim is only half of it: Ink/Stitch still cuts when
        # the hop to the next object exceeds this object's min_jump length, so
        # a no-trim path has to raise its own threshold above that hop.
        min_jump = (path.get("data-min-jump-mm") or "").strip() or MIN_JUMP_STITCH_LEN_MM

        # Cleanup + lock settings common to every embroidered object.
        _set_inkstitch(path, "min_stitch_length_mm", MIN_STITCH_LEN_MM)
        _set_inkstitch(path, "min_jump_stitch_length_mm", min_jump)
        # force_lock_stitches and trim_after are coupled: a forced tie-off means
        # the thread is about to be cut, so Ink/Stitch emits a TRIM after the
        # object no matter what trim_after says. On a path that deliberately
        # runs on into the next one there is no thread end to secure, so the
        # lock has to come off with the trim or the trim comes back anyway.
        # (Measured: 8 chained satin columns → 7 trims with the lock forced,
        # 0 without.) Objects that do end in a trim keep their lock stitches.
        # A buried end may still need a machine trim without an additional
        # lock knot.  For example, closely spaced decorative spokes can be
        # secured by a satin hub sewn immediately afterward; tying every spoke
        # inside the same 1mm area creates a density hotspot.  This is an
        # explicit per-path exception only--ordinary trimmed objects remain
        # locked by default, and chained/no-trim objects remain unlocked.
        lock_override = (path.get("data-force-lock") or "").strip().lower()
        if trim_after == "false" or lock_override == "false":
            force_lock = "false"
        else:
            force_lock = "true"
        _set_inkstitch(path, "force_lock_stitches", force_lock)

        if kind == "fill":
            # Per-path opt-in: a source path can carry data-stitch-method to
            # pick a non-default fill (e.g. contour_fill) and data-fill-angle
            # to rotate fill rows. Useful for mixing methods in one design,
            # e.g. the LOSERS test swatch. Unknown methods fall back safely.
            method = path.get("data-stitch-method") or "auto_fill"
            if method not in ("auto_fill", "contour_fill"):
                method = "auto_fill"
            _set_inkstitch(path, "fill_method", method)
            _set_inkstitch(path, "row_spacing_mm", row_spacing)
            _set_inkstitch(path, "max_stitch_length_mm", max_stitch_len)
            _set_inkstitch(path, "staggers", "2")
            # Ink/Stitch implements fill underlay as a sparse fill grid.  The
            # preset's legacy edge-walk labels both enable that grid; "none"
            # disables it. Inset stays small so thin strokes retain support.
            _set_inkstitch(path, "fill_underlay",
                           "true" if fill_underlay_enabled else "false")
            _set_inkstitch(path, "fill_underlay_row_spacing_mm", underlay_spacing)
            _set_inkstitch(path, "fill_underlay_max_stitch_length_mm", max_stitch_len)
            _set_inkstitch(path, "fill_underlay_inset_mm", "0.4")
            _set_inkstitch(path, "expand_mm", pull_comp)
            # Travel under the fill instead of jumping/trimming across it.
            _set_inkstitch(path, "underpath", "true")
            _set_inkstitch(path, "running_stitch_tolerance_mm", RUNNING_TOLERANCE_MM)
            _set_inkstitch(path, "trim_after", trim_after)
            angle = path.get("data-fill-angle")
            if angle is not None:
                _set_inkstitch(path, "angle", angle.strip())
            # Pro fills (Zenbul ring, CHGL napkin) hide the tatami edge under a
            # satin run sewn after the fill. Per-path data-fill-border-mm wins
            # over the preset; "0" opts a path out.
            border_attr = (path.get("data-fill-border-mm") or "").strip()
            try:
                border_mm = float(border_attr) if border_attr else preset_fill_border
            except ValueError:
                border_mm = preset_fill_border
            if border_mm > 0:
                fill_borders.append((path, border_mm))
        elif kind == "satin":
            _set_inkstitch(path, "satin_column", "true")
            zigzag_spacing = ((path.get("data-satin-spacing-mm") or "").strip()
                              or satin_spacing)
            _set_inkstitch(path, "zigzag_spacing_mm", zigzag_spacing)
            # Longest unsplit zig-zag. Legacy 4.0; the satin recipe preset
            # carries 5.0 because the pro Acre wordmark sews 5 mm columns
            # whole (a split column halves its reversal share and reads as
            # two ridges).
            satin_max = (
                (path.get("data-satin-max-stitch-mm") or "").strip()
                or f"{getattr(preset, 'satin_max_stitch_mm', float(SATIN_MAX_STITCH_MM)):g}"
            )
            try:
                satin_max_value = float(satin_max)
            except ValueError:
                satin_max_value = float(SATIN_MAX_STITCH_MM)
            if not 4.0 <= satin_max_value <= 7.0:
                satin_max_value = float(SATIN_MAX_STITCH_MM)
            _set_inkstitch(
                path, "max_stitch_length_mm", f"{satin_max_value:.1f}"
            )
            split_method = (
                path.get("data-satin-split-method") or ""
            ).strip().lower()
            if split_method in {"default", "simple", "staggered"}:
                _set_inkstitch(path, "split_method", split_method)
            if split_method == "staggered":
                try:
                    staggers = float(
                        (path.get("data-satin-split-staggers") or "4").strip()
                    )
                except ValueError:
                    staggers = 4.0
                if not 2.0 <= staggers <= 8.0:
                    staggers = 4.0
                _set_inkstitch(path, "staggers", f"{staggers:g}")
            # Pull compensation widens every column by a fixed amount, so on a
            # 0.8mm serif the same 0.2mm that's right for a 3mm stem is a 25%
            # fattening — it closes counters and puffs fine detail. A caller
            # that knows the column width can dial it down per path.
            _set_inkstitch(path, "pull_compensation_mm",
                           (path.get("data-pull-comp-mm") or "").strip() or pull_comp)
            try:
                short_distance = float(
                    (path.get("data-satin-short-stitch-mm") or "").strip()
                    or SATIN_SHORT_STITCH_MM
                )
            except ValueError:
                short_distance = float(SATIN_SHORT_STITCH_MM)
            if not 0.0 <= short_distance <= 0.75:
                short_distance = float(SATIN_SHORT_STITCH_MM)
            # Short-stitch insets a stitch whose spacing to its neighbour on
            # the same rail has fallen below this distance — it exists for the
            # INSIDE of a curve. At or above the zigzag spacing it fires on
            # every stitch instead, alternating the rails in and out: measured
            # on BEACH BABY at 0.50 against 0.40 spacing, the satin rail turn
            # p90 went 53 -> 157 degrees and the pitch tail 10 -> 41%.
            # Keep it clear of the nominal spacing.
            try:
                spacing_value = float(zigzag_spacing)
            except ValueError:
                spacing_value = 0.0
            if spacing_value > 0 and short_distance >= 0.6 * spacing_value:
                short_distance = round(0.6 * spacing_value, 2)
            _set_inkstitch(
                path, "short_stitch_distance_mm", f"{short_distance:g}"
            )
            if short_distance > 0:
                try:
                    short_inset = float(
                        (path.get("data-satin-short-stitch-inset") or "25").strip()
                    )
                except ValueError:
                    short_inset = 25.0
                if not 10.0 <= short_inset <= 50.0:
                    short_inset = 25.0
                _set_inkstitch(path, "short_stitch_inset", f"{short_inset:g}")
            # Center-walk + sparse zig-zag underlay is the lettering-grade combo
            # from Ink/Stitch's own fonts. It lies flat under a column ~1.3mm and
            # wider, but stacking two underlays plus the top pass inside a
            # sub-millimetre column raises it into a cord — the clumpy serifs on
            # the first Paris Review sew-out. data-satin-underlay="center" drops
            # the zig-zag layer and keeps only the center walk.
            underlay = _satin_underlay_name(
                path.get("data-satin-underlay") or preset_satin_underlay
            )
            _set_inkstitch(path, "center_walk_underlay", "false" if underlay == "none" else "true")
            # Pro lettering runs its center walk at fine pitch — the Bjerknes
            # Paris Review PES averages 1.23mm and Ink/Stitch's own satin fonts
            # overwhelmingly use 1.2 — where the Ink/Stitch default (~2mm) lets
            # the column ride loose on twill. Pin the repeats too (down-and-back).
            # Down-and-back at 1.2mm is the pro default and the shipped
            # setting. On a design made of many short columns the second pass
            # is ~16% of all stitches for very little hold, so it is opt-out
            # per path rather than pinned.
            _set_inkstitch(path, "center_walk_underlay_stitch_length_mm",
                           (path.get("data-satin-underlay-length-mm") or "").strip()
                           or "1.2")
            cw_repeats = (path.get("data-satin-underlay-repeats") or "").strip() or "2"
            if cw_repeats not in ("1", "2", "3"):
                cw_repeats = "2"
            _set_inkstitch(path, "center_walk_underlay_repeats", cw_repeats)
            # Contour underlay (edge run inset from both rails) is the wide-
            # column complement to the center walk — Ink/Stitch's recommended
            # combo for medium-wide satins. Opt-in via "center+contour"; the
            # outline-mode width tiers use it on columns >= 2.5mm.
            _set_inkstitch(path, "contour_underlay",
                           "true" if underlay == "center+contour" else "false")
            if underlay == "center+contour":
                _set_inkstitch(path, "contour_underlay_inset_mm", "0.4")
                _set_inkstitch(path, "contour_underlay_stitch_length_mm", "1.5")
            _set_inkstitch(path, "zigzag_underlay", "true" if underlay == "center+zigzag" else "false")
            _set_inkstitch(path, "zigzag_underlay_spacing_mm",
                           (path.get("data-zigzag-underlay-mm") or "").strip()
                           or zigzag_underlay_spacing)
            _set_inkstitch(path, "zigzag_underlay_max_stitch_length_mm", "5.0")
            _set_inkstitch(path, "zigzag_underlay_inset_mm", "0.0")
            _set_inkstitch(path, "running_stitch_tolerance_mm", RUNNING_TOLERANCE_MM)
            _set_inkstitch(path, "trim_after", trim_after)
        elif kind == "running":
            # Per-path opt-in via data-stroke-method:
            #  - bean_stitch  triples each stitch in place (bean_stitch_repeats=2)
            #    for ~3x visual weight without changing the shape — good for thin
            #    lines on twill that would otherwise sink into the fabric.
            #  - chainstitch  fakes a chain-stitch look with a narrow zigzag
            #    (ric-rac) run, since a lockstitch machine can't form a real
            #    chain loop. See CHAIN_ZIGZAG_* for the rationale and tuning.
            method = path.get("data-stroke-method") or "running_stitch"
            if method not in ("running_stitch", "bean_stitch", "chainstitch"):
                method = "running_stitch"
            if method == "chainstitch":
                _set_inkstitch(path, "stroke_method", "zigzag_stitch")
                _set_inkstitch(path, "zigzag_width_mm", CHAIN_ZIGZAG_WIDTH_MM)
                _set_inkstitch(path, "zigzag_spacing_mm", CHAIN_ZIGZAG_SPACING_MM)
                _set_inkstitch(path, "pull_compensation_mm", pull_comp)
            else:
                _set_inkstitch(path, "stroke_method", "running_stitch")
                requested_length = (
                    path.get("data-running-stitch-length-mm") or ""
                ).strip()
                preset_running = getattr(preset, "running_stitch_length_mm", None)
                if path.get("data-smoothed") == "true":
                    default_length = (f"{preset_running:g}" if preset_running
                                      else SMOOTH_RUNNING_LENGTH_MM)
                    _set_inkstitch(path, "running_stitch_tolerance_mm", SMOOTH_RUNNING_TOLERANCE_MM)
                else:
                    default_length = f"{preset_running:g}" if preset_running else max_stitch_len
                    _set_inkstitch(path, "running_stitch_tolerance_mm", RUNNING_TOLERANCE_MM)
                try:
                    requested_length_value = float(requested_length)
                except ValueError:
                    running_length = default_length
                else:
                    # Below 0.8 mm the global micro-stitch cleanup starts
                    # collapsing the requested points. Keep overrides within
                    # a useful, machine-safe running-stitch range.
                    running_length = (
                        f"{requested_length_value:g}"
                        if 0.8 <= requested_length_value <= 5.0
                        else default_length
                    )
                _set_inkstitch(
                    path, "running_stitch_length_mm", running_length
                )
                if method == "bean_stitch":
                    # Per-path opt-in: data-bean-repeats is Ink/Stitch
                    # bean_stitch_repeats — 1 = TRIPLE (fwd/back/fwd), 2 =
                    # quintuple, 3 = seven passes. The pro rail file is a
                    # triple; the legacy default here is 2. The bean recipe
                    # preset (hat-twill-bean) sets 1. Small text on twill
                    # scatters under many passes because they can't re-register
                    # in the same holes; use running_stitch (single) there.
                    preset_repeats = str(getattr(preset, "bean_repeats", 2))
                    repeats = (path.get("data-bean-repeats") or preset_repeats).strip()
                    if repeats not in ("1", "2", "3"):
                        repeats = preset_repeats
                    _set_inkstitch(path, "bean_stitch_repeats", repeats)
            _set_inkstitch(path, "trim_after", trim_after)

    for fill_path, border_mm in fill_borders:
        _add_fill_border(fill_path, border_mm, satin_spacing)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(
        str(output_path),
        xml_declaration=True,
        encoding="utf-8",
        standalone=True,
    )
    return output_path
