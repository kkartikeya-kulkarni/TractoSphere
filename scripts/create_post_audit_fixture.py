"""Create harmless diffusion-shaped files for manual GUI usability checks.

These constant-valued images are *not* suitable for scientific processing or
anatomical acceptance. A fresh temporary directory prevents source overwrite.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import nibabel as nib
import numpy as np


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="tractosphere-usability-"))
    affine = np.diag([2.0, 2.0, 2.0, 1.0])
    dwi = nib.Nifti1Image(np.ones((8, 8, 8, 7), dtype=np.float32), affine)
    t1 = nib.Nifti1Image(np.ones((8, 8, 8), dtype=np.float32), affine)
    for image in (dwi, t1):
        image.set_qform(affine, 1)
        image.set_sform(affine, 1)
        image.header.set_xyzt_units("mm")
    gradients = (
        "0 1000 1000 1000 1000 1000 1000\n",
        "0 1 0 0 -1 0 0\n0 0 1 0 0 -1 0\n0 0 0 1 0 0 -1\n",
    )
    bids = root / "bids"
    bids.mkdir()
    (bids / "dataset_description.json").write_text(
        json.dumps({"Name": "Synthetic interface check", "BIDSVersion": "1.9.0"}) + "\n"
    )
    for subject in ("01", "02"):
        folder = bids / f"sub-{subject}" / "dwi"
        folder.mkdir(parents=True)
        stem = folder / f"sub-{subject}_dwi"
        nib.save(dwi, str(stem) + ".nii.gz")
        (folder / f"sub-{subject}_dwi.bval").write_text(gradients[0])
        (folder / f"sub-{subject}_dwi.bvec").write_text(gradients[1])
        (folder / f"sub-{subject}_dwi.json").write_text(
            json.dumps({"PhaseEncodingDirection": "j", "TotalReadoutTime": 0.05}) + "\n"
        )
    custom = root / "custom"
    for subject in ("01", "02"):
        folder = custom / f"person-{subject}" / f"scan-{subject}"
        folder.mkdir(parents=True)
        nib.save(dwi, folder / "dwi.nii.gz")
        (folder / "dwi.bval").write_text(gradients[0])
        (folder / "dwi.bvec").write_text(gradients[1])
    anatomy = root / "T1.nii.gz"
    nib.save(t1, anatomy)
    response = root / "wm-response.txt"
    response.write_text("# Shells: 1000\n1.0 0.2\n")
    print(
        json.dumps(
            {
                "root": str(root),
                "bids": str(bids),
                "custom": str(custom),
                "anatomy": str(anatomy),
                "response": str(response),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
