"""Вызов C++ runner'а и разбор его JSON-вывода.

Runner — бинарник Серёжи. Пока его нет, укажите ``LATENCYLAB_RUNNER`` на мок,
чтобы гонять TUI end-to-end::

    LATENCYLAB_RUNNER="python3 tui/mock_runner.py" python3 -m tui

Контракт вызова:

    runner --command CMD [--cpu N]

Runner выполняет ``CMD``, собирает метрики и печатает в stdout ровно один
JSON-объект (см. ``schema.py``). Вывод самой программы передаётся внутри JSON,
а не вперемешку со stdout runner'а.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

from .schema import ResultParseError, RunnerResult, parse_result

DEFAULT_RUNNER = "runner"


class RunnerError(RuntimeError):
    """Runner не удалось выполнить, либо он не вернул валидный результат."""


@dataclass
class RunOutcome:
    """Сырой ответ runner'а + разобранная модель для отображения."""

    result: RunnerResult
    raw: dict[str, Any]


def run_runner(
    command: str,
    cpu: Optional[int] = None,
    runner: Optional[str] = None,
    timeout: Optional[float] = None,
) -> RunOutcome:
    runner = runner or os.environ.get("LATENCYLAB_RUNNER", DEFAULT_RUNNER)
    # shlex.split позволяет задать runner с аргументами, например
    # "python tui/mock_runner.py".
    argv = shlex.split(runner) + ["--command", command]
    if cpu is not None:
        argv += ["--cpu", str(cpu)]

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise RunnerError(
            f"не удалось запустить runner {runner!r}: {reason}. Соберите его или "
            "укажите мок: LATENCYLAB_RUNNER='python3 tui/mock_runner.py'"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"runner не завершился за {timeout} с") from exc

    if not proc.stdout or not proc.stdout.strip():
        detail = (proc.stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RunnerError(f"runner не вернул JSON (код {proc.returncode}){suffix}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError(f"stdout runner'а — не валидный JSON: {exc}") from exc

    try:
        result = parse_result(data)
    except ResultParseError as exc:
        raise RunnerError(f"результат runner'а не прошёл валидацию: {exc}") from exc

    return RunOutcome(result=result, raw=data)
