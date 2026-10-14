"""Check installed CLI/core isolation and packaged assets; no GUI or tool execution."""

import importlib.metadata
import json
import sys
from pathlib import Path

import dmri_studio
from dmri_studio.core.dependencies import inspect_dependencies

package = Path(dmri_studio.__file__).resolve().parent
# Wheel installation must not resolve imports to the checkout's src tree.
assert "site-packages" in package.parts, package
assert (package / "gui/assets/icons/brain.svg").is_file()
assets = [str(p) for p in (package / "gui/assets/fonts").glob("*.ttf")]
assert assets
report = inspect_dependencies(environ={"PATH": ""}, search_common=False)
assert report["features"]["viewer"]["state"] == "available"
assert "PySide6" not in sys.modules and "vtk" not in sys.modules
print(
    json.dumps(
        {
            "version": importlib.metadata.version("dmri-studio"),
            "installed_package": str(package),
            "font_files": len(assets),
            "core_is_Qt_free": True,
        },
        indent=2,
    )
)
