# stitch — Brother PE900 CLI toolchain

A small Python CLI that turns SVGs (and Processing sketches) into machine-ready
`.pes` files for a Brother PE900 home embroidery machine, with digitization
parameters driven by an inventory of the threads, needles, and fabrics actually
in the studio.

## What it does

```
designs/foo.svg                     materials/presets.toml ──┐
       │                                                    │
       ├──► topology + connector preflight                  │
       │                 │                                  │
       └──► stitch from-svg foo.svg --preset hat-twill-...  │
                         │                                  │
                         ▼                                  │
             isolated compiled-object assertions            │
                         │                                  │
                         ▼  Ink/Stitch CLI                  │
             staged PES → audit → PES/DST/previews          │
                         │                                  │
                         ▼  manifest promoted last          │
             out/foo.{pes,dst,preview.*,qa.png}              │
             out/foo.manifest.json                          │
                         │                                  │
                         ▼  verify hashes + source freshness
             stitch deploy --device /Volumes/USB
```

Then you walk the USB stick to the PE900, press the Start button, and
swap threads when prompted.

## What it doesn't do

- Drive the machine. The PE900 has Wireless LAN, but Brother's transfer apps
  are Windows-only and the protocol is closed; an
  operator presses the buttons.
- Fully auto-digitize arbitrary raster art. Wordmarks and lettering have a
  validated raster trace pipeline (`stitch_cli/tracing.py` + a thin per-job
  driver — see `docs/QUALITY-BAR.md`); photos and complex fills still need
  hand tuning or an outside digitizer.
- Talk to Brother's Wi-Fi transfer app. Closed protocol, not worth chasing.

## Install

Requires Python 3.11 or newer. The package is named `embroidery-pipeline`;
its command is `stitch`. Releases are hosted in this private GitHub repo,
so downloads and clones require an account with repository access.

### From a release

Download the `.whl` from [GitHub Releases](https://github.com/philleif/embroidery-pipeline/releases).
In the directory containing that file, run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install './embroidery_pipeline-0.1.0-py3-none-any.whl[trace]'
stitch --version
stitch init my-embroidery
cd my-embroidery
stitch presets list
```

On Windows, use `py -m venv .venv`, then `.venv\Scripts\Activate.ps1`
in PowerShell. Use `python` for the remaining commands. The `[trace]` extra
installs the raster tracing dependencies. Omit it if you only work with SVGs
or existing embroidery files.

`stitch init` copies editable material inventories and three example SVGs into
a working directory. It refuses to overwrite existing template files.

### From the source checkout

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
git clone https://github.com/philleif/embroidery-pipeline.git
cd embroidery-pipeline
uv sync --locked --extra trace
uv run stitch --version
uv run stitch presets list
```

Use `uv run stitch` in place of `stitch` in the examples, or activate `.venv`.
If Git requests authentication, sign in with `gh auth login` and run
`gh auth setup-git`, or clone with your configured SSH key. Do not put tokens
in clone URLs.

### Install the digitizing engine

Auditing, inspection, file conversion, and raster tracing work with the Python
package alone. Converting SVGs to stitches also requires
[Inkscape](https://inkscape.org/release/) and
[Ink/Stitch](https://inkstitch.org/docs/install/). Install them separately.
This pipeline has been used with Ink/Stitch on macOS; the Python package also
runs on Linux and Windows, but those digitizing-engine setups need verification.

On macOS:

```bash
brew install --cask inkscape
```

Launch Inkscape once, then follow Ink/Stitch's installation instructions for
your platform. The CLI searches `PATH` and the usual macOS application and
extension locations. If necessary, set the full path to the Ink/Stitch
executable, including `.exe` on Windows:

```bash
export STITCH_INKSTITCH_BIN="/absolute/path/to/inkstitch"
# Common macOS installation:
export STITCH_INKSTITCH_BIN="$HOME/Library/Application Support/org.inkscape.Inkscape/config/inkscape/extensions/inkstitch/inkstitch.app/Contents/MacOS/inkstitch"
```

PowerShell uses `$env:STITCH_INKSTITCH_BIN = 'C:\path\to\inkstitch.exe'`.
Point this at Ink/Stitch's executable, not Inkscape itself or the extension folder.

Processing and PEmbroider are needed only for `stitch from-generative`.
Install [Processing](https://processing.org/download) and
[PEmbroider](https://github.com/CreativeInquiry/PEmbroider), then put
`processing-java` on `PATH` or set `STITCH_PROCESSING_BIN` to its full path.
Hershey text commands use Inkscape's bundled Python and Hershey extension;
see `STITCH_INKSCAPE_RESOURCES` in `stitch_cli/hershey.py` for a custom install.

### First design

From an initialized working directory or this checkout:

```bash
stitch from-svg designs/recipes/satin-bar.svg --preset hat-twill-satin
stitch inspect out/satin-bar.pes
stitch verify out/satin-bar.pes
```

The build writes PES, DST, stitch previews, a tuned SVG, and a manifest into
`out/`. Check the preview and sew a scrap test before using a new material preset.
See [the recipe guide](docs/RECIPES.md) for fill and bean-stitch examples.

Material lookup uses `STITCH_MATERIALS_DIR` when set, then `./materials`, then
the checkout's inventory or the installed package's defaults. Run commands
from your initialized directory to use its inventory. To use it from anywhere:

```bash
export STITCH_MATERIALS_DIR="/absolute/path/to/my-embroidery/materials"
```

Edit all four TOML files there to match your actual threads, fabrics, needles,
and presets. The bundled inventory is a starting point.

### Raster wordmarks

With the `[trace]` extra installed:

```bash
stitch trace artwork.png --width-mm 100 -o designs/wordmark.svg \
  --debug-png out/wordmark-debug.png --finish-png out/wordmark-finish.png
stitch from-svg designs/wordmark.svg --preset hat-twill-satin
```

Use dark artwork on a light or transparent background. Trace coverage and
renders help you tune the result; the SVG still needs review. The tracer
handles wordmarks and line art, not arbitrary photographs. `--satin-mode outline`
uses the alternate fill-to-satin route. Python drivers can import
`TraceConfig` and `WordmarkTracer` from `stitch_cli.tracing` for finer control.

## Working loop

```bash
# 1. Edit your spool inventory and presets to reflect reality.
$EDITOR materials/threads.toml materials/presets.toml

# 2. Tune + digitize.
stitch from-svg designs/ohhi-heart.svg --preset hat-twill-yellow-on-black

# 3. Inspect.
stitch inspect out/ohhi-heart.pes
#    (from-svg already printed the quality audit; re-score any file any time:)
stitch audit out/ohhi-heart.pes
#    Verify that the PES, DST, previews, tuned SVG, and source still match:
stitch verify out/ohhi-heart.pes

# 4. Plug in the USB stick (FAT32) and deploy with the load-out checklist.
stitch deploy out/ohhi-heart.pes --device /Volumes/USB --preset hat-twill-yellow-on-black

# 5. Stitch a test patch on scrap. If density looks off, tweak the preset
#    (not the SVG) and re-run from step 2.
```

Production-generating commands are strict by default: a failed
technique-aware audit returns a non-zero status, and `stitch deploy` refuses
files with universal hotspot, long-float, trim-fragmentation, or manifest
freshness failures. `from-svg` builds in a temporary staging directory and
publishes the manifest last; a failed preflight, compilation, or audit leaves
the previous known-good bundle in place. For an
intermediate scrap-only experiment, explicitly pass `--no-strict-audit`; USB
deployment requires the still more explicit `--allow-audit-failures` override.

## Development and releases

```bash
uv sync --locked --extra trace
uv run --extra trace python -m unittest discover -s tests -q
uv build
```

The wheel includes the CLI, tracing library, material defaults, and starter
recipes. See [distribution and release instructions](docs/DISTRIBUTION.md)
for artifact checks and the GitHub release workflow.

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
| `out/`                  | Atomic PES/DST/preview/QA bundles + manifests (gitignored)         |
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

### Structural and compiled QA metadata

Production SVGs can opt into fail-closed structural validation on the root:

```xml
<svg data-require-explicit-rungs="true"
     data-validate-connectors="true"
     data-reference-image="artwork/foo.png">
```

With explicit-rung mode enabled, every satin column needs at least three
interior direction rungs. Rungs at a rail endpoint are rejected because
Ink/Stitch can reinterpret the cap as a third rail. Travel paths named
`travel-*` (or marked `data-role="travel"`) are checked against the preceding
exit and following entry point; override the default 1.5mm tolerance with
`data-connector-tolerance-mm` only when the geometry deliberately requires it.

Critical components can also assert their *compiled* stitch bounds rather
than trusting the SVG preview:

```xml
<path id="core" data-qa-role="starburst-core"
      data-qa-min-width-mm="4.5" data-qa-min-height-mm="2.5"
      data-qa-max-height-mm="4.0" ... />
```

Supported checks are min/max width, height, and stitch count. Each annotated
object is compiled in isolation before the full design; a collapsed or missing
object blocks publication. `data-jig-rotation-deg` records intentional hoop
compensation and is applied when constructing the normalized visual-QA overlay.
