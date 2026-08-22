"""`stitch` CLI entrypoint.

Verbs:
    from-svg <input.svg> --preset <name> [-o out/]
    hershey <input.svg> [--font <name>] [--no-bean-stitch] [--preset <name>]
    from-generative <sketch_dir> --preset <name> [-o out/]
    convert <input> [-o out.pes]
    deploy <file.pes> --device <volume> [--preset <name>]
    preview <file.pes> [--png] [--open]
    presets list|show <name>
    threads list
    inspect <file.pes>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import convert as convert_mod
from . import deploy as deploy_mod
from . import inkstitch
from . import processing
from . import tuning
from .materials import (
    list_preset_names,
    load_presets_raw,
    load_threads,
    resolve_preset,
)


# Brother PE900 limits, from the operation manual's specification table.
# Note the field is NOT square: 130mm across the hoop, 180mm front-to-back.
# A design that fits one axis can still bust the other, so both are checked.
HOOP_MAX_X_MM = 130.0     # 5-1/8" — max embroidery pattern width
HOOP_MAX_Y_MM = 180.0     # 7-3/32" — max embroidery pattern height
PE900_MAX_STITCHES = 200_000  # per pattern
PE900_MAX_SPM = 650       # max embroidery speed (typical avg with trims ~400)
PE900_TYPICAL_SPM = 400


def _print_pes_stats(pes: Path) -> None:
    """Shared post-normalize report: counts, bounds, run-time estimate,
    hoop and stitch-count warnings."""
    summary = convert_mod.describe(pes)
    sc = summary["stitch_count"]
    print(f"  stitches: {sc}, threads: {summary['thread_count']}, bounds_mm: {summary['bounds_mm']}")
    if sc:
        print(f"  est. run time: ~{sc/PE900_MAX_SPM:.1f}-{sc/PE900_TYPICAL_SPM:.1f} min "
              f"@ {PE900_TYPICAL_SPM}-{PE900_MAX_SPM} SPM")
    if summary["bounds_mm"]:
        w = summary["bounds_mm"][2] - summary["bounds_mm"][0]
        h = summary["bounds_mm"][3] - summary["bounds_mm"][1]
        if w > HOOP_MAX_X_MM or h > HOOP_MAX_Y_MM:
            fits_rotated = h <= HOOP_MAX_X_MM and w <= HOOP_MAX_Y_MM
            hint = " (fits if rotated 90° on the machine)" if fits_rotated else ""
            print(f"  WARNING: design is {w:.1f}x{h:.1f}mm — exceeds the PE900 "
                  f"{HOOP_MAX_X_MM:.0f}x{HOOP_MAX_Y_MM:.0f}mm field{hint}", file=sys.stderr)
    if sc > PE900_MAX_STITCHES:
        print(f"  WARNING: {sc} stitches exceeds PE900 max of {PE900_MAX_STITCHES} per pattern",
              file=sys.stderr)


def _default_out_dir() -> Path:
    return Path.cwd() / "out"


def _digitize(
    source: Path,
    out_dir: Path,
    name: str,
    preset,
    *,
    strict_audit: bool = True,
) -> int:
    """Shared pipeline: source SVG → tuned SVG → PES (normalized) → summary.

    If the design flags any stroked centerlines with data-stitch-method="satin",
    a stroke_to_satin pre-pass converts them into real satin columns before the
    final stitch — the automatic route to pro-style satin edges (e.g. monoline
    display logos, borders, single-stroke Hershey text). The converted file is
    re-tuned so the new columns pick up the preset's satin parameters.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tuned_svg = out_dir / f"{name}.tuned.svg"
    pes = out_dir / f"{name}.pes"
    audit_profile = tuning.audit_profile_for_svg(source)

    def _rel(p: Path) -> str:
        return str(p.relative_to(Path.cwd()) if p.is_relative_to(Path.cwd()) else p)

    # Outline-mode designs (trace_lib --satin-mode outline) carry glyph groups
    # of branch fills + rungs. Convert those into routed satin columns with
    # Ink/Stitch's fill_to_satin + auto_satin BEFORE tuning, so the plain
    # fills never reach the fill parameter branch.
    outline_groups = tuning.collect_outline_groups(source)
    if outline_groups:
        from . import fillsatin
        print(f"outline mode: converting {len(outline_groups)} glyph group(s) "
              f"(fill_to_satin + auto_satin)")
        fts_svg = out_dir / f"{name}.fts.svg"
        fillsatin.convert_outline_svg(source, fts_svg)
        source = fts_svg

    print(f"tuning {source.name} for preset {preset.name!r}")
    tuning.tune_svg(source, tuned_svg, preset)
    print(f"  → {_rel(tuned_svg)}")

    to_stitch = tuned_svg
    satin_ids = tuning.collect_satin_stroke_ids(tuned_svg)
    if satin_ids:
        print(f"converting {len(satin_ids)} stroked path(s) → satin columns (stroke_to_satin)")
        satin_svg = out_dir / f"{name}.satin.svg"
        inkstitch.run_effect("stroke_to_satin", tuned_svg, satin_svg, ids=satin_ids)
        retuned_svg = out_dir / f"{name}.tuned2.svg"
        tuning.tune_svg(satin_svg, retuned_svg, preset)
        to_stitch = retuned_svg
        print(f"  → {_rel(satin_svg)}  →  {_rel(retuned_svg)}")

    print("running Ink/Stitch")
    inkstitch.svg_to_pes(to_stitch, pes)
    print(f"  → {pes}")

    print("normalizing PES metadata")
    convert_mod.normalize_pes(pes, preset=preset)
    _print_pes_stats(pes)

    # Score every build against the pro-digitized bands. Quality regressions are
    # invisible in a preview and expensive on a blank, so make them loud here.
    from . import audit as audit_mod
    print(f"quality audit{f' ({audit_profile})' if audit_profile else ''}")
    _, failures = audit_mod.audit(pes, profile=audit_profile)
    return 1 if strict_audit and failures else 0


def cmd_from_svg(args: argparse.Namespace) -> int:
    preset = resolve_preset(args.preset)
    source = Path(args.input).resolve()
    out_dir = Path(args.out or _default_out_dir()).resolve()
    name = args.name or source.stem
    return _digitize(
        source, out_dir, name, preset,
        strict_audit=getattr(args, "strict_audit", False),
    )


def cmd_hershey(args: argparse.Namespace) -> int:
    """Render <text> elements as single-stroke Hershey paths, then digitize
    as running stitch (default: bean stitch for ~3x visual weight on twill)."""
    from . import hershey as hershey_mod

    preset = resolve_preset(args.preset)
    source = Path(args.input).resolve()
    out_dir = Path(args.out or _default_out_dir()).resolve()
    name = args.name or source.stem
    designs_dir = Path.cwd() / "designs"
    designs_dir.mkdir(parents=True, exist_ok=True)
    prepared_svg = designs_dir / f"{name}.svg"

    if args.chainstitch:
        weight = "faux chainstitch"
    elif args.bean_stitch:
        weight = "bean stitch"
    else:
        weight = "running stitch"
    smoothing = "smoothed" if args.smooth else "raw polylines"
    print(f"rendering text as single-stroke {args.font!r} ({weight}, {smoothing})")
    hershey_mod.prepare_hershey_svg(
        source, prepared_svg, font=args.font,
        bean_stitch=args.bean_stitch, chainstitch=args.chainstitch,
        smooth=args.smooth,
    )
    rel = prepared_svg.relative_to(Path.cwd()) if prepared_svg.is_relative_to(Path.cwd()) else prepared_svg
    print(f"  → {rel}")
    return _digitize(
        prepared_svg, out_dir, name, preset,
        strict_audit=getattr(args, "strict_audit", False),
    )


def cmd_swatch(args: argparse.Namespace) -> int:
    from . import swatch as swatch_mod  # local import — only needed for this verb

    preset = resolve_preset(args.preset)
    logo = Path(args.logo).resolve()
    out_dir = Path(args.out or _default_out_dir()).resolve()
    name = args.name or "LOSERS-swatch"

    designs_dir = Path.cwd() / "designs"
    designs_dir.mkdir(parents=True, exist_ok=True)
    swatch_svg = designs_dir / f"{name}.svg"

    print(f"building swatch from {logo.name}")
    swatch_mod.build_swatch(logo, swatch_svg)
    rel = swatch_svg.relative_to(Path.cwd()) if swatch_svg.is_relative_to(Path.cwd()) else swatch_svg
    print(f"  → {rel}")
    print()
    print("row legend:")
    for line in swatch_mod.row_legend().splitlines():
        print(f"  {line}")
    print()
    return _digitize(
        swatch_svg, out_dir, name, preset,
        strict_audit=getattr(args, "strict_audit", False),
    )


def _finalize_pes(
    pes: Path,
    preset=None,
    profile: str | None = None,
    *,
    strict_audit: bool = True,
) -> int:
    """Normalize PES metadata to v6 (with optional preset thread override)
    and print summary, machine-limit warnings, and the quality audit."""
    print("normalizing PES metadata")
    convert_mod.normalize_pes(pes, preset=preset)
    print(f"  → {pes}")
    _print_pes_stats(pes)
    from . import audit as audit_mod
    print(f"quality audit{f' ({profile})' if profile else ''}")
    _, failures = audit_mod.audit(pes, profile=profile)
    return 1 if strict_audit and failures else 0


def cmd_lettering_swatch(args: argparse.Namespace) -> int:
    """Build a PES directly from Ink/Stitch built-in satin fonts, bypassing
    auto-fill entirely. Skips tune_svg + Ink/Stitch — the fonts already
    carry proper stitch parameters. The preset is only used to stamp
    correct thread metadata (brand/code) into the PES."""
    from . import lettering as lettering_mod

    preset = resolve_preset(args.preset)
    out_dir = Path(args.out or _default_out_dir()).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or "LOSERS-lettering"
    pes = out_dir / f"{name}.pes"

    print(f"building lettering swatch via Ink/Stitch batch_lettering")
    print(f"  LOSERS → Barstitch Bold @ 50% (≈13mm caps)")
    print(f"  (917)-524-7853 → Ink/Stitch Small Font @ 135% (≈7mm digits)")
    print(f"  per-glyph trims via --trim=glyph")
    print(f"  thread metadata from preset {preset.name!r}")
    lettering_mod.build_lettering_swatch(pes)
    return _finalize_pes(
        pes, preset=preset, profile="satin-lettering",
        strict_audit=args.strict_audit,
    )


def cmd_lettering_logo(args: argparse.Namespace) -> int:
    """Build the full DESPERATE LOSERS [phone] logo from Ink/Stitch built-in
    satin fonts. Three stacked lines, centered on the 4×4" canvas. The
    preset is only used to stamp thread metadata into the PES."""
    from . import lettering as lettering_mod

    preset = resolve_preset(args.preset)
    out_dir = Path(args.out or _default_out_dir()).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or "DL-logo"
    pes = out_dir / f"{name}.pes"

    print(f"building DL-logo via Ink/Stitch batch_lettering")
    print(f"  DESPERATE / LOSERS → Barstitch Bold @ 50% (≈13mm caps)")
    print(f"  (917)-524-7853 → Ink/Stitch Small Font @ 135% (≈7mm digits)")
    print(f"  per-glyph trims via --trim=glyph")
    print(f"  thread metadata from preset {preset.name!r}")
    lettering_mod.build_lettering_logo(pes)
    return _finalize_pes(
        pes, preset=preset, profile="satin-lettering",
        strict_audit=args.strict_audit,
    )


def cmd_lettering(args: argparse.Namespace) -> int:
    """Generate a satin PES from any Ink/Stitch built-in satin font — the right
    tool for clean satin lettering, vs auto_fill on glyph outlines. Optionally
    fits the design to an exact width (adjusts the font scale, then trims).
    The preset only stamps thread metadata; the font carries the stitch params."""
    import re as _re
    from . import lettering as lettering_mod

    if args.list_fonts:
        for name in lettering_mod.list_fonts():
            print(name)
        return 0
    if not args.text or not args.font:
        print("error: --text and --font are required (or use --list-fonts)", file=sys.stderr)
        return 2

    preset = resolve_preset(args.preset)
    out_dir = Path(args.out or _default_out_dir()).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or (_re.sub(r"[^a-z0-9]+", "-", args.text.lower()).strip("-") or "lettering")
    pes = out_dir / f"{name}.pes"

    print(f"lettering {args.text!r} in {args.font!r} @ {args.scale}% (trim={args.trim})")
    lettering_mod.make_lettering_pes(
        args.text, args.font, pes, scale=args.scale, trim=args.trim, text_align=args.align
    )

    if args.width_mm:
        def _width(p: Path) -> float:
            b = convert_mod.describe(p)["bounds_mm"]
            return (b[2] - b[0]) if b else 0.0
        w = _width(pes)
        scale2 = args.scale
        if w > 0:
            # land near target via the font scale, then trim the residual exactly
            scale2 = max(1, round(args.scale * args.width_mm / w))
            if scale2 != args.scale:
                lettering_mod.make_lettering_pes(
                    args.text, args.font, pes, scale=scale2, trim=args.trim, text_align=args.align
                )
                w = _width(pes)
            if w > 0 and abs(w - args.width_mm) > 0.2:
                convert_mod.scale_pes(pes, pes, args.width_mm / w)
            print(f"  fitted to {args.width_mm:.1f}mm wide (font scale {scale2}%)")

    return _finalize_pes(
        pes, preset=preset, profile="satin-lettering",
        strict_audit=args.strict_audit,
    )


def cmd_from_generative(args: argparse.Namespace) -> int:
    preset = resolve_preset(args.preset)
    sketch = Path(args.sketch).resolve()
    out_dir = Path(args.out or _default_out_dir()).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    name = args.name or sketch.name
    pes = out_dir / f"{name}.pes"

    print(f"running PEmbroider sketch {sketch.name} with preset {preset.name!r}")
    processing.run_sketch(sketch, preset, pes)
    print(f"  → {pes}")
    return _finalize_pes(
        pes, preset=preset, profile="mixed",
        strict_audit=args.strict_audit,
    )


def cmd_offset(args: argparse.Namespace) -> int:
    """Shift the visible design within the hoop by (x_mm, y_mm). Useful for
    compensating when a hat-hoop's clamping position can't be adjusted
    further. The shift is achieved by extending the file's bbox with a
    phantom JUMP stitch; capped independently by the 130x180mm field."""
    src = Path(args.input).resolve()
    if args.out:
        dst = Path(args.out).resolve()
    else:
        suffix = f"-offset_x{args.x_mm:+g}_y{args.y_mm:+g}".replace("+", "p").replace("-", "m")
        dst = src.with_name(f"{src.stem}{suffix}.pes")
    print(f"offsetting {src.name} by x={args.x_mm:+g}mm y={args.y_mm:+g}mm")
    convert_mod.offset_pes(src, dst, x_mm=args.x_mm, y_mm=args.y_mm)
    print(f"  → {dst}")
    _print_pes_stats(dst)
    from . import audit as audit_mod
    print("quality audit (mixed)")
    _, failures = audit_mod.audit(dst, profile="mixed")
    return 1 if args.strict_audit and failures else 0


def cmd_convert(args: argparse.Namespace) -> int:
    src = Path(args.input).resolve()
    dst = Path(args.out).resolve() if args.out else src.with_suffix(".pes")
    convert_mod.normalize_pes(src, dst)
    summary = convert_mod.describe(dst)
    print(f"{src.name} → {dst}  (stitches: {summary['stitch_count']}, bounds_mm: {summary['bounds_mm']})")
    from . import audit as audit_mod
    print(f"quality audit ({args.profile})")
    _, failures = audit_mod.audit(dst, profile=args.profile)
    return 1 if args.strict_audit and failures else 0


def cmd_deploy(args: argparse.Namespace) -> int:
    pes = Path(args.input).resolve()
    device = Path(args.device).resolve()
    preset = resolve_preset(args.preset) if args.preset else None
    deploy_mod.deploy(
        pes, device, preset,
        allow_audit_failures=args.allow_audit_failures,
    )
    return 0


def cmd_presets(args: argparse.Namespace) -> int:
    if args.action == "list":
        for p in load_presets_raw():
            print(f"{p['name']}  — {p.get('notes', '')}")
        return 0
    preset = resolve_preset(args.name)
    print(f"preset: {preset.name}")
    print(f"  fabric:  {preset.fabric.id} ({preset.fabric.construction}, {preset.fabric.weight_gsm}gsm)")
    print(f"  needle:  {preset.needle.id} ({preset.needle.size}, {preset.needle.point})")
    print(f"  threads: {[t.id for t in preset.threads]}")
    print(f"  row_spacing_mm:        {preset.row_spacing_mm}")
    print(f"  max_stitch_length_mm:  {preset.max_stitch_length_mm}")
    print(f"  pull_compensation_mm:  {preset.pull_compensation_mm}")
    print(f"  satin_underlay:        {preset.satin_underlay}")
    print(f"  fill_underlay:         {preset.fill_underlay}")
    return 0


def cmd_threads(args: argparse.Namespace) -> int:
    threads = load_threads()
    if args.action == "list":
        for t in threads.values():
            print(f"{t.id:40s}  {t.hex}  {t.weight}wt {t.fiber}  — {t.notes}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Score a finished PES against the professionally digitized corpus."""
    from . import audit as audit_mod

    failed = 0
    for i, f in enumerate(args.files):
        if i:
            print()
        _, fails = audit_mod.audit(Path(f).resolve(),
                                   profile=getattr(args, "profile", None))
        failed += bool(fails)
    return 1 if failed and args.strict else 0


def cmd_inspect(args: argparse.Namespace) -> int:
    summary = convert_mod.describe(Path(args.input).resolve())
    for k, v in summary.items():
        print(f"{k}: {v}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Render a stitch-plan preview directly from an embroidery file.

    Avoids Inkscape entirely — Ink/Stitch's GUI extensions hit a known
    wxPython/libiconv collision on Inkscape 1.4 + macOS that we don't try
    to fix here.
    """
    import subprocess

    import pyembroidery

    src = Path(args.input).resolve()
    pattern = pyembroidery.read(str(src))
    out_svg = src.parent / f"{src.stem}.preview.svg"
    with out_svg.open("wb") as f:
        pyembroidery.write_svg(pattern, f)
    print(f"  → {out_svg}")

    if args.png:
        out_png = src.parent / f"{src.stem}.preview.png"
        with out_png.open("wb") as f:
            pyembroidery.write_png(pattern, f)
        print(f"  → {out_png}")

    if args.open:
        subprocess.run(["open", str(out_svg)], check=False)
    return 0


def _add_strict_audit_switch(parser: argparse.ArgumentParser) -> None:
    """Make production builds fail closed while retaining an explicit escape.

    ``--strict-audit`` remains accepted for old recipes. The generated
    ``--no-strict-audit`` form is deliberately conspicuous and should be used
    only for diagnostics or intermediate experiments.
    """
    parser.add_argument(
        "--strict-audit",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="fail when the technique-aware quality audit fails (default: on)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stitch", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_svg = sub.add_parser("from-svg", help="SVG → tuned SVG → .pes via Ink/Stitch")
    p_svg.add_argument("input")
    p_svg.add_argument("--preset", required=True, choices=list_preset_names())
    p_svg.add_argument("-o", "--out", help="output directory (default: ./out)")
    p_svg.add_argument("--name", help="output basename (default: input stem)")
    _add_strict_audit_switch(p_svg)
    p_svg.set_defaults(func=cmd_from_svg)

    p_hsh = sub.add_parser(
        "hershey",
        help="render <text> as single-stroke Hershey paths → running/bean stitch PES",
    )
    p_hsh.add_argument("input", help="SVG with live <text> elements")
    p_hsh.add_argument("--preset", default="hat-twill-black-detail",
                       choices=list_preset_names(),
                       help="materials preset (default: hat-twill-black-detail)")
    p_hsh.add_argument("--font", default="EMSReadability",
                       help="Inkscape Hershey font face (default: EMSReadability). "
                            "Try EMSOsmotron, EMSTech, HersheySans1, HersheySansMed.")
    p_hsh.add_argument("--no-bean-stitch", dest="bean_stitch", action="store_false",
                       help="single-pass running stitch instead of triple-pass bean stitch")
    p_hsh.set_defaults(bean_stitch=True)
    p_hsh.add_argument("--chainstitch", action="store_true",
                       help="faux chainstitch effect: narrow zigzag (ric-rac) run "
                            "imitating a chain on the lockstitch PE900 (overrides bean stitch)")
    p_hsh.add_argument("--no-smooth", dest="smooth", action="store_false",
                       help="skip corner-preserving curve smoothing; stitch the raw "
                            "Hershey polylines (rounder glyphs read as polygons)")
    p_hsh.set_defaults(smooth=True)
    p_hsh.add_argument("-o", "--out", help="output directory (default: ./out)")
    p_hsh.add_argument("--name", help="output basename (default: input stem)")
    _add_strict_audit_switch(p_hsh)
    p_hsh.set_defaults(func=cmd_hershey)

    p_sw = sub.add_parser(
        "swatch",
        help="build the LOSERS stitch-treatment test swatch from a logo SVG",
    )
    p_sw.add_argument("--logo", default="designs/DL-logo.svg",
                      help="source logo SVG (default: designs/DL-logo.svg)")
    p_sw.add_argument("--preset", required=True, choices=list_preset_names())
    p_sw.add_argument("-o", "--out", help="output directory (default: ./out)")
    p_sw.add_argument("--name", help="output basename (default: LOSERS-swatch)")
    _add_strict_audit_switch(p_sw)
    p_sw.set_defaults(func=cmd_swatch)

    p_ls = sub.add_parser(
        "lettering-swatch",
        help="build a PES from Ink/Stitch built-in satin fonts (LOSERS + phone)",
    )
    p_ls.add_argument("--preset", default="hat-twill-black-detail",
                      choices=list_preset_names(),
                      help="thread metadata source (default: hat-twill-black-detail)")
    p_ls.add_argument("-o", "--out", help="output directory (default: ./out)")
    p_ls.add_argument("--name", help="output basename (default: LOSERS-lettering)")
    _add_strict_audit_switch(p_ls)
    p_ls.set_defaults(func=cmd_lettering_swatch)

    p_ll = sub.add_parser(
        "lettering-logo",
        help="build the full DESPERATE LOSERS [phone] logo via Ink/Stitch satin fonts",
    )
    p_ll.add_argument("--preset", default="hat-twill-black-detail",
                      choices=list_preset_names(),
                      help="thread metadata source (default: hat-twill-black-detail)")
    p_ll.add_argument("-o", "--out", help="output directory (default: ./out)")
    p_ll.add_argument("--name", help="output basename (default: DL-logo)")
    _add_strict_audit_switch(p_ll)
    p_ll.set_defaults(func=cmd_lettering_logo)

    p_let = sub.add_parser(
        "lettering",
        help="generate a satin PES from an Ink/Stitch built-in font (text + font name)",
    )
    p_let.add_argument("--text", help="text to letter (Title Case reads best in blackletter)")
    p_let.add_argument("--font",
                       help="Ink/Stitch font name, e.g. 'Manuskript Gothisch', 'Barstitch Bold' "
                            "(run with --list-fonts to see all)")
    p_let.add_argument("--preset", default="hat-twill-black-detail", choices=list_preset_names(),
                       help="thread metadata source (default: hat-twill-black-detail)")
    p_let.add_argument("--scale", type=int, default=100, help="font scale percent (default 100)")
    p_let.add_argument("--width-mm", type=float,
                       help="fit the design to this width in mm (auto font scale + exact trim)")
    p_let.add_argument("--trim", default="glyph", choices=["off", "line", "word", "glyph"],
                       help="trim threads between letters (default: glyph)")
    p_let.add_argument("--align", default="center", choices=["left", "center", "right"])
    p_let.add_argument("--list-fonts", action="store_true",
                       help="list available Ink/Stitch font names and exit")
    p_let.add_argument("-o", "--out", help="output directory (default: ./out)")
    p_let.add_argument("--name", help="output basename (default: slug of --text)")
    _add_strict_audit_switch(p_let)
    p_let.set_defaults(func=cmd_lettering)

    p_gen = sub.add_parser("from-generative", help="PEmbroider sketch → .pes")
    p_gen.add_argument("sketch")
    p_gen.add_argument("--preset", required=True, choices=list_preset_names())
    p_gen.add_argument("-o", "--out")
    p_gen.add_argument("--name")
    _add_strict_audit_switch(p_gen)
    p_gen.set_defaults(func=cmd_from_generative)

    p_off = sub.add_parser(
        "offset",
        help="shift a PES design within the hoop by x/y mm (uses phantom-jump bbox extension)",
    )
    p_off.add_argument("input")
    p_off.add_argument("--x-mm", type=float, default=0.0,
                       help="horizontal shift in mm (sign depends on hat-hoop orientation)")
    p_off.add_argument("--y-mm", type=float, default=0.0,
                       help="vertical shift in mm (sign depends on hat-hoop orientation)")
    p_off.add_argument("-o", "--out",
                       help="output PES path (default: <input>-offset_x...y....pes)")
    _add_strict_audit_switch(p_off)
    p_off.set_defaults(func=cmd_offset)

    p_conv = sub.add_parser("convert", help="any embroidery format → PES v6")
    p_conv.add_argument("input")
    p_conv.add_argument("-o", "--out")
    p_conv.add_argument(
        "--profile",
        choices=["satin-wordmark", "satin-lettering", "satin-outline",
                 "fill", "running", "mixed"],
        default="mixed",
        help="technique profile for the converted file (default: mixed safety checks)",
    )
    _add_strict_audit_switch(p_conv)
    p_conv.set_defaults(func=cmd_convert)

    p_dep = sub.add_parser("deploy", help="copy .pes to a USB volume")
    p_dep.add_argument("input")
    p_dep.add_argument("--device", required=True, help="mounted USB path, e.g. /Volumes/USB")
    p_dep.add_argument("--preset", help="preset name to print the load-out checklist")
    p_dep.add_argument(
        "--allow-audit-failures",
        action="store_true",
        help="deploy despite universal hotspot/routing failures (unsafe; test only)",
    )
    p_dep.set_defaults(func=cmd_deploy)

    p_pre = sub.add_parser("presets", help="list / show materials presets")
    pre_sub = p_pre.add_subparsers(dest="action", required=True)
    pre_sub.add_parser("list")
    pre_show = pre_sub.add_parser("show")
    pre_show.add_argument("name")
    p_pre.set_defaults(func=cmd_presets)

    p_th = sub.add_parser("threads", help="thread inventory")
    th_sub = p_th.add_subparsers(dest="action", required=True)
    th_sub.add_parser("list")
    p_th.set_defaults(func=cmd_threads)

    p_aud = sub.add_parser(
        "audit", help="score a .pes against the pro-digitized quality bands")
    p_aud.add_argument("files", nargs="+")
    p_aud.add_argument("--strict", action="store_true",
                       help="exit non-zero if any file falls outside a band")
    p_aud.add_argument("--profile",
                       choices=["satin-wordmark", "satin-lettering", "satin-outline",
                                "fill", "running", "mixed"],
                       default=None,
                       help="select technique-appropriate quality bands "
                            "(default: strict satin bands plus advisory rows)")
    p_aud.set_defaults(func=cmd_audit)

    p_ins = sub.add_parser("inspect", help="print stitch count, bounds, threadlist")
    p_ins.add_argument("input")
    p_ins.set_defaults(func=cmd_inspect)

    p_prev = sub.add_parser(
        "preview",
        help="render a stitch-plan SVG (and optional PNG) from any embroidery file",
    )
    p_prev.add_argument("input")
    p_prev.add_argument("--png", action="store_true", help="also write a PNG raster")
    p_prev.add_argument("--open", action="store_true", help="open the SVG preview after writing")
    p_prev.set_defaults(func=cmd_preview)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
