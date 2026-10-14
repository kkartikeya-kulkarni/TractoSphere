"""Read-only resource measurements with explicit platform semantics."""

from __future__ import annotations

import sys

import psutil


def windows_process_snapshot(pid: int) -> dict | None:
    """Return direct-process RSS and lifetime-average CPU; never attach/signal.

    A fresh Process.cpu_percent() reports an uninitialized zero, so use CPU time
    divided by process age instead. This average is not instantaneous activity.
    """
    import time

    try:
        process = psutil.Process(pid)
        with process.oneshot():
            times = process.cpu_times()
            age = max(time.time() - process.create_time(), 0.001)
            return {
                "pid": pid,
                "cpu_percent": 100 * (times.user + times.system) / age,
                "rss_bytes": process.memory_info().rss,
                "cpu_semantics": "Lifetime-average direct-process CPU; 100% is one logical CPU",
                "measurement_backend": "psutil",
            }
    except (psutil.Error, OSError):
        return None


def peak_self_rss_bytes() -> int | None:
    """Return OS high-water RSS bytes, or None when the OS does not provide it.

    Linux/BSD ru_maxrss is KiB; macOS reports bytes. Windows exposes peak_wset.
    This is an OS peak measurement, distinct from sampled process-runner peaks.
    """
    if sys.platform == "win32":
        try:
            return getattr(psutil.Process().memory_info(), "peak_wset", None)
        except psutil.Error:
            return None
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)
