"""Create editable materials and example designs from the installed package."""
from pathlib import Path
import shutil

from .materials import MATERIALS_DIR


def template_dir(name: str) -> Path:
    packaged = Path(__file__).resolve().parent / "data" / name
    if packaged.is_dir():
        return packaged
    root = MATERIALS_DIR.parent
    return root / ("materials" if name == "materials" else "designs/recipes")


def initialize(destination: Path) -> list[Path]:
    sources = [(template_dir("materials"), destination / "materials"),
               (template_dir("recipes"), destination / "designs/recipes")]
    copies = []
    for source, target in sources:
        if not source.is_dir():
            raise FileNotFoundError(f"Missing bundled templates: {source}")
        copies.extend((path, target / path.name) for path in sorted(source.iterdir())
                      if path.is_file())
    conflicts = [str(target) for _, target in copies if target.exists()]
    if conflicts:
        raise FileExistsError("Refusing to overwrite existing files: " + ", ".join(conflicts))
    for source, target in copies:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also protects against another process creating a file.
        with target.open("xb") as output, source.open("rb") as original:
            shutil.copyfileobj(original, output)
    (destination / "out").mkdir(parents=True, exist_ok=True)
    return [target for _, target in copies]
