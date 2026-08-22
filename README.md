# stitch — Brother PE900 CLI toolchain

A small Python CLI that turns SVGs (and Processing sketches) into machine-ready
`.pes` files for a Brother PE900 home embroidery machine, with digitization
parameters driven by an inventory of the threads, needles, and fabrics actually
in the studio.

## What it does

```
designs/foo.svg                     materials/presets.toml ──┐
       │                                                    │
       └──► stitch from-svg foo.svg --preset hat-twill-...  │
                          │                                 │
                          ▼                                 │
                    out/foo.tuned.svg ◄── inkstitch:* attrs ┘
                          │
                          ▼  Ink/Stitch CLI
                    out/foo.pes
                          │
                          ▼  pyembroidery + quality audit
                    out/foo.pes (PES v6, normalized + strict quality gate)
                          │
                          ▼  stitch deploy --device /Volumes/USB
                    /Volumes/USB/foo.pes  + load-out checklist
```

Then you walk the USB stick to the PE900, press the Start button, and
swap threads when prompted.

## What it doesn't do

- Drive the machine. The PE900 has Wireless LAN, but Brother's transfer apps
  are Windows-only and the protocol is closed; an
  operator presses the buttons.
- Fully auto-digitize arbitrary raster art. Wordmarks and lettering have a
  validated raster trace pipeline (`scripts/trace_lib.py` + a thin per-job
  driver — see `docs/QUALITY-BAR.md`); photos and complex fills still need
  hand tuning or an outside digitizer.
- Talk to Brother's Wi-Fi transfer app. Closed protocol, not worth chasing.

## Install

```bash
# Python toolchain (uses uv; substitute pip if you prefer)
cd ~/LIFE/embroidery
uv venv --python 3.13
uv pip install -e .

# System dependencies (one-time)
brew install --cask inkscape
# Launch Inkscape once so it creates ~/Library/Application Support/org.inkscape.Inkscape/.
# Then download Ink/Stitch from https://inkstitch.org and unzip into
# ~/Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/
brew install --cask processing
# Drop PEmbroider into ~/Documents/Processing/libraries/PEmbroider/

# Optional overrides if the binaries aren't on PATH (the default Inkscape 1.4+
# install location is auto-detected, so usually you don't need to set this).
export STITCH_INKSTITCH_BIN="$HOME/Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/inkstitch/inkstitch.app/Contents/MacOS/inkstitch"
export STITCH_PROCESSING_BIN=/Applications/Processing.app/Contents/MacOS/processing-java
```

## v1 loop

```bash
# 1. Edit your spool inventory and presets to reflect reality.
$EDITOR materials/threads.toml materials/presets.toml

# 2. Tune + digitize.
.venv/bin/stitch from-svg designs/ohhi-heart.svg --preset hat-twill-yellow-on-black

# 3. Inspect.
.venv/bin/stitch inspect out/ohhi-heart.pes
#    (from-svg already printed the quality audit; re-score any file any time:)
.venv/bin/stitch audit out/ohhi-heart.pes

# 4. Plug in the USB stick (FAT32) and deploy with the load-out checklist.
.venv/bin/stitch deploy out/ohhi-heart.pes --device /Volumes/USB --preset hat-twill-yellow-on-black

# 5. Stitch a test patch on scrap. If density looks off, tweak the preset
#    (not the SVG) and re-run from step 2.
```

Production-generating commands are strict by default: a failed
technique-aware audit returns a non-zero status, and `stitch deploy` refuses
files with universal hotspot or trim-fragmentation failures. For an
intermediate scrap-only experiment, explicitly pass `--no-strict-audit`; USB
deployment requires the still more explicit `--allow-audit-failures` override.

## Layout

| Path                    | What lives there                                                  |
|-------------------------|-------------------------------------------------------------------|
| `stitch_cli/`           | Python source (CLI, materials loader, tuning pass, shell-outs)    |
| `materials/*.toml`      | Thread / needle / fabric inventories + composed presets           |
| `designs/*.svg`         | Hand-tuned source SVGs (lightweight, human-edited)                |
| `designs/artwork/`      | Source raster artwork the trace scripts consume                   |
| `scripts/`              | Raster trace pipeline (`trace_lib.py` + per-job drivers), QA render |
| `docs/QUALITY-BAR.md`   | Pro-derived quality bands enforced by `stitch audit`              |
| `generative/*/`         | PEmbroider Processing sketches                                    |
| `out/`                  | Generated `.tuned.svg` and `.pes` files (gitignored)              |
| `machine/pe900.md`      | Field limits, USB rules, trim behaviour, operator flow            |

## Version control

Git tracks the reproducible pipeline: source artwork and SVG masters,
design-specific overrides, scripts, CLI code, material presets, machine notes,
documentation, tests, and dependency locks. Everything under `out/` is a local
generated artifact and is ignored except for `out/.gitkeep`; rebuild PES files,
previews, tuned SVGs, and debug renders from the tracked inputs instead of
force-adding them. Historical iterations under `designs/archive/` are excluded
as well, while current files in `designs/` and `designs/artwork/` are retained.

## Editing source SVGs

The tuning pass classifies each `<path>` as one of:

- **fill** — has a `fill:` other than `none`. Becomes Ink/Stitch `auto_fill`.
- **satin** — explicitly marked with `inkstitch:satin_column="true"`. Becomes a
  satin column with the preset's underlay.
- **running** — stroke-only path with `fill:none`. Becomes a running stitch.

Pure satin SVGs automatically receive the `satin-wordmark` audit profile. Set
`data-audit-profile="satin-outline"` on decorative closed borders, or declare
`fill`, `running`, or `mixed` when the technique calls for those bands. The
raster wordmark tracer stamps `satin-wordmark` by default, so a new thin driver
cannot silently lose the full quality gate.

Set the source SVG's stroke and fill to any color you want — by default the
tuning pass re-tints to the preset's first thread color before Ink/Stitch sees
the file. The preset's `satin_underlay` and `fill_underlay` policies are applied
to every object unless that object has an explicit supported override.

For multi-color designs, put `data-thread="<inventory-thread-id>"` on each path
or ancestor group. The id must appear in the selected preset's `threads` list;
unknown ids fail before digitizing. Arrange the preset thread list in color-
block sew order so PES metadata and the machine's pause sequence stay aligned.
