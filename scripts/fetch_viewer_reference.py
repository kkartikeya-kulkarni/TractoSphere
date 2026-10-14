"""Download one CC0 TractoInferno subject, verify hashes and convert real bundles.

Usage: python scripts/fetch_viewer_reference.py NEW_DIRECTORY
No tractography reconstruction, scientific correction or synthetic fibers.
"""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import nibabel as nib
import numpy as np

REPOSITORY = "https://raw.githubusercontent.com/OpenNeuroDatasets/ds003900/"
S3 = "https://s3.amazonaws.com/openneuro.org/ds003900/"
PREFIX = "derivatives/testset/sub-1006/"


def main():
    """Use a pinned Git tree for associations; validate every received object."""
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    with urllib.request.urlopen(
        "https://api.github.com/repos/OpenNeuroDatasets/ds003900/git/trees/main?recursive=1"
    ) as r:
        tree = json.load(r)
    with urllib.request.urlopen(REPOSITORY + tree["sha"] + "/dataset_description.json") as r:
        description = json.load(r)
    if description.get("License") != "CC0":
        raise ValueError("Dataset terms changed; review before downloading")
    paths = ["README", "dataset_description.json", "CHANGES"]
    paths += [
        x["path"]
        for x in tree["tree"]
        if x["type"] == "blob"
        and x["path"].startswith(PREFIX)
        and any("/" + a + "/" in x["path"] for a in ["anat", "dti", "mask", "dwi", "tractography"])
    ]
    records = []
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(S3 + path) as response, target.open("xb") as output:
            sha = hashlib.sha256()
            md5 = hashlib.md5()
            size = 0
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                sha.update(chunk)
                md5.update(chunk)
                size += len(chunk)
            assert size == int(response.headers["Content-Length"])
            etag = response.headers["ETag"].strip('"')
            if "-" in etag:
                raise ValueError("Multipart ETag: require a separate trusted source checksum")
            assert md5.hexdigest() == etag
        records.append(
            {
                "path": path,
                "url": S3 + path,
                "bytes": size,
                "sha256": sha.hexdigest(),
                "etag": etag,
                "verified": True,
            }
        )
        (root / "download-manifest.json").write_text(
            json.dumps(
                {"repository_tree": tree["sha"], "license": "CC0", "files": records}, indent=2
            )
        )
        print(path, size, flush=True)
    reference = nib.load(root / PREFIX / "anat/sub-1006__T1w.nii.gz")
    validation = []
    for p in (root / PREFIX / "tractography").glob("*.trk"):
        tract = nib.streamlines.load(p)
        points = tract.tractogram.streamlines._data
        assert np.isfinite(points).all()
        vox = nib.affines.apply_affine(np.linalg.inv(reference.affine), points)
        inside = np.all((vox >= -0.5) & (vox < np.array(reference.shape) - 0.5), axis=1)
        record = {
            "path": str(p.relative_to(root)),
            "streamlines": len(tract.tractogram.streamlines),
            "points": len(points),
            "inside_T1_fraction": float(inside.mean()),
        }
        if p.name.endswith(("AF_L.trk", "PYT_L.trk", "CC_Pr_Po.trk")):
            output = p.with_suffix(".tck")
            nib.streamlines.save(tract.tractogram, output)
            assert np.array_equal(nib.streamlines.load(output).tractogram.streamlines._data, points)
            record["tck_derivative"] = str(output.relative_to(root))
            record["coordinate_equality"] = "exact"
        validation.append(record)
    (root / "tract-verification.json").write_text(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()
