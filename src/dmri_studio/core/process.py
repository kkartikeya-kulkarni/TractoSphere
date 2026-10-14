"""Cancellable argv execution with local logs, never through a shell.

Use from processing adapters in a worker, not from the GUI event thread. A
dedicated pipe reader lets cancellation work even when a tool emits no output.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path


class ProcessingCancelled(RuntimeError):
    """The user cancelled; partially written outputs are never accepted."""


class ToolProcessError(RuntimeError):
    """A nonzero child exit with its exact timing, log and return code retained."""

    def __init__(self, message: str, record: dict) -> None:
        """Keep a structured failure record for the provenance manifest."""
        super().__init__(message)
        self.record = record


def check_cancel(cancelled: Callable[[], bool]) -> None:
    """Use one cancellation contract during subprocesses and integrity scans."""
    if cancelled():
        raise ProcessingCancelled(
            "Processing cancelled; incomplete outputs retained for diagnosis."
        )


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    log: Path,
    environment: Mapping[str, str],
    cancelled: Callable[[], bool],
    progress: Callable[[str], None],
) -> dict:
    """Stream merged output, terminate on cancellation, and always reap the child.

    POSIX cancellation terminates the process group. Windows currently terminates
    the direct executable (these bounded adapters do not launch child pipelines).
    No overwrite option, shell interpolation or pre-existing log is permitted.
    """
    from .storage import require_local_processing

    # Defense in depth for adapters called directly through Python, as well as
    # GUI/CLI plan preparation. Wrapper-owned scratch and all absolute arguments
    # must be local. Cloud download/copy helpers do not invoke this MRI runner.
    arguments = [str(value).split("=", 1)[-1] for value in argv]
    require_local_processing(
        [cwd, log, *(value for value in arguments if Path(value).is_absolute())]
    )
    check_cancel(cancelled)
    messages: queue.Queue = queue.Queue(maxsize=32)
    start = time.monotonic()
    samples = []
    with (
        log.open("xb") as handle,
        log.with_suffix(".resources.jsonl").open("x", encoding="utf-8") as resource_log,
    ):
        child = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )

        def read_pipe() -> None:
            """Bound buffering while preserving every byte in the disk log."""
            try:
                while chunk := child.stdout.read1(8192):
                    messages.put(chunk)
            finally:
                messages.put(None)

        reader = threading.Thread(target=read_pipe, name="dmri-tool-output", daemon=True)
        reader.start()
        stopped_at = None
        done = False
        last_sample = 0.0
        last_update = 0.0
        last_text = ""
        try:
            while not done or child.poll() is None:
                if time.monotonic() - last_sample >= 2 and child.poll() is None:
                    last_sample = time.monotonic()
                    sample = resource_snapshot(child.pid)
                    if sample is not None:
                        sample["elapsed_seconds"] = time.monotonic() - start
                        resource_log.write(json.dumps(sample) + "\n")
                        resource_log.flush()
                        samples.append(sample)
                        progress(
                            f"Resource sample · PID {child.pid} · CPU {sample['cpu_percent']}% · RSS {sample['rss_bytes'] / 1024**2:.1f} MiB · elapsed {sample['elapsed_seconds']:.0f}s (direct process)"
                        )
                if cancelled() and stopped_at is None:
                    stopped_at = time.monotonic()
                    _stop(child, signal.SIGTERM)
                if stopped_at is not None and time.monotonic() - stopped_at > 3:
                    _stop(child, getattr(signal, "SIGKILL", -1))
                try:
                    chunk = messages.get(timeout=0.1)
                except queue.Empty:
                    continue
                if chunk is None:
                    done = True
                else:
                    handle.write(chunk)
                    handle.flush()
                    # Progress is actual tool text; do not manufacture a total ETA.
                    last_text = chunk.decode("utf-8", errors="replace").strip()
                    if time.monotonic() - last_update >= 0.2:
                        progress(last_text[-2000:])
                        last_update = time.monotonic()
            child.wait()
            if last_text:
                progress(last_text[-2000:])
        finally:
            if child.poll() is None:
                _stop(child, getattr(signal, "SIGKILL", -1))
                child.wait()
            # Drain the queue before joining: the bounded reader may be waiting
            # to publish its last bytes after cancellation or a callback failure.
            while reader.is_alive() or not messages.empty():
                try:
                    chunk = messages.get(timeout=0.1)
                    if chunk is not None:
                        handle.write(chunk)
                except queue.Empty:
                    pass
            reader.join()
            child.stdout.close()
        record = {
            "argv": list(argv),
            "return_code": child.returncode,
            "elapsed_seconds": time.monotonic() - start,
            "log": str(log),
            "resource_log": str(log.with_suffix(".resources.jsonl")),
            "resource_sample_count": len(samples),
            "sampled_peak_direct_rss_bytes": max((s["rss_bytes"] for s in samples), default=None),
            "resource_scope": "Direct process sampled every ~2s; excludes descendants and may miss brief peaks. POSIX CPU follows ps semantics; Windows CPU is a lifetime average from psutil.",
        }
        if cancelled():
            error = ProcessingCancelled(
                "Processing cancelled; incomplete outputs retained for diagnosis."
            )
            error.record = record
            raise error
        if child.returncode:
            raise ToolProcessError(
                f"{Path(argv[0]).name} exited {child.returncode}; see {log}", record
            )
        return record


def _stop(child: subprocess.Popen, requested_signal: int) -> None:
    """Ignore a child that exited between polling and signal delivery."""
    if child.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(child.pid, requested_signal)
        elif requested_signal == signal.SIGTERM:
            child.terminate()
        else:
            child.kill()
    except ProcessLookupError:
        pass


def resource_snapshot(pid: int) -> dict | None:
    """Read process metrics without signals or debugger attachment."""
    if os.name != "posix":
        from .resources import windows_process_snapshot

        return windows_process_snapshot(pid)
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu=,rss="],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=True,
        )
        cpu, rss = completed.stdout.split()
        return {"pid": pid, "cpu_percent": float(cpu), "rss_bytes": int(rss) * 1024}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
