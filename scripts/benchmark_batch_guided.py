"""Synthetic, non-scientific Batch discovery-table interaction timings."""

from __future__ import annotations

import json
import platform
import statistics
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication

from dmri_studio.core.batch.discovery import Acquisition, Study
from dmri_studio.gui.batch import BatchPage
from dmri_studio.gui.theme import Theme


def elapsed(operation):
    start = time.perf_counter()
    operation()
    return round((time.perf_counter() - start) * 1000, 3)


def main():
    app = QApplication.instance() or QApplication([])
    theme = Theme(app)
    records = {}
    with TemporaryDirectory() as folder:
        for size in (10, 100, 1000):
            samples = []
            for _ in range(3):
                page = BatchPage(theme)
                study = Study(
                    f"synthetic-{size}",
                    str(Path(folder)),
                    tuple(
                        Acquisition(f"synthetic-{i:04}", paths={"tensor": f"missing-{i}.nii"})
                        for i in range(size)
                    ),
                )
                page.kind = "discover"
                initial = elapsed(lambda page=page, study=study: page.result(0, study, ""))
                guided = page.guided
                search = elapsed(
                    lambda guided=guided: guided.subject_search.setText("synthetic-0001")
                )
                select = elapsed(guided.deselect_all)
                return_ms = elapsed(
                    lambda guided=guided: (guided.set_stage(2), guided.set_stage(1))
                )
                samples.append(
                    dict(
                        initial_ms=initial,
                        search_ms=search,
                        selection_ms=select,
                        return_ms=return_ms,
                    )
                )
                page.deleteLater()
            records[size] = {
                key: {
                    "median": round(statistics.median(s[key] for s in samples), 3),
                    "range": [min(s[key] for s in samples), max(s[key] for s in samples)],
                }
                for key in samples[0]
            }
    print(
        json.dumps({"platform": platform.platform(), "qt": app.platformName(), "records": records})
    )


if __name__ == "__main__":
    main()
