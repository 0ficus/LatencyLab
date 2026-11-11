"""Модель данных контракта runner <-> TUI.

TUI общается с C++ runner'ом через один JSON-объект, который тот печатает
в stdout. Этот модуль описывает и валидирует этот объект. Контракт версируется
через ``schema_version``, чтобы его можно было развивать, не ломая TUI молча.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Optional

SCHEMA_VERSION = 1

# Порядок показа метрик, которые мы понимаем.
KNOWN_METRICS = (
    "cycles",
    "instructions",
    "cache_misses",
    "cache_references",
    "branch_misses",
    "branches",
    "stalled_cycles_backend",
    "stalled_cycles_frontend",
    "task_clock",
    "context_switches",
    "cpu_migrations",
)


class ResultParseError(ValueError):
    """stdout runner'а — валидный JSON, но не соответствует контракту."""


@dataclass
class Metric:
    name: str
    status: str
    unit: str = ""
    value: Optional[float] = None
    raw_value: Optional[int] = None
    time_enabled_ns: int = 0
    time_running_ns: int = 0
    errno: int = 0
    reason: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclass
class Resources:
    source: str = ""
    user_cpu_us: Optional[int] = None
    system_cpu_us: Optional[int] = None
    minor_page_faults: Optional[int] = None
    major_page_faults: Optional[int] = None


@dataclass
class RunnerResult:
    schema_version: int
    command: str
    exit_code: int
    elapsed_ns: int
    metrics: dict[str, Metric]
    status: str = "ok"
    error: Optional[str] = None
    cpu: Optional[int] = None
    kernel: Optional[str] = None
    perf_event_paranoid: Optional[str] = None
    resources: Optional[Resources] = None
    mock: bool = False
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def available_metrics(self) -> list[Metric]:
        return [m for m in self.metrics.values() if m.available]

    @property
    def hardware_available(self) -> int:
        """Число доступных аппаратных счётчиков (без task_clock)."""
        return sum(
            1
            for name in ("cycles", "instructions", "cache_misses")
            if self.metrics.get(name) and self.metrics[name].available
        )


def parse_metric(name: str, raw: Any) -> Metric:
    if not isinstance(raw, dict):
        raise ResultParseError(f"метрика {name!r} должна быть объектом")

    def _int(key: str) -> int:
        value = raw.get(key)
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            raise ResultParseError(
                f"поле {key!r} метрики {name!r} должно быть целым числом"
            )

    status = raw.get("status", "unavailable")
    if not isinstance(status, str):
        status = "unavailable"
    value = raw.get("value")
    if status == "available" and (
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
    ):
        raise ResultParseError(f"метрика {name!r} объявлена доступной, но не содержит конечного неотрицательного числа")
    return Metric(
        name=name,
        status=status,
        unit=str(raw.get("unit", "") or ""),
        value=value if status == "available" else None,
        raw_value=raw.get("raw_value"),
        time_enabled_ns=_int("time_enabled_ns"),
        time_running_ns=_int("time_running_ns"),
        errno=_int("errno"),
        reason=raw.get("reason"),
    )


def parse_resources(raw: Any) -> Optional[Resources]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ResultParseError("поле 'resources' должно быть объектом")

    def _opt_int(key: str) -> Optional[int]:
        value = raw.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return Resources(
        source=str(raw.get("source", "") or ""),
        user_cpu_us=_opt_int("user_cpu_us"),
        system_cpu_us=_opt_int("system_cpu_us"),
        minor_page_faults=_opt_int("minor_page_faults"),
        major_page_faults=_opt_int("major_page_faults"),
    )


def parse_result(data: Any) -> RunnerResult:
    if not isinstance(data, dict):
        raise ResultParseError("stdout runner'а должен быть JSON-объектом")

    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise ResultParseError("неподдерживаемая версия schema_version")

    def _req_int(key: str) -> int:
        value = data.get(key)
        if type(value) is not int:
            raise ResultParseError(f"поле {key!r} должно быть целым числом")
        return int(value)

    command = data.get("command", "")
    if not isinstance(command, str):
        raise ResultParseError("поле 'command' должно быть строкой")

    metrics_raw = data.get("metrics", {})
    if not isinstance(metrics_raw, dict):
        raise ResultParseError("поле 'metrics' должно быть объектом")

    metrics: dict[str, Metric] = {}
    for name, raw in metrics_raw.items():
        metrics[str(name)] = parse_metric(str(name), raw)
    # Известные метрики присутствуют всегда, даже если runner их не отдал.
    for name in KNOWN_METRICS:
        if name not in metrics:
            metrics[name] = Metric(name=name, status="unavailable", reason="не отдано runner'ом")

    cpu = data.get("cpu")
    return RunnerResult(
        schema_version=schema_version,
        command=command,
        exit_code=_req_int("exit_code"),
        elapsed_ns=_req_int("elapsed_ns"),
        metrics=metrics,
        status=str(data.get("status", "ok" if data.get("exit_code") == 0 else "target_error")),
        error=data.get("error"),
        cpu=int(cpu) if isinstance(cpu, (int, float)) and not isinstance(cpu, bool) else None,
        kernel=data.get("kernel"),
        perf_event_paranoid=data.get("perf_event_paranoid"),
        resources=parse_resources(data.get("resources")),
        mock=bool(data.get("mock", False)),
        stdout=str(data.get("stdout", "") or ""),
        stderr=str(data.get("stderr", "") or ""),
        stdout_truncated=bool(data.get("stdout_truncated", False)),
        stderr_truncated=bool(data.get("stderr_truncated", False)),
    )
