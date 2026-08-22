from __future__ import annotations

import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import pyembroidery
from lxml import etree

from stitch_cli import audit, convert, deploy, inkstitch, lettering, main, processing, tuning
from stitch_cli.materials import Fabric, Needle, ResolvedPreset, Thread, list_preset_names, resolve_preset
from scripts.compose_csj_hybrid import scale_j_master


def _preset(*, satin_underlay: str = "center-walk+zigzag",
            fill_underlay: str = "edge-walk") -> ResolvedPreset:
    threads = (
        Thread("black", "Test", "Poly", "1", "#111111", 40, "polyester"),
        Thread("yellow", "Test", "Poly", "2", "#FFD400", 40, "polyester"),
    )
    return ResolvedPreset(
        name="test",
        fabric=Fabric("twill", "woven", 300, "none", "tearaway", 0.2),
        needle=Needle("75-11", "75/11", "embroidery"),
        threads=threads,
        row_spacing_mm=0.4,
        max_stitch_length_mm=3.0,
        pull_compensation_mm=0.2,
        satin_underlay=satin_underlay,
        fill_underlay=fill_underlay,
    )


def _write_two_block_pes(path: Path) -> None:
    pattern = pyembroidery.EmbPattern()
    pattern.add_thread("#111111")
    pattern.add_stitch_absolute(pyembroidery.STITCH, 0, 0)
    pattern.add_stitch_absolute(pyembroidery.STITCH, 10, 0)
    pattern.add_stitch_absolute(pyembroidery.JUMP, 1000, 1000)
    pattern.add_stitch_absolute(pyembroidery.STITCH, 1000, 1000)
    pattern.add_stitch_absolute(pyembroidery.STITCH, 1010, 1000)
    pattern.add_command(pyembroidery.END)
    pyembroidery.write_pes(pattern, str(path), {"version": "6"})


class AuditTests(unittest.TestCase):
    def test_metrics_do_not_bridge_non_stitch_commands(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pes = Path(td) / "blocks.pes"
            _write_two_block_pes(pes)
            metrics = audit.measure(pes)
            self.assertEqual(metrics["stitches"], 4)
            self.assertEqual(metrics["blocks"], 2)
            self.assertAlmostEqual(metrics["thread_m"], 0.002, places=6)
            self.assertAlmostEqual(metrics["p10_len"], 1.0)
            self.assertIn("peak_1mm_location", metrics)
            self.assertIn("peak_1mm_blocks", metrics)

    def test_offset_phantom_does_not_change_quality_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.pes"
            shifted = Path(td) / "shifted.pes"
            _write_two_block_pes(source)
            before = audit.measure(source)
            convert.offset_pes(source, shifted, x_mm=2.3, y_mm=3.7,
                               hoop_x_mm=200, hoop_y_mm=200)
            after = audit.measure(shifted)
            self.assertEqual(after["size_mm"], before["size_mm"])
            self.assertEqual(after["density"], before["density"])


class ConversionTests(unittest.TestCase):
    def test_describe_separates_penetrations_from_commands(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pes = Path(td) / "counts.pes"
            _write_two_block_pes(pes)
            summary = convert.describe(pes)
            self.assertEqual(summary["stitch_count"], 4)
            self.assertGreater(summary["command_count"], summary["stitch_count"])
            self.assertGreaterEqual(summary["jump_count"], 1)

    def test_scale_rejects_non_positive_factor(self) -> None:
        with self.assertRaises(ValueError):
            convert.scale_pes(Path("in.pes"), Path("out.pes"), 0)


class StackingTests(unittest.TestCase):
    def test_finished_width_accounts_for_pull_compensation(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 10">
          <path id="top" d="M 1 1 L 10 1"/>
          <path id="bottom" d="M 30 1 L 39 1"/>
        </svg>'''
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.svg"
            output = Path(td) / "stacked.svg"
            source.write_bytes(svg)
            subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "stack_wordmark_svg.py"),
                    str(source),
                    str(output),
                    "--target-content-width-mm", "101.6",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            stacked = etree.parse(str(output)).getroot()
            self.assertEqual(stacked.get("width"), "104.20mm")
            self.assertEqual(stacked.get("data-layout"), "stacked")

    def test_csj_hybrid_uses_scalable_checked_in_j_master(self) -> None:
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "j.svg"
            scale_j_master(root / "designs" / "csj-j-only.svg", output, 12.5)
            scaled = etree.parse(str(output)).getroot()
            self.assertEqual(scaled.get("width"), "15.50mm")
            self.assertEqual(scaled.get("data-audit-profile"), "mixed")


class TuningTests(unittest.TestCase):
    def test_inherited_thread_and_preset_underlay_are_honored(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg"
            xmlns:inkstitch="http://inkstitch.org/namespace" viewBox="0 0 20 20">
          <g data-thread="yellow">
            <path id="satin" style="fill:none;stroke:#000000"
              inkstitch:satin_column="true" d="M 1 1 L 1 10 M 3 1 L 3 10 M 1 5 L 3 5"/>
          </g>
          <path id="fill" style="fill:#000000;stroke:none" d="M 5 5 h 5 v 5 h -5 z"/>
        </svg>'''
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.svg"
            output = Path(td) / "output.svg"
            source.write_bytes(svg)
            tuning.tune_svg(
                source, output,
                _preset(satin_underlay="none", fill_underlay="none"),
            )
            root = etree.parse(str(output)).getroot()
            ns = {"svg": tuning.SVG_NS, "ink": tuning.INKSTITCH_NS}
            satin = root.xpath(".//svg:path[@id='satin']", namespaces=ns)[0]
            fill = root.xpath(".//svg:path[@id='fill']", namespaces=ns)[0]
            self.assertIn("stroke:#FFD400", satin.get("style"))
            self.assertEqual(satin.get(f"{{{tuning.INKSTITCH_NS}}}center_walk_underlay"), "false")
            self.assertEqual(satin.get(f"{{{tuning.INKSTITCH_NS}}}zigzag_underlay"), "false")
            self.assertEqual(fill.get(f"{{{tuning.INKSTITCH_NS}}}fill_underlay"), "false")

    def test_unknown_thread_fails_before_digitizing(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
          <path data-thread="missing" style="fill:#000" d="M0 0h5v5z"/>
        </svg>'''
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.svg"
            output = Path(td) / "output.svg"
            source.write_bytes(svg)
            with self.assertRaisesRegex(ValueError, "data-thread"):
                tuning.tune_svg(source, output, _preset())

    def test_per_path_satin_spacing_override_is_honored(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg"
            xmlns:inkstitch="http://inkstitch.org/namespace" viewBox="0 0 20 20">
          <path id="wide" style="fill:none;stroke:#000000"
            data-satin-spacing-mm="0.38" inkstitch:satin_column="true"
            d="M 1 1 L 1 10 M 4 1 L 4 10 M 1 5 L 4 5"/>
        </svg>'''
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.svg"
            output = Path(td) / "output.svg"
            source.write_bytes(svg)
            tuning.tune_svg(source, output, _preset())
            root = etree.parse(str(output)).getroot()
            path = root.xpath(
                ".//svg:path[@id='wide']", namespaces={"svg": tuning.SVG_NS}
            )[0]
            self.assertEqual(
                path.get(f"{{{tuning.INKSTITCH_NS}}}zigzag_spacing_mm"), "0.38"
            )


class ArtifactBoundaryTests(unittest.TestCase):
    def test_selection_effect_requires_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "selected element id"):
            inkstitch.run_effect(
                "fill_to_satin", Path("in.svg"), Path("out.svg")
            )

    def test_processing_refuses_stale_pes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sketch = Path(td) / "sketch"
            sketch.mkdir()
            (sketch / "old.pes").write_bytes(b"old")
            output = Path(td) / "output.pes"
            with mock.patch.object(processing, "_find_processing_bin", return_value="processing-java"), \
                 mock.patch.object(processing.subprocess, "run"):
                with self.assertRaisesRegex(RuntimeError, "stale artifact"):
                    processing.run_sketch(sketch, _preset(), output)

    def test_deploy_rejects_machine_unsafe_filename(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "bad name.pes"
            source.write_bytes(b"not parsed because the name fails first")
            with self.assertRaisesRegex(ValueError, "filename"):
                deploy.deploy(source, Path(td))

    def test_deploy_copies_and_verifies_valid_pes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "valid_name.pes"
            device = Path(td) / "usb"
            device.mkdir()
            _write_two_block_pes(source)
            with mock.patch.object(deploy, "_is_fat32", return_value=True), \
                 mock.patch.object(deploy.subprocess, "run"):
                destination = deploy.deploy(source, device)
            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_deploy_rejects_universal_quality_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "unsafe.pes"
            device = Path(td) / "usb"
            device.mkdir()
            _write_two_block_pes(source)
            metrics = {"peak_1mm": 22.0, "trims_per_1k": 0.0}
            with mock.patch.object(
                deploy.audit_mod, "audit", return_value=(metrics, ["peak_1mm"])
            ):
                with self.assertRaisesRegex(RuntimeError, "refusing to deploy"):
                    deploy.deploy(source, device)


class InventoryAndMergeTests(unittest.TestCase):
    def test_all_checked_in_presets_validate(self) -> None:
        names = list_preset_names()
        self.assertTrue(names)
        for name in names:
            self.assertEqual(resolve_preset(name).name, name)

    def test_vertical_merge_requires_input(self) -> None:
        with self.assertRaises(ValueError):
            lettering.merge_pes_vertical([], Path("unused.pes"))

    def test_profile_sets_keep_universal_hotspot_check(self) -> None:
        for profile in ("satin-wordmark", "satin-lettering", "satin-outline",
                        "fill", "running", "mixed"):
            self.assertIn("peak_1mm", audit.PROFILE_BASE_KEYS[profile])

    def test_svg_audit_profiles_are_inferred(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.assertEqual(
            tuning.audit_profile_for_svg(root / "designs/hearts-2in.svg"),
            "satin-outline",
        )
        self.assertEqual(
            tuning.audit_profile_for_svg(root / "designs/ptm-badge-2in.svg"),
            "fill",
        )
        self.assertEqual(
            tuning.audit_profile_for_svg(root / "designs/paris-review-5in.svg"),
            "satin-wordmark",
        )

    def test_production_builds_are_strict_by_default(self) -> None:
        parser = main.build_parser()
        preset = list_preset_names()[0]
        commands = (
            ["from-svg", "design.svg", "--preset", preset],
            ["hershey", "design.svg"],
            ["swatch", "--preset", preset],
            ["lettering-swatch"],
            ["lettering-logo"],
            ["lettering", "--text", "A", "--font", "Test"],
            ["from-generative", "sketch", "--preset", preset],
            ["offset", "design.pes"],
            ["convert", "design.dst"],
        )
        for argv in commands:
            with self.subTest(command=argv[0]):
                self.assertTrue(parser.parse_args(argv).strict_audit)
        self.assertFalse(
            parser.parse_args(
                ["from-svg", "design.svg", "--preset", preset,
                 "--no-strict-audit"]
            ).strict_audit
        )

    def test_finalize_propagates_audit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pes = Path(td) / "failed.pes"
            _write_two_block_pes(pes)
            with mock.patch.object(audit, "audit", return_value=({}, ["peak_1mm"])):
                # _finalize imports the module locally, so patching the shared
                # module object is enough.
                self.assertEqual(
                    main._finalize_pes(
                        pes, profile="mixed", strict_audit=True
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
