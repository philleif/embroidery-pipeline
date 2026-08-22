"""High-res needle-path render of a PES, with optional source-ink overlay.

The QA companion to `stitch preview`: renders at a chosen px/mm (preview PNGs
top out too low to judge coverage), and with --src draws the artwork's ink
underneath in pink so gaps and overshoots are obvious — uncovered ink shows
pink, spill shows black outside the pink.

Note the caveat that cost us a false alarm once: this draws each satin bite as
a thick LINE, so bite ends scallop the column edge and a perfectly straight
column reads jagged at high zoom. For edge inspection use the tracer's
--finish-png (filled rail polygons) instead; this tool is for coverage and
stray-travel checks.

  uv run --with numpy --with pillow --with pyembroidery python scripts/qa_render.py \
      out/foo.pes --out /tmp/foo_qa.png --pxmm 30 --src designs/artwork/foo.png \
      [--crop X0 Y0 X1 Y1]   # mm window
"""
import argparse

import numpy as np
import pyembroidery
from PIL import Image, ImageDraw

ap = argparse.ArgumentParser()
ap.add_argument("pes")
ap.add_argument("--out", required=True)
ap.add_argument("--pxmm", type=float, default=24.0)
ap.add_argument("--crop", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"),
                help="crop window in mm from the design origin")
ap.add_argument("--src", default=None,
                help="source artwork PNG to draw underneath (pink)")
ap.add_argument("--thread-mm", type=float, default=0.4)
cli = ap.parse_args()

pat = pyembroidery.read(cli.pes)
st = np.array([(x, y, c) for x, y, c in pat.stitches], float)
xy = st[:, :2] / 10.0                    # 0.1mm units -> mm
cmd = st[:, 2].astype(int)
xy -= [xy[:, 0].min(), xy[:, 1].min()]
w_mm, h_mm = xy[:, 0].max(), xy[:, 1].max()
print(f"design {w_mm:.1f} x {h_mm:.1f} mm, {len(xy)} stitch records")

cx0, cy0, cx1, cy1 = cli.crop if cli.crop else (0, 0, w_mm, h_mm)
W, H = int((cx1 - cx0) * cli.pxmm), int((cy1 - cy0) * cli.pxmm)
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)

if cli.src:
    s = Image.open(cli.src).convert("LA")
    a = np.array(s)
    al = a[..., 1].astype(float) / 255
    ink = (a[..., 0].astype(float) * al + 255 * (1 - al)) < 128
    ys, xs = np.where(ink)
    sub = Image.fromarray(
        (~ink[ys.min():ys.max() + 1, xs.min():xs.max() + 1] * 255).astype(np.uint8))
    sub = sub.resize((max(1, int(w_mm * cli.pxmm)), max(1, int(h_mm * cli.pxmm))),
                     Image.LANCZOS)
    arr = np.array(sub)
    tint = np.stack([np.full_like(arr, 255), arr // 2 + 128, arr // 2 + 128], -1)
    img.paste(Image.fromarray(tint.astype(np.uint8)),
              (int(-cx0 * cli.pxmm), int(-cy0 * cli.pxmm)))

pen = max(1, int(cli.thread_mm * cli.pxmm))
skipped = 0
for i in range(1, len(xy)):
    if cmd[i] in (pyembroidery.JUMP, pyembroidery.TRIM,
                  pyembroidery.COLOR_CHANGE, pyembroidery.STOP, pyembroidery.END):
        skipped += 1
        continue
    d.line([((xy[i - 1, 0] - cx0) * cli.pxmm, (xy[i - 1, 1] - cy0) * cli.pxmm),
            ((xy[i, 0] - cx0) * cli.pxmm, (xy[i, 1] - cy0) * cli.pxmm)],
           fill=(20, 20, 25), width=pen)
print(f"non-stitch records skipped: {skipped}")
img.save(cli.out)
print("wrote", cli.out, img.size)
