"""Bounded-memory index stress test, not evidence of biological tract quality.

Replicates three vertices of one REAL reference fiber ten million times in an
exclusive temporary TCK. This artificial repetition tests record count/indexing
only. It does not substitute for the separate real, unique-bundle GUI tests.
The verified disposable stress file is removed after the test; metrics remain.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from dmri_studio.core.resources import peak_self_rss_bytes
from dmri_studio.core.scene import TractGroup


def main():
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=False)
    source = (
        Path(__file__).resolve().parents[2]
        / "data/viewer-reference/tractoinferno-sub-1006/derivatives/testset/sub-1006/tractography/sub-1006__AF_L.tck"
    )
    line = TractGroup.load(source).line(0)[:3]
    count = 10_000_000
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tract-index-stress-") as folder:
        path = Path(folder) / "repeated-real-vertices.tck"
        header = f"mrtrix tracks\ncount: {count}\ndatatype: Float32LE\nfile: . 128\nEND\n".encode()
        block = np.tile(np.vstack((line, [np.nan] * 3)).astype("<f4"), (10000, 1))
        with path.open("xb") as f:
            f.write(header + b"\x00" * (128 - len(header)))
            for _ in range(count // 10000):
                f.write(block.tobytes())
            f.write(np.full((1, 3), np.inf, dtype="<f4").tobytes())
        size = path.stat().st_size
        t = time.monotonic()
        g = TractGroup.load(path)
        index_seconds = time.monotonic() - t
        assert g.total == count and len(g.streamlines) == count
        for i in [0, 12345, 9999999]:
            assert np.array_equal(g.line(i), line)
        assert len(g.display_ids(1000)) == 1000
        stats = g.statistics()
        assert not stats["sampled"]
        result = {
            "source": str(source),
            "purpose": "synthetic record-count stress using repeated real vertices; not scientific validation",
            "count": count,
            "temporary_bytes": size,
            "index_seconds": index_seconds,
            "elapsed_seconds": time.monotonic() - started,
            "peak_rss_bytes": peak_self_rss_bytes(),
            "statistics": stats,
            "disposable_file_removed_after_test": True,
        }
        del g
    (output / "benchmark.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
