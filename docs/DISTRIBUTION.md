# Distribution and releases

The private repository is https://github.com/philleif/embroidery-pipeline.
GitHub Releases hosts the installable wheel, source distribution, and SHA-256
checksums. The Python package name is `embroidery-pipeline`; the command is
`stitch`. This project is not published to PyPI.

## What ships

The wheel contains `stitch_cli`, including the raster tracer, four material
TOML files, and three starter SVGs. The `trace` extra adds NumPy, SciPy,
scikit-image, and potracer. Core auditing and SVG workflows do not need that
extra. Inkscape, Ink/Stitch, Processing, and PEmbroider are separate installs.

The source distribution uses an explicit file list. It includes the package,
material defaults, recipes, setup and quality documentation, dependency lock,
and distribution tests. It excludes studio artwork, generated embroidery,
professional reference files, browser fonts, and design-specific scripts.
Those may still exist in the private Git repository and GitHub's automatic
source archives. Share the built wheel or `.tar.gz` package when distributing
the reusable pipeline.

No open-source license has been selected for this project. Repository access
does not establish permission to redistribute studio artwork or third-party
assets. Dependency licenses remain with their respective projects.

## Build and test

```bash
uv sync --locked --extra trace
uv run --extra trace python -m unittest discover -s tests -q
uv build
```

`uv build` builds the source distribution first, then builds the wheel from
that source distribution. This checks that the source package has the files
needed for a wheel. The GitHub checks also install the wheel into a separate
virtual environment and run the distribution tests outside the checkout.
Those checks run on macOS, Linux, and Windows with Python 3.11 and 3.14.
They do not install Ink/Stitch or operate an embroidery machine.

The build configuration follows [Hatch's file-selection documentation](https://hatch.pypa.io/latest/config/build/).
See [uv's build guide](https://docs.astral.sh/uv/guides/package/) for build options.

Before a release, also build and inspect a recipe with a local Ink/Stitch
installation:

```bash
uv run stitch from-svg designs/recipes/satin-bar.svg --preset hat-twill-satin
uv run stitch verify out/satin-bar.pes
```

## Publish a version

1. Update the version in `pyproject.toml` and `stitch_cli/__init__.py`.
2. Run `uv lock`, the tests, and `uv build`.
3. Commit the release changes and push `main`.
4. Create and push a matching tag, for example `git tag v0.1.0` and
   `git push origin v0.1.0`.

The tag workflow checks that both versions match, runs the package checks,
builds the release assets, calculates checksums, and publishes a GitHub
Release. It uses GitHub's repository token; no PyPI or personal token secret
is required. Releases inherit the repository's private visibility.

Do not move an existing release tag or overwrite its assets. Bump the version
for a correction so installed copies can be identified with `stitch --version`.

## Download and install

Authenticated repository members can download through the Releases page or:

```bash
gh release download v0.1.0 --repo philleif/embroidery-pipeline --pattern '*.whl' --pattern SHA256SUMS
python -m pip install './embroidery_pipeline-0.1.0-py3-none-any.whl[trace]'
stitch init my-embroidery
```

Use a virtual environment, as shown in the README. `SHA256SUMS` covers both
the wheel and source distribution; download both assets to check the entire
list with `shasum -a 256 -c SHA256SUMS` on macOS or `sha256sum -c SHA256SUMS`
on Linux. PowerShell can check each file with `Get-FileHash -Algorithm SHA256`.
