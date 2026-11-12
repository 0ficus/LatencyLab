"""One pinned target invocation via perf; no general-purpose perf interface."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

from .hotspots import Hotspot, parse_report

MODES = {
    "cpu": ("cpu-clock:u", ["-F", "99"]),
    "cache": ("cache-misses:u", ["-c", "1000"]),
}


class ProfileError(RuntimeError):
    pass


@dataclass
class ProfileResult:
    mode: str
    cpu: int
    hotspots: list[Hotspot]
    total_samples: int
    directory: Path
    diagnostics: str


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _run(argv: list[str], stdout: Path, stderr: Path, timeout: float,
         cancel: threading.Event) -> int:
    """Files avoid pipe deadlocks; always stop/reap the entire process group."""
    with stdout.open("wb") as out, stderr.open("wb") as err:
        proc = subprocess.Popen(argv, stdout=out, stderr=err, start_new_session=True,
                                env={**os.environ, "LC_ALL": "C", "PERF_PAGER": "cat"})
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if cancel.is_set():
                    raise ProfileError("Профилирование остановлено.")
                if time.monotonic() >= deadline:
                    raise ProfileError(f"Таймаут профилирования ({timeout:g} с).")
                cancel.wait(0.05)
            if cancel.is_set():
                raise ProfileError("Профилирование остановлено.")
            return proc.returncode
        finally:
            # A target can leave descendants even if perf already exited.
            _signal_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            _signal_group(proc, signal.SIGKILL)
            proc.wait()


def _detail(path: Path) -> str:
    with path.open("rb") as stream:
        return stream.read(6000).decode("utf-8", errors="replace").strip()


def profile(command: str, mode: str, cpu: int | None, results_dir: Path,
            timeout: float | None = 10, cancel: threading.Event | None = None) -> ProfileResult:
    if sys.platform != "linux":
        raise ProfileError("Участки кода доступны на Linux с установленным perf.")
    if mode not in MODES:
        raise ProfileError("Доступны только режимы CPU и cache misses.")
    timeout = 10 if timeout is None else timeout
    if not math.isfinite(timeout) or not 0.001 <= timeout <= 3600:
        raise ProfileError("Таймаут должен быть от 0.001 до 3600 секунд.")
    try:
        target = shlex.split(command)
    except ValueError as exc:
        raise ProfileError(f"Некорректные кавычки: {exc}") from exc
    if not target:
        raise ProfileError("Введите команду.")
    perf, taskset = shutil.which("perf"), shutil.which("taskset")
    if not perf or not taskset:
        raise ProfileError("Нужны perf и taskset (пакет util-linux). См. docs/hotspots.md.")
    allowed = os.sched_getaffinity(0)
    cpu = min(allowed) if cpu is None else cpu
    if cpu not in allowed:
        raise ProfileError(f"CPU {cpu} недоступен. Разрешены: {sorted(allowed)}")
    cancel = cancel if cancel is not None else threading.Event()
    if cancel.is_set():
        raise ProfileError("Профилирование остановлено.")

    directory = results_dir.resolve() / f"profile-{datetime.now():%Y-%m-%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    data = directory / "perf.data"
    diagnostics = directory / "record.stderr.txt"
    event, sampling = MODES[mode]
    record = [taskset, "--cpu-list", str(cpu), perf, "record", "--no-inherit",
              "-e", event, *sampling, "-o", str(data), "--", *target]
    report = [perf, "report", "--stdio", "--stdio-color=never", "--no-children",
              "--percent-limit", "0", "--full-source-path", "--sort", "symbol,srcline,dso",
              "--fields", "overhead,sample,symbol,srcline,dso", "--field-separator", "\t",
              "-i", str(data)]
    metadata = {"schema_version": 1, "kind": "hotspots", "command": command,
                "argv": target, "mode": mode, "event": event, "cpu": cpu,
                "scope": "target main thread, user space, includes startup",
                "record_argv": record, "report_argv": report, "status": "running"}
    try:
        code = _run(record, directory / "target.stdout.txt", diagnostics, timeout, cancel)
        if code:
            raise ProfileError(f"perf record / программа: код {code}.\n{_detail(diagnostics)}")
        report_path = directory / "report.txt"
        report_errors = directory / "report.stderr.txt"
        code = _run(report, report_path, report_errors, min(timeout, 30), cancel)
        if code:
            raise ProfileError(f"perf report: код {code}.\n{_detail(report_errors)}")
        hotspots, total = parse_report(report_path.read_text(encoding="utf-8", errors="replace"))
        warnings = "\n".join(filter(None, (_detail(diagnostics), _detail(report_errors))))
        metadata.update(status="ok", total_samples=total,
                        hotspots=[item.to_dict() for item in hotspots], diagnostics=warnings)
        return ProfileResult(mode, cpu, hotspots, total, directory, warnings)
    except (OSError, ValueError, ProfileError) as exc:
        metadata.update(status="error", error=str(exc))
        raise ProfileError(f"{exc}\nФайлы запуска: {directory}") from exc
    finally:
        (directory / "hotspots.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
