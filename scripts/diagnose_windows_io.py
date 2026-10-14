"""Isolate native gzip Unicode I/O in disposable child processes, not MRI data.

Used only by Windows CI to locate native-extension exits that bypass pytest.
Every child creates a tiny synthetic NIfTI and prints checkpoints with flushing.
No installed package or source data is changed; diagnostics do not replace tests.
"""

import subprocess
import sys
import textwrap

CODE = textwrap.dedent("""
import faulthandler, pathlib, tempfile
import nibabel as nib
import numpy as np
faulthandler.enable()
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory) / NAME
    print("checkpoint: save", str(path), flush=True)
    nib.save(nib.Nifti1Image(np.ones((4, 5, 6), np.float32), np.eye(4)), path)
    print("checkpoint: load; indexed_gzip=", nib.openers.HAVE_INDEXED_GZIP, flush=True)
    if DISABLE:
        # Diagnostic comparison only, scoped to this disposable child process.
        nib.openers.HAVE_INDEXED_GZIP = False
    image = nib.load(path)
    print("checkpoint: materialize", flush=True)
    assert np.asarray(image.dataobj).sum() == 120
    print("checkpoint: complete", flush=True)
""")

for name, disable in [("ascii.nii.gz", False), ("MRI β.nii.gz", False), ("MRI β.nii.gz", True)]:
    print(f"Diagnostic: {name!r}, disable_indexed_gzip={disable}", flush=True)
    code = CODE.replace("NAME", repr(name)).replace("DISABLE", repr(disable))
    child = subprocess.run([sys.executable, "-u", "-c", code], timeout=30)
    print(f"Diagnostic return code: {child.returncode}", flush=True)
