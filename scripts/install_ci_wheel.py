"""Install the sole newly built alpha wheel using portable argv, never a shell."""

import subprocess
import sys
from pathlib import Path

wheels = list(Path("dist").glob("dmri_studio-*.whl"))
if len(wheels) != 1:
    raise RuntimeError("Expected exactly one freshly built TractoSphere wheel")
subprocess.run(
    [sys.executable, "-m", "pip", "install", f"{wheels[0].resolve()}[gui-dev]"], check=True
)
