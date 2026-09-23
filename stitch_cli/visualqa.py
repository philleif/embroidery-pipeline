"""Reference-versus-stitch-preview visual QA artifacts.

The result is intentionally a review aid, not a substitute for stitch metrics:
the source artwork may contain sub-thread-width details and the digitization
may deliberately widen them.  Normalized side-by-side and overlay panels make
missing components, collapsed shapes, clipping, and unexpected terminals easy
to spot while recording a coarse silhouette mismatch in the build manifest.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


def _pixels(image: Image.Image):
    """Iterate pixels without Pillow 12's deprecated getdata warning."""
    flattened = getattr(image, "get_flattened_data", None)
    return flattened() if flattened is not None else image.getdata()


def _background(image: Image.Image) -> tuple[int, int, int]:
    rgb = image.convert("RGB")
    w, h = rgb.size
    samples = [rgb.getpixel(point) for point in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    return tuple(sorted(sample[channel] for sample in samples)[len(samples) // 2]
                 for channel in range(3))


def _foreground_mask(image: Image.Image, threshold: int = 8) -> Image.Image:
    rgb = image.convert("RGB")
    bg = _background(rgb)
    pixels = []
    for r, g, b in _pixels(rgb):
        distance = max(abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2]))
        pixels.append(255 if distance > threshold else 0)
    mask = Image.new("L", rgb.size)
    mask.putdata(pixels)
    return mask


def _crop_foreground(image: Image.Image, mask: Image.Image) -> tuple[Image.Image, Image.Image]:
    bbox = mask.getbbox()
    if bbox is None:
        raise ValueError("visual QA image contains no detectable foreground")
    return image.crop(bbox), mask.crop(bbox)


def _fit(image: Image.Image, mask: Image.Image, size: tuple[int, int]) -> tuple[Image.Image, Image.Image]:
    target_w, target_h = size
    scale = min(target_w / image.width, target_h / image.height)
    dims = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    resized = image.resize(dims, Image.Resampling.LANCZOS)
    resized_mask = mask.resize(dims, Image.Resampling.LANCZOS)
    frame = Image.new("RGB", size, "black")
    frame_mask = Image.new("L", size, 0)
    offset = ((target_w - dims[0]) // 2, (target_h - dims[1]) // 2)
    frame.paste(resized.convert("RGB"), offset)
    frame_mask.paste(resized_mask, offset)
    return frame, frame_mask


def make_visual_qa(
    reference_path: Path,
    preview_path: Path,
    output_path: Path,
    *,
    svg_rotation_deg: float = 0.0,
) -> dict:
    reference = Image.open(reference_path).convert("RGB")
    preview = Image.open(preview_path).convert("RGB")
    reference_mask = _foreground_mask(reference)
    preview_mask = _foreground_mask(preview)
    reference, reference_mask = _crop_foreground(reference, reference_mask)
    preview, preview_mask = _crop_foreground(preview, preview_mask)

    # SVG y increases downward, so a negative SVG angle is visually
    # counterclockwise. Pillow uses the conventional positive-CCW sign.
    if abs(svg_rotation_deg) > 1e-9:
        pillow_angle = -svg_rotation_deg
        reference = reference.rotate(
            pillow_angle, Image.Resampling.BICUBIC, expand=True, fillcolor="black"
        )
        reference_mask = reference_mask.rotate(
            pillow_angle, Image.Resampling.BICUBIC, expand=True, fillcolor=0
        )
        reference, reference_mask = _crop_foreground(reference, reference_mask)

    panel_size = (1200, 420)
    reference_panel, reference_fit_mask = _fit(reference, reference_mask, panel_size)
    preview_panel, preview_fit_mask = _fit(preview, preview_mask, panel_size)

    ref_color = Image.new("RGB", panel_size, (230, 45, 150))
    preview_color = Image.new("RGB", panel_size, (25, 205, 235))
    overlay = Image.new("RGB", panel_size, "black")
    overlay.paste(ref_color, mask=reference_fit_mask)
    cyan_only = Image.new("RGB", panel_size, (25, 205, 235))
    overlay.paste(cyan_only, mask=preview_fit_mask)
    overlap = ImageChops.multiply(reference_fit_mask, preview_fit_mask)
    overlay.paste(Image.new("RGB", panel_size, "white"), mask=overlap)

    # Silhouette mismatch is advisory. Intentional satin widening raises it,
    # while a missing spoke or collapsed center raises it much more abruptly.
    binary_ref = reference_fit_mask.point(lambda value: 255 if value >= 128 else 0)
    binary_preview = preview_fit_mask.point(lambda value: 255 if value >= 128 else 0)
    union = ImageChops.lighter(binary_ref, binary_preview)
    xor = ImageChops.difference(binary_ref, binary_preview)
    union_px = sum(1 for value in _pixels(union) if value)
    mismatch_px = sum(1 for value in _pixels(xor) if value)
    mismatch_pct = 100.0 * mismatch_px / max(1, union_px)

    label_h = 34
    canvas = Image.new("RGB", (panel_size[0] * 3, panel_size[1] + label_h), "white")
    draw = ImageDraw.Draw(canvas)
    labels = (
        "REFERENCE ARTWORK",
        "COMPILED STITCH PREVIEW",
        "NORMALIZED OVERLAY — WHITE MATCH / MAGENTA-CYAN DIFFERENCE",
    )
    for index, (panel, label) in enumerate(
        zip((reference_panel, preview_panel, overlay), labels)
    ):
        x = index * panel_size[0]
        canvas.paste(panel, (x, label_h))
        draw.text((x + 10, 10), label, fill="black")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)
    return {
        "reference": str(reference_path.resolve()),
        # The preview is part of the adjacent atomic bundle. Recording only
        # its durable filename avoids leaking the temporary staging directory
        # into a manifest after that directory is removed.
        "preview": preview_path.name,
        "output": output_path.name,
        "rotation_deg": svg_rotation_deg,
        "silhouette_mismatch_pct": round(mismatch_pct, 2),
        "advisory": True,
    }
