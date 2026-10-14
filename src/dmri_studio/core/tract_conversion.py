"""Optional official DSI Studio conversion, isolated from immutable input files.

Uses the documented --action=exp TT→TRK CLI; never runs reconstruction/tracking.
DSI may create source-side indexes, so only a private input copy is passed to it.
"""

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def convert_tinytrack(source, destination, executable):
    """Create an exclusive auditable conversion directory and return a TRK path."""
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    executable = Path(executable).resolve()
    if not source.name.lower().endswith(".tt.gz"):
        raise ValueError("Expected .tt.gz input")
    if not executable.is_file():
        raise ValueError("Choose the installed DSI Studio executable")
    destination.mkdir(parents=True, exist_ok=False)
    before = sha256(source)
    isolated = destination / "input.tt.gz"
    shutil.copyfile(source, isolated)
    compressed = destination / "converted.trk.gz"
    output = destination / "converted.trk"
    command = [
        str(executable),
        "--action=exp",
        "--source=" + str(isolated),
        "--output=" + str(compressed),
    ]
    record = {
        "source": str(source),
        "source_sha256": before,
        "executable": str(executable),
        "executable_sha256": sha256(executable),
        "command": command,
        "mechanism": "local CLI",
        "reference": "https://dsi-studio.labsolver.org/doc/cli_exp.html",
    }
    started = time.monotonic()
    try:
        with (destination / "conversion.log").open("xb") as log:
            result = subprocess.run(
                command,
                cwd=destination,
                env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=600,
                check=False,
            )
        record["exit_code"] = result.returncode
        if result.returncode or not compressed.is_file():
            raise ValueError("DSI conversion failed; see " + str(destination / "conversion.log"))
        with gzip.open(compressed, "rb") as stream, output.open("xb") as out:
            shutil.copyfileobj(stream, out)
        from .tract_index import IndexedStreamlines

        index = IndexedStreamlines(output)
        record.update(
            output=str(output),
            output_sha256=sha256(output),
            count=len(index),
            source_unchanged=sha256(source) == before,
        )
        if not record["source_unchanged"]:
            raise ValueError("Source changed during conversion")
        return output
    except Exception as exc:
        record["error"] = str(exc)
        raise
    finally:
        record["elapsed_seconds"] = time.monotonic() - started
        (destination / "conversion.json").write_text(json.dumps(record, indent=2))
