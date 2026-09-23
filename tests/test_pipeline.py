from __future__ import annotations

import re
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import pyembroidery
from lxml import etree
from PIL import Image, ImageDraw

from stitch_cli import (
    artifacts,
    audit,
    compiled,
    convert,
    deploy,
    fillsatin,
    inkstitch,
    lettering,
    main,
    preflight,
    processing,
    tuning,
    visualqa,
)
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

    def test_long_sewn_float_is_counted_but_jump_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pes = Path(td) / "float.pes"
            pattern = pyembroidery.EmbPattern()
            pattern.add_stitch_absolute(pyembroidery.STITCH, 0, 0)
            pattern.add_stitch_absolute(pyembroidery.STITCH, 61, 0)
            pattern.add_stitch_absolute(pyembroidery.JUMP, 200, 0)
            pattern.add_stitch_absolute(pyembroidery.STITCH, 200, 0)
            pattern.add_command(pyembroidery.END)
            pyembroidery.write_pes(pattern, str(pes), {"version": "6"})
            self.assertEqual(audit.measure(pes)["long_stitch_over_5_count"], 1)


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
            self.assertEqual(
                path.get(f"{{{tuning.INKSTITCH_NS}}}center_walk_underlay_repeats"),
                "2",
            )

    def test_safe_per_path_satin_max_stitch_override_is_honored(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg"
            xmlns:inkstitch="http://inkstitch.org/namespace" viewBox="0 0 20 20">
          <path id="wide" style="fill:none;stroke:#000000"
            data-satin-max-stitch-mm="7.0" inkstitch:satin_column="true"
            d="M 1 1 L 1 10 M 7 1 L 7 10 M 1 5 L 7 5"/>
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
                path.get(f"{{{tuning.INKSTITCH_NS}}}max_stitch_length_mm"),
                "7.0",
            )

    def test_per_path_staggered_satin_split_is_honored(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg"
            xmlns:inkstitch="http://inkstitch.org/namespace" viewBox="0 0 20 20">
          <path id="wide" style="fill:none;stroke:#000000"
            data-satin-split-method="staggered"
            data-satin-split-staggers="4" inkstitch:satin_column="true"
            data-satin-short-stitch-mm="0.5"
            data-satin-short-stitch-inset="25"
            d="M 1 1 L 1 10 M 7 1 L 7 10 M 1 5 L 7 5"/>
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
                path.get(f"{{{tuning.INKSTITCH_NS}}}split_method"),
                "staggered",
            )
            self.assertEqual(
                path.get(f"{{{tuning.INKSTITCH_NS}}}staggers"),
                "4",
            )
            # 0.5 is above the 0.4 zigzag spacing, so short-stitch would fire
            # on every stitch and alternate the rails in and out instead of
            # thinning tight inner curves. Tuning clamps it clear of spacing.
            self.assertEqual(
                path.get(f"{{{tuning.INKSTITCH_NS}}}short_stitch_distance_mm"),
                "0.24",
            )
            self.assertEqual(
                path.get(f"{{{tuning.INKSTITCH_NS}}}short_stitch_inset"),
                "25",
            )

    def test_short_stitch_below_spacing_is_passed_through(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg"
            xmlns:inkstitch="http://inkstitch.org/namespace" viewBox="0 0 20 20">
          <path id="wide" style="fill:none;stroke:#000000"
            inkstitch:satin_column="true"
            data-satin-spacing-mm="0.40"
            data-satin-short-stitch-mm="0.2"
            d="M 1 1 L 1 10 M 7 1 L 7 10 M 1 5 L 7 5"/>
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
                path.get(f"{{{tuning.INKSTITCH_NS}}}short_stitch_distance_mm"),
                "0.2",
            )

    def test_buried_trim_can_suppress_redundant_lock_stitch(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">
          <path id="spoke" style="fill:none;stroke:#000000"
            data-stroke-method="bean_stitch" data-force-lock="false"
            d="M 1 1 L 10 10"/>
        </svg>'''
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.svg"
            output = Path(td) / "output.svg"
            source.write_bytes(svg)
            tuning.tune_svg(source, output, _preset())
            root = etree.parse(str(output)).getroot()
            path = root.xpath(
                ".//svg:path[@id='spoke']", namespaces={"svg": tuning.SVG_NS}
            )[0]
            self.assertEqual(
                path.get(f"{{{tuning.INKSTITCH_NS}}}trim_after"), "true"
            )
            self.assertEqual(
                path.get(f"{{{tuning.INKSTITCH_NS}}}force_lock_stitches"), "false"
            )

    def test_per_path_running_stitch_length_override_is_honored(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">
          <path id="curve" style="fill:none;stroke:#000000"
            data-stroke-method="bean_stitch" data-smoothed="true"
            data-running-stitch-length-mm="0.9"
            d="M 1 10 C 5 1 15 1 19 10"/>
        </svg>'''
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.svg"
            output = Path(td) / "output.svg"
            source.write_bytes(svg)
            tuning.tune_svg(source, output, _preset())
            root = etree.parse(str(output)).getroot()
            path = root.xpath(
                ".//svg:path[@id='curve']", namespaces={"svg": tuning.SVG_NS}
            )[0]
            self.assertEqual(
                path.get(f"{{{tuning.INKSTITCH_NS}}}running_stitch_length_mm"),
                "0.9",
            )


class RailSmoothingTests(unittest.TestCase):
    """fillsatin.smooth_satin_rails: damp trace jitter, keep the shape."""

    NS = {"svg": fillsatin.SVG_NS}

    def _column(self, rail_a: str, rail_b: str, rung: str = "M 0 0 L 0 4") -> etree._Element:
        svg = f'''<svg xmlns="{fillsatin.SVG_NS}"
            xmlns:inkstitch="{fillsatin.INKSTITCH_NS}" viewBox="0 0 40 40">
          <path id="col" inkstitch:satin_column="true"
            d="{rail_a} {rail_b} {rung}"/>
        </svg>'''.encode()
        return etree.fromstring(svg)

    @staticmethod
    def _straight(y: float, n: int = 20, jitter: float = 0.0) -> str:
        pts = []
        for i in range(n):
            wobble = jitter if i % 2 else -jitter
            pts.append(f"{i * 0.4:.3f} {y + wobble:.3f}")
        return "M " + " L ".join(pts)

    def test_jitter_is_damped_but_the_line_stays_put(self) -> None:
        root = self._column(self._straight(0.0, jitter=0.25),
                            self._straight(4.0, jitter=0.25))
        self.assertEqual(fillsatin.smooth_satin_rails(root), 1)
        path = root.xpath(".//svg:path[@id='col']", namespaces=self.NS)[0]
        rails = re.findall(r"[Mm][^Mm]*", path.get("d"))
        self.assertEqual(len(rails), 3)          # rung survives untouched
        ys = [p[1] for p in fillsatin._flatten_subpath(rails[0])]
        # Interior wobble collapses toward the true line; caps are pinned.
        self.assertLess(max(abs(y) for y in ys[1:-1]), 0.25)
        self.assertAlmostEqual(ys[0], -0.25, places=3)


    def test_rungs_are_refitted_onto_the_smoothed_rails(self) -> None:
        rails = [[(0.0, 0.0), (10.0, 0.0)], [(0.0, 4.0), (10.0, 4.0)]]
        refitted = fillsatin._refit_rung([(5.0, 1.0), (5.0, 3.0)], rails)
        self.assertAlmostEqual(refitted[0][1], -0.30, places=6)
        self.assertAlmostEqual(refitted[1][1], 4.30, places=6)

    def test_a_rung_parallel_to_the_rails_is_left_alone(self) -> None:
        rails = [[(0.0, 0.0), (10.0, 0.0)], [(0.0, 4.0), (10.0, 4.0)]]
        self.assertIsNone(fillsatin._refit_rung([(1.0, 2.0), (6.0, 2.0)], rails))
        self.assertIsNone(fillsatin._refit_rung([(5.0, 1.0), (5.0, 1.0)], rails))

    def test_smoothing_keeps_every_rung_spanning_both_rails(self) -> None:
        root = self._column(self._straight(0.0, jitter=0.25),
                            self._straight(4.0, jitter=0.25),
                            rung="M 3.6 1.0 L 3.6 3.0")
        self.assertEqual(fillsatin.smooth_satin_rails(root), 1)
        path = root.xpath(".//svg:path[@id='col']", namespaces=self.NS)[0]
        subpaths = re.findall(r"[Mm][^Mm]*", path.get("d"))
        rung = fillsatin._flatten_subpath(subpaths[2])
        self.assertLess(min(p[1] for p in rung), 0.0)
        self.assertGreater(max(p[1] for p in rung), 4.0)

    def test_a_column_with_no_rails_is_left_alone(self) -> None:
        root = self._column("M 0 0 L 8 0", "", rung="")
        self.assertEqual(fillsatin.smooth_satin_rails(root), 0)

    def test_non_satin_paths_are_ignored(self) -> None:
        svg = f'''<svg xmlns="{fillsatin.SVG_NS}" viewBox="0 0 40 40">
          <path id="plain" d="M 0 0 L 1 1 M 2 2 L 3 3"/>
        </svg>'''.encode()
        root = etree.fromstring(svg)
        self.assertEqual(fillsatin.smooth_satin_rails(root), 0)
        self.assertEqual(root.xpath(".//svg:path[@id='plain']",
                                    namespaces=self.NS)[0].get("d"),
                         "M 0 0 L 1 1 M 2 2 L 3 3")


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

    def test_manifest_detects_artifact_and_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            source = directory / "design.svg"
            pes = directory / "design.pes"
            source.write_text("<svg/>")
            pes.write_bytes(b"machine bytes")
            manifest = directory / "design.manifest.json"
            artifacts.write_manifest(
                manifest,
                source=source,
                preset_name="test",
                audit_profile="mixed",
                metrics={},
                audit_failures=[],
                preflight={},
                compiled_objects=[],
                artifacts={"pes": pes},
            )
            self.assertEqual(artifacts.verify_manifest(manifest)[1], [])
            pes.write_bytes(b"changed machine bytes")
            failures = artifacts.verify_manifest(manifest)[1]
            self.assertTrue(any("pes:" in failure for failure in failures))
            pes.write_bytes(b"machine bytes")
            source.write_text("<svg data-new='true'/>")
            failures = artifacts.verify_manifest(manifest)[1]
            self.assertTrue(any("source:" in failure for failure in failures))

    def test_manifest_resolution_handles_compound_artifact_suffixes(self) -> None:
        directory = Path("out")
        expected = directory / "logo.manifest.json"
        for filename in (
            "logo.pes", "logo.dst", "logo.preview.png", "logo.preview.svg",
            "logo.tuned.svg", "logo.qa.png", "logo.manifest.json",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(
                    artifacts.manifest_for_artifact(directory / filename), expected
                )

    def test_visual_qa_records_durable_bundle_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            reference = directory / "reference.png"
            preview = directory / "logo.preview.png"
            output = directory / "logo.qa.png"
            for path in (reference, preview):
                image = Image.new("RGB", (20, 10), "black")
                ImageDraw.Draw(image).rectangle((3, 2, 16, 7), fill="white")
                image.save(path)
            report = visualqa.make_visual_qa(reference, preview, output)
            self.assertEqual(report["preview"], "logo.preview.png")
            self.assertEqual(report["output"], "logo.qa.png")
            self.assertFalse(report["preview"].startswith(str(directory)))

    def test_deploy_rejects_tampered_manifest_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            source_svg = directory / "source.svg"
            source_svg.write_text("<svg/>")
            source = directory / "valid_name.pes"
            device = directory / "usb"
            device.mkdir()
            _write_two_block_pes(source)
            artifacts.write_manifest(
                directory / "valid_name.manifest.json",
                source=source_svg,
                preset_name="test",
                audit_profile="mixed",
                metrics={},
                audit_failures=[],
                preflight={},
                compiled_objects=[],
                artifacts={"pes": source},
            )
            with source.open("ab") as handle:
                handle.write(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "stale or incomplete"):
                deploy.deploy(source, device)


class StructuralQATests(unittest.TestCase):
    def _validate(self, body: str, *, root_attrs: str = "") -> preflight.Report:
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg"
            xmlns:inkstitch="http://inkstitch.org/namespace"
            width="20mm" viewBox="0 0 20 20" {root_attrs}>
            {body}
        </svg>'''
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "design.svg"
            path.write_text(svg)
            return preflight.validate_svg(path)

    def test_strict_satin_accepts_three_interior_direction_rungs(self) -> None:
        report = self._validate(
            '''<path id="column" inkstitch:satin_column="true"
                data-direction-rungs="3" style="fill:none;stroke:#000"
                d="M 2 2 L 2 18 M 5 2 L 5 18
                   M 1 6 L 6 6 M 1 10 L 6 10 M 1 14 L 6 14"/>''',
            root_attrs='data-require-explicit-rungs="true"',
        )
        self.assertEqual(report.errors, [])
        self.assertEqual(report.explicit_rungs, 3)

    def test_strict_satin_rejects_a_rung_on_the_cap(self) -> None:
        report = self._validate(
            '''<path id="column" inkstitch:satin_column="true"
                style="fill:none;stroke:#000"
                d="M 2 2 L 2 18 M 5 2 L 5 18
                   M 1 2 L 6 2 M 1 10 L 6 10 M 1 14 L 6 14"/>''',
            root_attrs='data-require-explicit-rungs="true"',
        )
        self.assertIn("rung_on_cap_endpoint", [issue.code for issue in report.errors])

    def test_strict_connector_validation_rejects_stale_endpoints(self) -> None:
        report = self._validate(
            '''<path id="first" style="fill:none;stroke:#000" d="M 1 1 L 3 1"/>
               <path id="travel-1" data-role="travel"
                 data-connector-tolerance-mm="0.2"
                 style="fill:none;stroke:#000" d="M 6 1 L 8 1"/>
               <path id="second" style="fill:none;stroke:#000" d="M 10 1 L 12 1"/>''',
            root_attrs='data-validate-connectors="true"',
        )
        self.assertIn("stale_connector", [issue.code for issue in report.errors])

    def test_compiled_bounds_assertion_catches_collapsed_object(self) -> None:
        assertion = compiled.Assertion(
            element_id="core", role="starburst-core",
            min_width_mm=4.5, min_height_mm=2.5,
        )
        passed = compiled.check_assertion(
            assertion, width_mm=5.2, height_mm=3.1, stitches=60
        )
        failed = compiled.check_assertion(
            assertion, width_mm=5.2, height_mm=0.4, stitches=12
        )
        self.assertEqual(passed.failures, ())
        self.assertTrue(any("height" in message for message in failed.failures))

    def test_preflight_failure_preserves_last_published_bundle(self) -> None:
        svg = b'''<svg xmlns="http://www.w3.org/2000/svg"
            xmlns:inkstitch="http://inkstitch.org/namespace"
            data-require-explicit-rungs="true" viewBox="0 0 20 20">
          <path id="bad" inkstitch:satin_column="true"
            style="fill:none;stroke:#000" d="M 1 1 L 1 10 M 3 1 L 3 10"/>
        </svg>'''
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            source = directory / "source.svg"
            output = directory / "out"
            output.mkdir()
            source.write_bytes(svg)
            old_pes = output / "logo.pes"
            old_manifest = output / "logo.manifest.json"
            old_pes.write_bytes(b"known good pes")
            old_manifest.write_bytes(b"known good manifest")
            result = main._digitize(source, output, "logo", _preset())
            self.assertEqual(result, 1)
            self.assertEqual(old_pes.read_bytes(), b"known good pes")
            self.assertEqual(old_manifest.read_bytes(), b"known good manifest")


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
            self.assertIn(
                "long_stitch_over_5_count", audit.PROFILE_BASE_KEYS[profile]
            )

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


class ProFillTechniqueTests(unittest.TestCase):
    """Pro-fill learnings (Zenbul ring / CHGL napkin, 2026-09): a satin
    border sewn over every fill edge, a preset-set underlay pitch, and a
    preset-set zig-zag underlay pitch on satin columns."""

    def _tune(self, svg: str, preset: ResolvedPreset):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.svg"
            src.write_text(svg)
            out = tuning.tune_svg(src, Path(tmp) / "out.svg", preset)
            return etree.parse(str(out)).getroot()

    def test_fill_border_walk_and_zigzag_follow_every_subpath(self) -> None:
        import dataclasses
        preset = dataclasses.replace(_preset(), fill_border_mm=1.8,
                                     fill_underlay_row_spacing_mm=1.2)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" '
               'viewBox="0 0 20 20"><path id="ring" style="fill:#111111;stroke:none" '
               'd="M 2 2 L 18 2 L 18 18 L 2 18 Z M 6 6 L 6 14 L 14 14 L 14 6 Z"/></svg>')
        root = self._tune(svg, preset)
        ns = tuning.INKSTITCH_NS
        paths = list(root.iter(f"{{{tuning.SVG_NS}}}path"))
        ids = [p.get("id") for p in paths]
        self.assertEqual(ids, ["ring", "ring-border-walk", "ring-border",
                               "ring-border-walk-1", "ring-border-1"])
        fill, walk, zig = paths[0], paths[1], paths[2]
        self.assertEqual(fill.get(f"{{{ns}}}fill_underlay_row_spacing_mm"), "1.20")
        self.assertEqual(walk.get(f"{{{ns}}}stroke_method"), "running_stitch")
        self.assertEqual(walk.get(f"{{{ns}}}trim_after"), "false")
        self.assertEqual(zig.get(f"{{{ns}}}stroke_method"), "zigzag_stitch")
        self.assertEqual(zig.get(f"{{{ns}}}zigzag_spacing_mm"), "0.40")
        self.assertIn("stroke-width:1.80", zig.get("style"))
        self.assertTrue(walk.get("d").startswith("M 2 2"))
        self.assertTrue(paths[3].get("d").startswith("M 6 6"))

    def test_fill_border_can_be_opted_out_per_path(self) -> None:
        import dataclasses
        preset = dataclasses.replace(_preset(), fill_border_mm=1.8)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" '
               'viewBox="0 0 20 20"><path id="disc" data-fill-border-mm="0" '
               'style="fill:#111111;stroke:none" d="M 2 2 L 18 2 L 18 18 L 2 18 Z"/></svg>')
        root = self._tune(svg, preset)
        self.assertEqual(len(list(root.iter(f"{{{tuning.SVG_NS}}}path"))), 1)

    def test_zigzag_underlay_pitch_comes_from_preset_or_path(self) -> None:
        import dataclasses
        preset = dataclasses.replace(_preset(), zigzag_underlay_spacing_mm=1.2)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" '
               'xmlns:inkstitch="http://inkstitch.org/namespace" width="20mm" '
               'height="20mm" viewBox="0 0 20 20">'
               '<path id="a" inkstitch:satin_column="true" style="fill:none;stroke:#111111" '
               'd="M 2 2 L 18 2 M 2 5 L 18 5"/>'
               '<path id="b" inkstitch:satin_column="true" data-zigzag-underlay-mm="2.5" '
               'style="fill:none;stroke:#111111" d="M 2 8 L 18 8 M 2 11 L 18 11"/></svg>')
        root = self._tune(svg, preset)
        ns = tuning.INKSTITCH_NS
        by_id = {p.get("id"): p for p in root.iter(f"{{{tuning.SVG_NS}}}path")}
        self.assertEqual(by_id["a"].get(f"{{{ns}}}zigzag_underlay_spacing_mm"), "1.20")
        self.assertEqual(by_id["b"].get(f"{{{ns}}}zigzag_underlay_spacing_mm"), "2.5")

    def test_pro_fill_preset_resolves(self) -> None:
        preset = resolve_preset("hat-twill-fill")
        self.assertEqual(preset.row_spacing_mm, 0.2)
        self.assertEqual(preset.fill_border_mm, 1.8)
        self.assertEqual(preset.fill_underlay_row_spacing_mm, 1.2)
        self.assertEqual(preset.zigzag_underlay_spacing_mm, 1.2)


class BaseRecipePresetTests(unittest.TestCase):
    """The three hat-twill base recipes (docs/RECIPES.md) resolve and drive
    the technique defaults in tuning."""

    def test_recipe_presets_resolve_with_pro_values(self) -> None:
        satin = resolve_preset("hat-twill-satin")
        self.assertEqual(satin.satin_spacing_mm, 0.40)
        self.assertEqual(satin.satin_max_stitch_mm, 5.0)
        self.assertEqual(satin.zigzag_underlay_spacing_mm, 1.2)
        fill = resolve_preset("hat-twill-fill")
        self.assertEqual((fill.row_spacing_mm, fill.max_stitch_length_mm), (0.2, 4.0))
        self.assertEqual((fill.fill_border_mm, fill.fill_underlay_row_spacing_mm), (1.8, 1.2))
        bean = resolve_preset("hat-twill-bean")
        self.assertEqual((bean.bean_repeats, bean.running_stitch_length_mm), (1, 1.25))
        for preset in (satin, fill, bean):
            self.assertEqual(preset.fabric.id, "twill-hat-front")

    def test_bean_preset_sets_triple_bean_and_pitch(self) -> None:
        import dataclasses
        import tempfile
        preset = dataclasses.replace(_preset(), bean_repeats=1, running_stitch_length_mm=1.25,
                                     satin_max_stitch_mm=5.0)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkstitch="http://inkstitch.org/namespace" '
               'width="20mm" height="20mm" viewBox="0 0 20 20">'
               '<path id="pen" data-stroke-method="bean_stitch" data-smoothed="true" '
               'style="fill:none;stroke:#111111" d="M 2 2 C 5 2 8 8 18 18"/>'
               '<path id="col" inkstitch:satin_column="true" style="fill:none;stroke:#111111" '
               'd="M 2 2 L 18 2 M 2 6 L 18 6"/></svg>')
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.svg"
            src.write_text(svg)
            root = etree.parse(str(tuning.tune_svg(src, Path(tmp) / "out.svg", preset))).getroot()
        ns = tuning.INKSTITCH_NS
        by_id = {p.get("id"): p for p in root.iter(f"{{{tuning.SVG_NS}}}path")}
        self.assertEqual(by_id["pen"].get(f"{{{ns}}}bean_stitch_repeats"), "1")
        self.assertEqual(by_id["pen"].get(f"{{{ns}}}running_stitch_length_mm"), "1.25")
        self.assertEqual(by_id["col"].get(f"{{{ns}}}max_stitch_length_mm"), "5.0")
