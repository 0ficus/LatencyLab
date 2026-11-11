"""Производные индикаторы и гипотезы о причинах задержки.

Принцип (см. docs/project-plan.md): по одному числу причину не доказываем.
Индикатор — это наблюдение; гипотеза — это «похоже на …» + доказательства +
подсказка, что проверить дальше. Гипотезы не выдаются как вердикт.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .runner import RunOutcome

# Пороги «выше нормы». Подобраны консервативно: ложное срабатывание хуже,
# чем пропущенная гипотеза, потому что вывод формулируется как предположение.
CPI_HIGH = 2.0
LLC_MISS_RATE_HIGH = 0.05        # доля промахов LLC
MISS_PER_1K_INS_HIGH = 20.0      # промахов LLC на 1000 инструкций
BRANCH_MISS_RATE_HIGH = 0.02     # доля ошибочных ветвлений
BACKEND_STALL_HIGH = 0.30        # доля тактов backend-stall
FRONTEND_STALL_HIGH = 0.30       # доля тактов frontend-stall
SYS_CPU_HIGH = 0.20              # доля system CPU
OFF_CPU_LOW = 0.90               # ниже — существенное время вне CPU


@dataclass
class Indicator:
    key: str
    label: str
    value: Optional[float]
    text: str
    warn: bool = False

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass
class Hypothesis:
    title: str
    evidence: str
    check: str
    weight: float = 0.0  # больше — сильнее подозрение


@dataclass
class Analysis:
    indicators: list[Indicator] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if not self.hypotheses:
            return "Недостаточно данных: аппаратные счётчики недоступны."
        return self.hypotheses[0].title


def _mean(values: list[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _metric_mean(results, name: str) -> Optional[float]:
    values = []
    for r in results:
        metric = r.metrics.get(name)
        if metric is not None and metric.available and metric.value is not None:
            values.append(metric.value)
    return _mean(values)


def _resources_mean(results, attr: str) -> Optional[float]:
    values = []
    for r in results:
        res = r.resources
        if res is not None:
            value = getattr(res, attr)
            if value is not None:
                values.append(value)
    return _mean(values)


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: Optional[float], decimals: int = 2) -> str:
    return "—" if value is None else f"{value:.{decimals}f}"


def analyze(outcomes: list[RunOutcome]) -> Analysis:
    results = [o.result for o in outcomes if o.result.status == "ok" and o.result.exit_code == 0]
    analysis = Analysis()

    cyc = _metric_mean(results, "cycles")
    ins = _metric_mean(results, "instructions")
    miss = _metric_mean(results, "cache_misses")
    refs = _metric_mean(results, "cache_references")
    bmiss = _metric_mean(results, "branch_misses")
    branches = _metric_mean(results, "branches")
    backend = _metric_mean(results, "stalled_cycles_backend")
    frontend = _metric_mean(results, "stalled_cycles_frontend")
    task_clock = _metric_mean(results, "task_clock")
    elapsed = _mean([r.elapsed_ns for r in results])

    user = _resources_mean(results, "user_cpu_us")
    system = _resources_mean(results, "system_cpu_us")
    minor_flt = _resources_mean(results, "minor_page_faults")
    major_flt = _resources_mean(results, "major_page_faults")

    cpi = cyc / ins if (cyc and ins) else None
    miss_rate = miss / refs if (miss is not None and refs) else None
    miss_per_k = (miss / ins * 1000) if (miss is not None and ins) else None
    branch_rate = bmiss / branches if (bmiss is not None and branches) else None
    backend_frac = backend / cyc if (backend is not None and cyc) else None
    frontend_frac = frontend / cyc if (frontend is not None and cyc) else None
    sys_frac = (
        system / (user + system)
        if (user is not None and system is not None and (user + system) > 0)
        else None
    )
    cpu_util = task_clock / elapsed if (task_clock is not None and elapsed) else None

    def add(key, label, value, text, warn=False):
        analysis.indicators.append(Indicator(key, label, value, text, warn))

    add("cpi", "CPI (тактов на инструкцию)", cpi, _num(cpi), cpi is not None and cpi > CPI_HIGH)
    add("llc_miss_rate", "Доля промахов LLC", miss_rate, _pct(miss_rate),
        miss_rate is not None and miss_rate > LLC_MISS_RATE_HIGH)
    add("miss_per_1k_ins", "Промахов LLC на 1k инструкций", miss_per_k, _num(miss_per_k, 1),
        miss_per_k is not None and miss_per_k > MISS_PER_1K_INS_HIGH)
    add("branch_miss_rate", "Доля ошибок ветвления", branch_rate, _pct(branch_rate),
        branch_rate is not None and branch_rate > BRANCH_MISS_RATE_HIGH)
    add("backend_stall", "Backend-stall (память/данные)", backend_frac, _pct(backend_frac),
        backend_frac is not None and backend_frac > BACKEND_STALL_HIGH)
    add("frontend_stall", "Frontend-stall (выборка инструкций)", frontend_frac, _pct(frontend_frac),
        frontend_frac is not None and frontend_frac > FRONTEND_STALL_HIGH)
    add("sys_cpu", "Доля system CPU", sys_frac, _pct(sys_frac),
        sys_frac is not None and sys_frac > SYS_CPU_HIGH)
    add("cpu_util", "CPU utilization (task_clock/elapsed)", cpu_util, _pct(cpu_util),
        cpu_util is not None and cpu_util < OFF_CPU_LOW)
    add("minor_faults", "Minor page faults (среднее)", minor_flt, _num(minor_flt, 0),
        minor_flt is not None and minor_flt > 1000)
    add("major_faults", "Major page faults (среднее)", major_flt, _num(major_flt, 0),
        major_flt is not None and major_flt > 0)

    hypotheses: list[Hypothesis] = []

    if backend_frac is not None and backend_frac > BACKEND_STALL_HIGH:
        hypotheses.append(Hypothesis(
            "Похоже на задержки доступа к памяти (backend-stall)",
            f"backend-stall {backend_frac * 100:.1f}% тактов; CPI {_num(cpi)}",
            "проверьте локальность и порядок обхода данных (rows vs columns, sequential vs strided)",
            backend_frac,
        ))

    if miss_rate is not None and miss_rate > LLC_MISS_RATE_HIGH:
        hypotheses.append(Hypothesis(
            "Похоже на промахи кеша последнего уровня (LLC)",
            f"доля промахов LLC {miss_rate * 100:.1f}%, {_num(miss_per_k, 1)} на 1k инструкций",
            "проверьте рабочий набор: не влезает ли он в кеш; уменьшите или переупорядочьте данные",
            miss_rate,
        ))

    if branch_rate is not None and branch_rate > BRANCH_MISS_RATE_HIGH:
        hypotheses.append(Hypothesis(
            "Похоже на ошибки предсказания ветвлений",
            f"доля ошибок {branch_rate * 100:.1f}% при CPI {_num(cpi)}",
            "проверьте предсказуемость веток: сортировка данных, branchless, устойчивые паттерны",
            branch_rate,
        ))

    if frontend_frac is not None and frontend_frac > FRONTEND_STALL_HIGH:
        hypotheses.append(Hypothesis(
            "Похоже на фронт-энд-сталлы (выборка/декод инструкций)",
            f"frontend-stall {frontend_frac * 100:.1f}% тактов",
            "проверьте размер горячего кода и плотность инструкций (icache, разворачивание)",
            frontend_frac,
        ))

    if sys_frac is not None and sys_frac > SYS_CPU_HIGH:
        hypotheses.append(Hypothesis(
            "Похоже на системные вызовы / обработку страниц",
            f"system CPU {sys_frac * 100:.1f}%, minor faults {_num(minor_flt, 0)}, major faults {_num(major_flt, 0)}",
            "проверьте аллокации и first-touch, I/O и частоту системных вызовов",
            sys_frac,
        ))

    if cpu_util is not None and cpu_util < OFF_CPU_LOW:
        hypotheses.append(Hypothesis(
            "Похоже на время вне CPU",
            f"task_clock/elapsed = {cpu_util * 100:.1f}%",
            "проверьте sleep, ожидание I/O или блокировок, влияние планировщика",
            1.0 - cpu_util,
        ))

    hypotheses.sort(key=lambda h: h.weight, reverse=True)

    if not hypotheses and cpi is not None and cpi <= CPI_HIGH:
        hypotheses.append(Hypothesis(
            "Явных узких мест не видно",
            f"CPI {_num(cpi)}, промахов LLC {_num(miss_per_k, 1)} на 1k инструкций",
            "запустите контрастное демо (например, matrix-columns vs matrix-rows), чтобы увидеть эффект",
            0.0,
        ))

    analysis.hypotheses = hypotheses
    return analysis


def format_analysis(analysis: Analysis, max_hypotheses: int = 3) -> str:
    """Плоский текст для CLI и панели TUI."""
    lines = [f"Диагноз: {analysis.verdict}"]
    shown = [i for i in analysis.indicators if i.available]
    if shown:
        lines.append("  • " + "   ".join(f"{i.label}: {i.text}" for i in shown))
    for hypothesis in analysis.hypotheses[:max_hypotheses]:
        lines.append(f"  — {hypothesis.title}")
        lines.append(f"    наблюдение: {hypothesis.evidence}")
        lines.append(f"    проверьте: {hypothesis.check}")
    return "\n".join(lines)
