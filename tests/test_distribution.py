"""Distribution contracts, runnable against a wheel outside the checkout."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lxml import etree
from PIL import Image, ImageDraw

from stitch_cli import main, materials, tuning, workspace


class DistributionTests(unittest.TestCase):
    def test_all_shipped_presets_resolve(self):
        directory = workspace.template_dir("materials")
        names = materials.list_preset_names(directory)
        self.assertIn("hat-twill-satin", names)
        for name in names:
            self.assertEqual(materials.resolve_preset(name, directory).name, name)

    def test_initialize_and_tune_each_recipe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            files = workspace.initialize(root)
            self.assertEqual(len(files), 7)
            for filename, preset in [("satin-bar", "hat-twill-satin"),
                                     ("fill-ring", "hat-twill-fill"),
                                     ("bean-loop", "hat-twill-bean")]:
                source = root / "designs/recipes" / f"{filename}.svg"
                result = tuning.tune_svg(source, root / "out" / f"{filename}.svg",
                                         materials.resolve_preset(preset, root / "materials"))
                self.assertIsNotNone(etree.parse(str(result)).getroot())

    def test_init_refuses_to_overwrite_customizations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace.initialize(root)
            presets = root / "materials/presets.toml"
            presets.write_text("# my custom presets\n")
            with self.assertRaises(FileExistsError):
                workspace.initialize(root)
            self.assertEqual(presets.read_text(), "# my custom presets\n")

    def test_inventory_override_is_read_at_call_time(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace.initialize(root)
            with patch.dict(os.environ, {"STITCH_MATERIALS_DIR": str(root / "materials")}):
                self.assertEqual(materials.default_materials_dir().resolve(), (root / "materials").resolve())
                self.assertEqual(materials.resolve_preset("hat-twill-bean").bean_repeats, 1)
            with patch.dict(os.environ, {"STITCH_MATERIALS_DIR": str(root / "missing")}):
                with self.assertRaises(FileNotFoundError):
                    materials.load_threads()

    def test_current_directory_inventory_has_priority(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace.initialize(root)
            with patch.dict(os.environ, {}, clear=True), patch.object(Path, "cwd", return_value=root):
                self.assertEqual(materials.default_materials_dir().resolve(), (root / "materials").resolve())

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "requires trace extra")
    def test_trace_raster_to_valid_svg(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            artwork = Image.new("RGB", (160, 80), "white")
            ImageDraw.Draw(artwork).line([(20, 40), (140, 40)], fill="black", width=10)
            artwork.save(root / "line.png")
            with redirect_stdout(StringIO()):
                result = main.main(["trace", str(root / "line.png"), "--width-mm", "30",
                                    "--work-width", "320", "-o", str(root / "line.svg")])
            self.assertEqual(result, 0)
            svg = etree.parse(str(root / "line.svg"))
            self.assertTrue(svg.xpath("//*[local-name()='path']"))

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "requires trace extra")
    def test_empty_trace_returns_useful_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            Image.new("RGB", (80, 80), "white").save(root / "empty.png")
            error = StringIO()
            with redirect_stderr(error):
                result = main.main(["trace", str(root / "empty.png"), "--width-mm", "30",
                                    "--work-width", "80", "-o", str(root / "empty.svg")])
            self.assertEqual(result, 1)
            self.assertIn("no dark foreground", error.getvalue())
            self.assertFalse((root / "empty.svg").exists())


if __name__ == "__main__":
    unittest.main()
