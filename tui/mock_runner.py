#!/usr/bin/env python3
"""Мок-раннер — заглушка C++ runner'а до его появления.

Реализует тот же CLI + JSON-контракт, что и настоящий runner, чтобы TUI можно
было разрабатывать и показывать end-to-end без C++ бинарника::

    runner [--cpu N] [--mock-metrics] -- program [args...]

- Реально запускает программу без shell и замеряет wall-time и CPU-time.
- Захватывает stdout/stderr программы внутрь JSON (обрезаются до 64 КиБ).
- Метрики по умолчанию ``unavailable`` (честно — здесь нет perf_event_open),
  а с флагом ``--mock-metrics`` подставляются явно помеченные синтетические
  значения, чтобы предпросмотреть экран результата. При синтетике в корень
  JSON ставится ``"mock": true``.

Запуск:  python3 tui/mock_runner.py --command "echo hi" --mock-metrics
"""
from __future__ import annotations

import argparse
import json
import resource
import shlex
import subprocess
import sys
import time

SCHEMA_VERSION = 1
OUTPUT_CAP = 64 * 1024

_HARDWARE = (
    ("cycles", "count"),
    ("instructions", "count"),
    ("cache_misses", "count"),
)
_METRICS = _HARDWARE + (("task_clock", "ns"),)


def _truncate(text: str, cap: int = OUTPUT_CAP) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    return text[:cap], True


def _unavailable_metrics() -> dict:
    reason = "mock runner: perf_event_open недоступен (нет Linux PMU)"
    return {
        name: {
            "status": "unavailable",
            "unit": unit,
            "value": None,
            "raw_value": None,
            "time_enabled_ns": 0,
            "time_running_ns": 0,
            "errno": 0,
            "reason": reason,
        }
        for name, unit in _METRICS
    }


def _synthetic_metrics(elapsed_ns: int) -> dict:
    """Явно помеченная синтетика только для предпросмотра экрана результата."""
    seconds = elapsed_ns / 1e9
    cycles = int(seconds * 3.0e9)          # условные 3 ГГц
    instructions = int(cycles / 1.7)       # условный IPC
    cache_misses = int(cycles / 400)
    values = {
        "cycles": ("count", float(cycles), cycles),
        "instructions": ("count", float(instructions), instructions),
        "cache_misses": ("count", float(cache_misses), cache_misses),
        "task_clock": ("ns", float(elapsed_ns), elapsed_ns),
    }
    return {
        name: {
            "status": "available",
            "unit": unit,
            "value": value,
            "raw_value": raw,
            "time_enabled_ns": elapsed_ns,
            "time_running_ns": elapsed_ns,
            "errno": 0,
            "reason": None,
        }
        for name, (unit, value, raw) in values.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mock LatencyLab runner")
    parser.add_argument("--command", help="команда с аргументами (кавычки поддерживаются, без shell)")
    parser.add_argument("--cpu", type=int, default=None, help="CPU (принимается, в моке игнорируется)")
    parser.add_argument("--mock-metrics", action="store_true", help="подставить синтетические метрики")
    parser.add_argument("--timeout-ms", type=int, default=10000)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    target = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if args.command:
        if target:
            parser.error("задайте --command или argv после --, а не оба")
        target = shlex.split(args.command)
    if not target:
        parser.error("задайте бинарник и аргументы после --")

    start = time.monotonic()
    proc = subprocess.run(target, capture_output=True, text=True, timeout=args.timeout_ms / 1000)
    finish = time.monotonic()
    elapsed_ns = int((finish - start) * 1_000_000_000)

    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    stdout, stdout_truncated = _truncate(proc.stdout or "")
    stderr, stderr_truncated = _truncate(proc.stderr or "")

    result = {
        "schema_version": SCHEMA_VERSION,
        "mock": bool(args.mock_metrics),
        "command": args.command or shlex.join(target),
        "cpu": args.cpu,
        "exit_code": proc.returncode,
        "elapsed_ns": elapsed_ns,
        "kernel": None,
        "perf_event_paranoid": None,
        "resources": {
            "source": "getrusage(RUSAGE_CHILDREN)",
            "user_cpu_us": int(usage.ru_utime * 1_000_000),
            "system_cpu_us": int(usage.ru_stime * 1_000_000),
            "minor_page_faults": getattr(usage, "ru_minflt", None),
            "major_page_faults": getattr(usage, "ru_majflt", None),
        },
        "metrics": _synthetic_metrics(elapsed_ns) if args.mock_metrics else _unavailable_metrics(),
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
