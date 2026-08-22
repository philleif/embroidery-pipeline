"""Compose the Court Street Journal hybrid: Ink/Stitch satin font for the sans
words + the traced calligraphic J (mixed satin-column/bean, from trace_csj.py).

Layout constants come from the source raster (csj-full.png, 1344x256):
sans caps span rows 75..155 (capH 81px), the J spans rows 66..189 and
cols 814..989, gaps STREET->J 28px and J->OURNAL 13px. The shared scale is
mm-per-source-px derived from the font's rendered cap height, so the J keeps
its original size relationship to the sans and the baselines line up.

Run inside the project venv:  python scripts/compose_csj_hybrid.py [--font F]
The checked-in `designs/csj-j-only.svg` is the stable J master; it is scaled in
SVG space before digitizing so stitch spacing remains a physical parameter.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import pyembroidery
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stitch_cli.lettering import make_lettering_pes
from stitch_cli import audit as audit_mod

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

CAP_TOP, BASELINE = 75, 155          # sans rows in source px
CAP_H_PX = BASELINE - CAP_TOP + 1    # 81
J_TOP, J_BOT = 66, 189
J_X0, J_X1 = 814, 989
GAP_S2J_PX, GAP_J2O_PX = 28, 13
J_BASE_FRAC = (BASELINE - J_TOP) / (J_BOT - J_TOP)   # baseline within J height

TARGET_W_MM = 98.0
SATIN_OVERHANG_MM = 0.4              # rendered caps exceed ink by ~this much
SVG_NS = "http://www.w3.org/2000/svg"
NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def scale_j_master(source: Path, output: Path, target_ink_w_mm: float,
                   margin_mm: float = 1.5) -> Path:
    """Scale the checked-in J rails to an exact ink width without scaling pitch."""
    if target_ink_w_mm <= 0:
        raise ValueError("J target width must be positive")
    tree = etree.parse(str(source))
    root = tree.getroot()
    paths = root.xpath(".//svg:path", namespaces={"svg": SVG_NS})
    coords = []
    for path in paths:
        nums = [float(value) for value in NUMBER_RE.findall(path.get("d", ""))]
        if len(nums) < 2 or len(nums) % 2:
            raise ValueError(f"cannot measure J path {path.get('id', '<unnamed>')}")
        coords.extend(zip(nums[0::2], nums[1::2]))
    if not coords:
        raise ValueError("J master contains no measurable paths")
    xs, ys = zip(*coords)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    scale = target_ink_w_mm / (x1 - x0)
    width = target_ink_w_mm + 2 * margin_mm
    height = (y1 - y0) * scale + 2 * margin_mm

    wrapper = etree.Element(
        f"{{{SVG_NS}}}g",
        id="scaled-j",
        transform=(f"translate({margin_mm:.4f} {margin_mm:.4f}) "
                   f"scale({scale:.8f}) translate({-x0:.4f} {-y0:.4f})"),
    )
    for child in list(root):
        root.remove(child)
        wrapper.append(child)
    root.append(wrapper)
    root.set("width", f"{width:.2f}mm")
    root.set("height", f"{height:.2f}mm")
    root.set("viewBox", f"0 0 {width:.2f} {height:.2f}")
    root.set("data-audit-profile", "mixed")
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(output), encoding="utf-8", xml_declaration=False)
    return output


def build_parts(font: str, scale: int):
    p_cs = OUT / "csj-part-court-street.pes"
    p_o = OUT / "csj-part-urnal.pes"
    make_lettering_pes("COURT STREET", font, p_cs, scale=scale)
    make_lettering_pes("URNAL", font, p_o, scale=scale)
    for part in (p_cs, p_o):
        _, failures = audit_mod.audit(part, profile="satin-lettering")
        if failures:
            raise RuntimeError(f"unsafe lettering part {part.name}: {', '.join(failures)}")
    cs = pyembroidery.read(str(p_cs))
    o = pyembroidery.read(str(p_o))
    cap_h_mm = (cs.bounds()[3] - cs.bounds()[1]) / 10 - SATIN_OVERHANG_MM
    mmpx = cap_h_mm / CAP_H_PX
    j_w_mm = (J_X1 - J_X0 + 1) * mmpx

    j_svg = scale_j_master(
        ROOT / "designs" / "csj-j-only.svg",
        OUT / "csj-part-j-scaled.svg",
        j_w_mm,
    )
    subprocess.run(
        [sys.executable, "-m", "stitch_cli.main", "from-svg", str(j_svg),
         "--preset", "hat-twill-black-detail", "--name", "csj-part-j",
         "--strict-audit"],
        check=True, cwd=ROOT, capture_output=True, timeout=600,
    )
    j = pyembroidery.read(str(OUT / "csj-part-j.pes"))
    return cs, j, o, mmpx


def total_width(cs, j, o, mmpx):
    w = lambda pat: pat.bounds()[2] - pat.bounds()[0]
    return (w(cs) + w(j) + w(o)) / 10 + (GAP_S2J_PX + GAP_J2O_PX) * mmpx


def merge(parts_dys, output):
    """parts_dys: list of (pattern, dx, dy) in 0.1mm units."""
    if not parts_dys:
        raise ValueError("merge needs at least one pattern")
    out = pyembroidery.EmbPattern()
    out.threadlist.append(parts_dys[0][0].threadlist[0])
    for i, (pat, dx, dy) in enumerate(parts_dys):
        first = next(
            (s for s in pat.stitches
             if s[2] & pyembroidery.COMMAND_MASK == pyembroidery.STITCH),
            None,
        )
        if first is None:
            raise RuntimeError("cannot merge a pattern with no stitch penetrations")
        if i:
            out.add_command(pyembroidery.TRIM)
        out.add_stitch_absolute(pyembroidery.JUMP, first[0] + dx, first[1] + dy)
        for s in pat.stitches:
            cmd = s[2] & pyembroidery.COMMAND_MASK
            if cmd == pyembroidery.END:
                continue
            if cmd in (pyembroidery.COLOR_CHANGE, pyembroidery.STOP):
                continue
            out.add_stitch_absolute(s[2], s[0] + dx, s[1] + dy)
    out.add_command(pyembroidery.END)
    pyembroidery.write_pes(out, str(output), {"version": "6"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", default="Espresso tiny")
    ap.add_argument("--scale", type=int, default=72, help="starting font scale %%")
    ap.add_argument("--name", default="court-street-journal-hybrid")
    args = ap.parse_args()

    scale = args.scale
    for attempt in range(2):
        cs, j, o, mmpx = build_parts(args.font, scale)
        tw = total_width(cs, j, o, mmpx)
        print(f"scale {scale}%: total {tw:.1f}mm (cap {mmpx*CAP_H_PX:.1f}mm, "
              f"J {(j.bounds()[2]-j.bounds()[0])/10:.1f}mm)")
        if abs(tw - TARGET_W_MM) <= 1.0 or attempt == 1:
            break
        scale = max(1, round(scale * TARGET_W_MM / tw))

    b_cs, b_j, b_o = cs.bounds(), j.bounds(), o.bounds()
    baseline = b_cs[3] - b_cs[1]                     # sans bottom (y from its top)
    j_h = b_j[3] - b_j[1]
    dy_j = baseline - j_h * J_BASE_FRAC - b_j[1]
    x = -b_cs[0]
    parts = [(cs, x, -b_cs[1])]
    x += (b_cs[2] - b_cs[0]) + GAP_S2J_PX * mmpx * 10
    parts.append((j, x - b_j[0], dy_j))
    x += (b_j[2] - b_j[0]) + GAP_J2O_PX * mmpx * 10
    parts.append((o, x - b_o[0], baseline - b_o[3]))   # bottom-aligned to baseline

    out_pes = OUT / f"{args.name}.pes"
    merge(parts, out_pes)
    m = pyembroidery.read(str(out_pes))
    bb = m.bounds()
    print(f"wrote {out_pes}  {(bb[2]-bb[0])/10:.1f} x {(bb[3]-bb[1])/10:.1f} mm, "
          f"{sum(1 for s in m.stitches if (s[2] & pyembroidery.COMMAND_MASK) == pyembroidery.STITCH)} stitches")
    print("quality audit (mixed)")
    _, failures = audit_mod.audit(out_pes, profile="mixed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
