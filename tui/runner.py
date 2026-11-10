"""Python subprocess -> C++ runner -> measured executable -> JSON."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from .schema import ResultParseError, RunnerResult, parse_result

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNNER = str(PROJECT_ROOT / "build" / "runner")


class RunnerError(RuntimeError):
    """Runner could not be executed or returned invalid data."""


@dataclass
class RunOutcome:
    result: RunnerResult
    raw: dict[str, Any]


def run_runner(
    command: str,
    cpu: Optional[int] = None,
    runner: Optional[str] = None,
    timeout: Optional[float] = 10,
    cancel: Optional[threading.Event] = None,
) -> RunOutcome:
    runner = runner or os.environ.get("LATENCYLAB_RUNNER") or DEFAULT_RUNNER
    try:
        target = shlex.split(command)
        prefix = [runner] if runner == DEFAULT_RUNNER or Path(runner).is_file() else shlex.split(runner)
    except ValueError as exc:
        raise RunnerError(f"Некорректные кавычки в команде: {exc}") from exc
    if not target or not prefix:
        raise RunnerError("Команда или путь runner не заданы")
    if timeout is not None and (not math.isfinite(timeout) or not 0.001 <= timeout <= 3600):
        raise RunnerError("Таймаут должен быть от 0.001 до 3600 секунд")
    argv = list(prefix)
    if cpu is not None:
        argv += ["--cpu", str(cpu)]
    if timeout is not None:
        argv += ["--timeout-ms", str(max(1, round(timeout * 1000)))]
    # No shell. Quoted arguments, paths with spaces and literal shell characters are preserved.
    argv += ["--", *target]
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            errors="replace", start_new_session=True,
        )
    except OSError as exc:
        raise RunnerError(f"Не удалось запустить runner {runner!r}: {exc}. Соберите проект через CMake.") from exc

    deadline = time.monotonic() + (timeout if timeout is not None else 10) + 3
    stopped_at = None
    watchdog = False
    try:
        while True:
            now = time.monotonic()
            if stopped_at is None and ((cancel is not None and cancel.is_set()) or now >= deadline):
                watchdog = now >= deadline
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                stopped_at = now
            if stopped_at is not None and now - stopped_at >= 2:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                stdout, stderr = proc.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
        raise
    if watchdog:
        raise RunnerError("Runner не завершился после таймаута и был остановлен")
    if proc.returncode or not stdout.strip():
        detail = stderr.strip() or f"код {proc.returncode}, JSON отсутствует"
        raise RunnerError(f"Ошибка runner: {detail}")
    try:
        data = json.loads(stdout)
        result = parse_result(data)
    except (ValueError, ResultParseError) as exc:
        raise RunnerError(f"Некорректный JSON результата runner: {exc}") from exc
    # Preserve the user's original quoting in the saved result and UI.
    data["command"] = command
    result.command = command
    return RunOutcome(result=result, raw=data)
