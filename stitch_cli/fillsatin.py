"""Fill→satin conversion pass for outline-mode wordmark traces.

trace_lib.py --satin-mode outline emits each satin branch as a potrace FILL
(`class="satin-fill"`, with `data-col-w-mm`) plus synthesized direction rungs
(`class="rung"`, `data-for=<fill id>`), grouped per glyph in
`<g data-satin-mode="fill">`. This module turns those into routed satin
columns using Ink/Stitch's own tools, which is how the pro files are built:

  1. per-branch `fill_to_satin` — rails come from the true outline, split at
     our rungs (a straight stem with 2 terminal rungs = ONE merged column);
  2. per-glyph `auto_satin` — orders the columns and adds running travel
     inside the glyph (the pro signature: ~16% hidden runs, 1 trim/glyph);
  3. width-tiered parameter stamping via the data-* attrs tuning.py honors,
     including the min-column sewn-width floor applied as pull compensation
     (the traced outline stays true; the sewn column reaches 1.05mm).

Both effects are selection-based: they MUST be called with explicit ids or
the headless binary hangs waiting for an Inkscape selection. fill_to_satin
drops ids and stamps satin_column="True" (capital T — tuning normalizes),
so every converted path is re-id'd here before auto_satin can select it.

A branch whose conversion fails keeps its fill in place and is logged loudly
— tuning.py then stitches it as auto_fill, which sews but is visibly worse;
fix the branch (sidecar rungs) rather than shipping that.
"""

from __future__ import annotations

import copy
import re
import tempfile
from pathlib import Path

from lxml import etree

from . import inkstitch

SVG_NS = "http://www.w3.org/2000/svg"
INKSTITCH_NS = "http://inkstitch.org/namespace"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"

# Sewn-width floor, matched to TraceConfig.min_col_mm and the audit band
# satin_w10 >= 1.00 (pro corpus floors at 1.04-1.53mm).
MIN_COL_MM = 1.05


def _qn(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _is_satin(elem: etree._Element) -> bool:
    return (elem.get(_qn(INKSTITCH_NS, "satin_column")) or "").lower() == "true"


_NUM = re.compile(r"-?\d+\.?\d*")


def _load_ink(source_svg: Path) -> dict | None:
    """Load the run-length ink mask trace_lib writes next to an outline-mode
    SVG (<svg path>.ink.rle). None if the sidecar is missing."""
    p = Path(str(source_svg) + ".ink.rle")
    if not p.exists():
        return None
    lines = p.read_text().splitlines()
    w, h, ds, x0, y0, mmpp, margin = lines[0].split()
    rows = []
    for ln in lines[1:]:
        rows.append([(int(s), int(s) + int(n))
                     for s, n in (tok.split(":") for tok in ln.split())])
    return {"rows": rows, "ds": int(ds), "x0": int(x0), "y0": int(y0),
            "mmpp": float(mmpp), "margin": float(margin)}


def _polyline_buried(ink: dict, pairs: list[tuple[float, float]]) -> float:
    """Fraction of a polyline's sampled points that lie on the ink."""
    if len(pairs) < 2:
        return 1.0
    inside = total = 0
    for (ax, ay), (bx, by) in zip(pairs, pairs[1:]):
        seg = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        n = max(1, int(seg / 0.3))
        for k in range(n + 1):
            x = ax + (bx - ax) * k / n
            y = ay + (by - ay) * k / n
            fx = (x - ink["margin"]) / ink["mmpp"] + ink["x0"]
            fy = (y - ink["margin"]) / ink["mmpp"] + ink["y0"]
            ix, iy = int(fx // ink["ds"]), int(fy // ink["ds"])
            total += 1
            if 0 <= iy < len(ink["rows"]) and any(
                    s <= ix < e for s, e in ink["rows"][iy]):
                inside += 1
    return inside / max(1, total)


def _pairs(elem: etree._Element) -> list[tuple[float, float]]:
    nums = [float(v) for v in _NUM.findall(elem.get("d") or "")]
    return list(zip(nums[::2], nums[1::2]))


def _buried_fraction(ink: dict, elem: etree._Element) -> float:
    """Fraction of a running path's length that lies on the ink."""
    return _polyline_buried(ink, _pairs(elem))




def _tier_attrs(w_mm: float) -> dict[str, str]:
    """Width-tiered satin params (consumed by tuning.py's satin branch).

    Spacing 0.38 keeps the measured pitch mid-band (pro median advance 0.32,
    audit band 0.30-0.44) while respecting the density ceiling at 99mm.
    Narrow columns take their sewn-width floor as pull compensation: the
    potrace outline sits slightly inside the ink, so the target is padded
    (1.15) to measure >= 1.0 on the machine. Mid columns get center walk
    only — pro lettering at this width carries no zigzag underlay and the
    density band has no room for it."""
    if w_mm < 1.5:
        pull = max(0.10, round((1.25 - w_mm) / 2, 2))
        return {"data-satin-underlay": "center",
                "data-pull-comp-mm": f"{pull:.2f}",
                "data-satin-spacing-mm": "0.38"}
    if w_mm < 2.5:
        return {"data-satin-underlay": "center",
                "data-satin-spacing-mm": "0.38"}
    return {"data-satin-underlay": "center+contour",
            "data-satin-spacing-mm": "0.38"}


def _mini_svg(root: etree._Element, elems: list[etree._Element]) -> bytes:
    """A standalone SVG (same user units) holding copies of ``elems``.

    The inkstitch_svg_version metadata is required: a document carrying
    inkstitch:* attributes without it trips Ink/Stitch's 'unversioned file'
    update dialog, which blocks forever in a headless run."""
    from .tuning import INKSTITCH_SVG_VERSION

    svg = etree.Element(_qn(SVG_NS, "svg"),
                        nsmap={None: SVG_NS, "inkstitch": INKSTITCH_NS})
    for attr in ("width", "height", "viewBox"):
        if root.get(attr):
            svg.set(attr, root.get(attr))
    meta = etree.SubElement(svg, _qn(SVG_NS, "metadata"))
    ver = etree.SubElement(meta, _qn(INKSTITCH_NS, "inkstitch_svg_version"))
    ver.text = INKSTITCH_SVG_VERSION
    for el in elems:
        svg.append(copy.deepcopy(el))
    return etree.tostring(svg, xml_declaration=True, encoding="utf-8")


def _convert_branch(root, fill, rungs, next_id, workdir):
    """fill_to_satin on one branch in isolation. Returns the converted satin
    paths (re-id'd, tier-stamped) or None on failure."""
    in_svg = workdir / "branch-in.svg"
    out_svg = workdir / "branch-out.svg"
    in_svg.write_bytes(_mini_svg(root, [fill] + rungs))
    try:
        inkstitch.run_effect(
            "fill_to_satin", in_svg, out_svg,
            params={"keep_originals": False, "center": False,
                    "contour": False, "zigzag": False,
                    "pull_compensation_mm": "0", "skip_end_section": False},
            ids=[fill.get("id")] + [r.get("id") for r in rungs],
            timeout_s=60)
    except Exception as exc:  # noqa: BLE001 — log and fall through to fill
        print(f"  !! fill_to_satin failed on {fill.get('id')}: {exc}")
        return None
    converted = [p for p in etree.parse(str(out_svg)).getroot()
                 .iter(_qn(SVG_NS, "path")) if _is_satin(p)]
    if not converted:
        print(f"  !! fill_to_satin produced no satin for {fill.get('id')}")
        return None
    w = float(fill.get("data-col-w-mm") or MIN_COL_MM)
    tier = _tier_attrs(w)
    out = []
    for p in converted:
        el = copy.deepcopy(p)
        el.set("id", f"sat{next_id + len(out)}")
        el.set(_qn(INKSTITCH_NS, "satin_column"), "true")  # normalize "True"
        el.set("data-col-w-mm", f"{w:.2f}")
        for k, v in tier.items():
            el.set(k, v)
        out.append(el)
    return out


def convert_outline_svg(source_svg: Path, output_svg: Path,
                        run_auto_satin: bool = True) -> Path:
    """Convert every outline-mode glyph group in ``source_svg``; write the
    result (still un-tuned — tune_svg runs after this) to ``output_svg``."""
    tree = etree.parse(str(source_svg))
    root = tree.getroot()
    groups = root.findall(f".//{_qn(SVG_NS, 'g')}[@data-satin-mode='fill']")
    if not groups:
        raise ValueError(f"{source_svg} has no data-satin-mode='fill' groups")

    n_branch = n_fail = 0
    next_id = 0
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)

        # Pass 1 — per-branch fill_to_satin, isolated so one rough serif
        # can't take down the build.
        for group in groups:
            fills = [p for p in group.findall(_qn(SVG_NS, "path"))
                     if p.get("class") == "satin-fill"]
            rungs_by_fill: dict[str, list[etree._Element]] = {}
            for r in group.findall(_qn(SVG_NS, "path")):
                if r.get("class") == "rung":
                    rungs_by_fill.setdefault(r.get("data-for"), []).append(r)
            fallbacks = {p.get("data-for"): p
                         for p in group.findall(_qn(SVG_NS, "path"))
                         if p.get("class") == "satin-fallback"}
            for fill in fills:
                n_branch += 1
                fid = fill.get("id")
                rungs = rungs_by_fill.get(fid, [])
                converted = _convert_branch(root, fill, rungs,
                                            next_id, workdir)
                for r in rungs:
                    group.remove(r)
                fb = fallbacks.pop(fid, None)
                if converted is None:
                    n_fail += 1
                    if fb is not None:
                        # Promote the hidden centerline to a bean hairline —
                        # what a branch this thin should have been anyway.
                        w = float(fb.get("data-col-w-mm") or 0.4)
                        sw = min(0.9, max(0.35, w))
                        fb.attrib.pop("class")
                        fb.set("style", f"fill:none;stroke:#000000;"
                                        f"stroke-width:{sw:.2f};stroke-linecap:round")
                        fb.set("data-stroke-method", "bean_stitch")
                        fb.set("data-bean-repeats", "2" if w > 0.5 else "1")
                        group.remove(fill)
                        print(f"     -> {fid} demoted to bean hairline")
                    continue
                pos = group.index(fill)
                for off, el in enumerate(converted):
                    group.insert(pos + off, el)
                group.remove(fill)
                if fb is not None:
                    group.remove(fb)
                next_id += len(converted)

        print(f"fill_to_satin: {n_branch - n_fail}/{n_branch} branches "
              f"converted ({next_id} satin sections)"
              + (f", {n_fail} FAILED — left as auto_fill, fix these"
                 if n_fail else ""))

        # Pass 2 — per-glyph auto_satin routing, ISOLATED like pass 1:
        # auto_satin is seconds on a single-glyph document but times out on
        # the full design (its runtime scales with every element in the doc,
        # not the selection). Route each glyph in a mini SVG and rebuild the
        # group from the routed document's element order.
        if run_auto_satin:
            for group in list(root.findall(
                    f".//{_qn(SVG_NS, 'g')}[@data-satin-mode='fill']")):
                sat_ids = [p.get("id") for p in group.iter(_qn(SVG_NS, "path"))
                           if _is_satin(p) and p.get("id")]
                if len(sat_ids) < 2:
                    continue
                gid = group.get("id")
                in_svg = workdir / "route-in.svg"
                out_svg = workdir / "route-out.svg"
                in_svg.write_bytes(_mini_svg(root, [group]))
                try:
                    # trim=True: wherever auto_satin breaks its chain it
                    # attaches a trim COMMAND (symbol defs + use + connector
                    # markup) — keep all of it, or the break becomes an
                    # untrimmed jump dragging thread across open counters.
                    inkstitch.run_effect(
                        "auto_satin", in_svg, out_svg,
                        params={"trim": True, "preserve_order": False,
                                "keep_originals": False},
                        ids=sat_ids, timeout_s=120)
                except Exception as exc:  # noqa: BLE001
                    print(f"  !! auto_satin failed on {gid}: {exc} "
                          f"(glyph left in document order)")
                    continue
                routed_root = etree.parse(str(out_svg)).getroot()
                # auto_satin may hoist routed elements out of our group into
                # its own wrappers — rebuild the glyph group from every
                # non-metadata element in the routed doc, in document order.
                # Its trim-command symbol definitions live in <defs>: merge
                # those into the main document so the use references resolve.
                new_group = etree.Element(_qn(SVG_NS, "g"), attrib=dict(group.attrib))
                for child in routed_root:
                    tag = etree.QName(child).localname
                    if tag == "metadata":
                        continue
                    if tag == "defs":
                        main_defs = root.find(_qn(SVG_NS, "defs"))
                        if main_defs is None:
                            main_defs = etree.Element(_qn(SVG_NS, "defs"))
                            root.insert(0, main_defs)
                        have = {d.get("id") for d in main_defs}
                        for sym in child:
                            if sym.get("id") not in have:
                                main_defs.append(sym)
                        continue
                    if tag == "g" and child.get("id") == gid:
                        for sub in child:
                            new_group.append(sub)
                    else:
                        new_group.append(child)
                parent = group.getparent()
                parent.replace(group, new_group)

    # Pass 3 — trim policy. auto_satin's own trim commands cover its chain
    # breaks (kept above, defs and all); everything else runs on into the
    # next element (tuning's trim/lock coupling would otherwise reintroduce
    # a trim per column). Each glyph's LAST stitchable element trims.
    ink = _load_ink(source_svg)
    if ink is None:
        print("  (no .ink.rle sidecar — mid-air connector check skipped)")
    n_cut = [0]
    for group in root.findall(f".//{_qn(SVG_NS, 'g')}[@data-satin-mode='fill']"):
        stitchable = [p for p in group.iter(_qn(SVG_NS, "path"))
                      if p.get("class") not in ("rung", "satin-fallback")
                      and not (p.get("id") or "").startswith("command_")]
        # auto_satin connects columns with straight running paths — it has no
        # idea where the ink is, so across a V or W a connector sews right
        # over the open counter. Test each one against the ink mask trace_lib
        # sidecars next to the SVG; cut the mid-air ones and trim out instead.
        cut_after = []
        if ink is not None:
            for p in list(stitchable):
                if not (p.get("id") or "").startswith("autosatinrun"):
                    continue
                if _buried_fraction(ink, p) < 0.97:
                    i = stitchable.index(p)
                    if i > 0:
                        cut_after.append(stitchable[i - 1])
                    stitchable.remove(p)
                    p.getparent().remove(p)
                    n_cut[0] += 1
        # Beans and travel paths arrive from trace_lib carrying its INK-AWARE
        # trim policy (a bean→bean hop across a gap already trims) — leave
        # those attrs alone. Only the routed satin chain and auto_satin's
        # connectors are forced to run on.
        def _is_bean(el):
            return el.get("data-stroke-method") in ("bean_stitch",
                                                    "running_stitch")
        for p in stitchable:
            if not _is_bean(p):
                p.set("data-trim-after", "false")
        for p in cut_after:
            p.set("data-trim-after", "true")
        # The group rebuild loses the original interleave, so trace_lib's
        # bean trim policy points at neighbours that are no longer next:
        # re-test every hop. Bean/travel endpoints are reliable (auto_satin
        # never touches them), so bean→bean hops get the ink test; any hop
        # touching a satin (whose stitch end is unknowable post-routing)
        # trims unconditionally at the kind boundary.
        for p, nxt in zip(stitchable, stitchable[1:]):
            if _is_bean(p) != _is_bean(nxt):
                p.set("data-trim-after", "true")
            elif _is_bean(p) and _is_bean(nxt) and ink is not None:
                pa, pb = _pairs(p), _pairs(nxt)
                if pa and pb:
                    hop = [pa[-1], pb[0]]
                    d = ((hop[1][0] - hop[0][0]) ** 2
                         + (hop[1][1] - hop[0][1]) ** 2) ** 0.5
                    if d > 1.0 and _polyline_buried(ink, hop) < 0.97:
                        p.set("data-trim-after", "true")
        if stitchable:
            stitchable[-1].set("data-trim-after", "true")

    if n_cut[0]:
        print(f"  cut {n_cut[0]} mid-air connector(s) crossing open counters")
    tree.write(str(output_svg), xml_declaration=True, encoding="utf-8")
    print(f"wrote {output_svg}")
    return output_svg
