"""Explainable hypotheses, measurement quality and conservative series comparison.

Thresholds are screening heuristics, not CPU-specific norms or causal probabilities.
Ratios use events from the same run, never unrelated sample means.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import mean, median, pstdev
from typing import Optional

from .runner import RunOutcome

MIN_SAMPLES = 3
MIN_ELAPSED_NS = 10_000_000
MIN_RUNNING_FRACTION = 0.8
MAX_TIME_CV = 0.1


@dataclass
class Indicator:
    key: str
    label: str
    value: Optional[float]
    text: str
    warn: bool = False
    n: int = 0

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass
class Hypothesis:
    title: str
    evidence: str
    check: str
    weight: float = 0.0  # number of supporting indicators, not probability
    key: str = ""
    strength: str = "предварительная"
    alternatives: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


@dataclass
class Quality:
    successful: int = 0
    excluded: int = 0
    elapsed_cv: Optional[float] = None
    limitations: list[str] = field(default_factory=list)
    comparable: bool = True
    repeatable: bool = False


@dataclass
class Analysis:
    indicators: list[Indicator] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    quality: Quality = field(default_factory=Quality)

    @property
    def verdict(self) -> str:
        if not self.quality.comparable:
            return "Серия неоднородна: анализируйте команды и условия отдельно."
        if not self.quality.successful:
            return "Недостаточно данных: нет успешных запусков."
        if self.hypotheses:
            return self.hypotheses[0].title
        if not any(i.available for i in self.indicators):
            return "Недостаточно данных для гипотез."
        return "Согласованных признаков причины не найдено; это не исключает узкое место."

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, **asdict(self)}


def _successful(outcomes):
    return [o for o in outcomes if o.result.status == "ok" and o.result.exit_code == 0]


def _identity(outcome):
    r = outcome.result
    argv = outcome.raw.get("argv")
    command = tuple(argv) if isinstance(argv, list) and all(isinstance(arg, str) for arg in argv) else r.command
    return command, r.cpu, r.kernel, outcome.raw.get("scope"), r.mock


def _quality(outcomes: list[RunOutcome]) -> Quality:
    good = _successful(outcomes)
    q = Quality(successful=len(good), excluded=len(outcomes) - len(good))
    notes = q.limitations
    if q.excluded:
        notes.append(f"Исключены ошибочные/отменённые запуски: {q.excluded}.")
    if not good:
        return q
    if len({_identity(o) for o in good}) > 1:
        q.comparable = False
        notes.append("Различаются команда, CPU, kernel, scope или признак mock.")
    if any(o.result.mock for o in good):
        notes.append("Синтетические данные: выводы не относятся к реальной программе.")
    if any(o.result.cpu is None or not o.result.kernel or not o.raw.get("scope") for o in good):
        notes.append("Не полностью описаны CPU/kernel/область измерений.")
    if len(good) < MIN_SAMPLES:
        notes.append(f"Мало повторов: {len(good)}; нужно хотя бы {MIN_SAMPLES}.")
    times = [o.result.elapsed_ns for o in good]
    if min(times) < MIN_ELAPSED_NS:
        notes.append("Есть запуски короче 10 мс: запуск процесса и погрешность могут доминировать.")
    if min(times) <= 0:
        q.comparable = False
        notes.append("Некорректное неположительное wall time.")
    if mean(times) > 0:
        q.elapsed_cv = pstdev(times) / mean(times)
        if q.elapsed_cv > MAX_TIME_CV:
            notes.append(f"Большой разброс wall time: CV={q.elapsed_cv:.1%}; повторите на спокойной машине.")
    rejected = set()
    missing = set()
    for o in good:
        for name, metric in o.result.metrics.items():
            if not metric.available:
                missing.add(name)
            elif _event(o, name) is None:
                rejected.add(name)
    if missing:
        notes.append("Недоступны в части или всех запусках: " + ", ".join(sorted(missing)) + ".")
    if rejected:
        notes.append("Не используются события с неизвестным timing или running/enabled < 80%: "
                     + ", ".join(sorted(rejected)) + ".")
    q.repeatable = (q.comparable and len(good) >= MIN_SAMPLES and min(times) >= MIN_ELAPSED_NS
                    and q.elapsed_cv is not None and q.elapsed_cv <= MAX_TIME_CV
                    and not any(o.result.mock or o.result.cpu is None or not o.result.kernel or not o.raw.get("scope") for o in good))
    return q


def _event(outcome: RunOutcome, name: str) -> Optional[float]:
    m = outcome.result.metrics.get(name)
    if m is None or not m.available or m.value is None:
        return None
    if not (0 < m.time_running_ns <= m.time_enabled_ns):
        return None
    if m.time_running_ns / m.time_enabled_ns < MIN_RUNNING_FRACTION:
        return None
    return m.value


def _ratio(o, numerator, denominator, scale=1.0):
    a, b = _event(o, numerator), _event(o, denominator)
    return a / b * scale if a is not None and b is not None and b > 0 else None


def _resource(o, name):
    resources = o.result.resources
    value = getattr(resources, name) if resources else None
    return value if value is not None and value >= 0 else None


def _system_fraction(o):
    user, system = _resource(o, "user_cpu_us"), _resource(o, "system_cpu_us")
    return system / (user + system) if user is not None and system is not None and user + system > 0 else None


def _utilization(o):
    # Keep the scope consistent: task_clock covers the measured main thread.
    # wait4 CPU time can cover all target threads and must not replace task_clock.
    clock = _event(o, "task_clock")
    elapsed = o.result.elapsed_ns
    return clock / elapsed if clock is not None and elapsed > 0 and clock <= elapsed * 1.05 else None


def analyze(outcomes: list[RunOutcome]) -> Analysis:
    analysis = Analysis(quality=_quality(outcomes))
    good = _successful(outcomes)
    if not good or not analysis.quality.comparable:
        return analysis

    specs = [
        ("cpi", "CPI (тактов на инструкцию)", lambda o: _ratio(o, "cycles", "instructions"), 2.0, False),
        ("cache_miss_rate", "cache_misses/cache_references (PMU-зависимо)", lambda o: _ratio(o, "cache_misses", "cache_references"), .05, True),
        ("miss_per_1k_ins", "Cache misses на 1k инструкций", lambda o: _ratio(o, "cache_misses", "instructions", 1000), 20.0, False),
        ("branch_miss_rate", "Доля ошибок ветвления", lambda o: _ratio(o, "branch_misses", "branches"), .02, True),
        ("backend_stall", "Backend-stall / cycles", lambda o: _ratio(o, "stalled_cycles_backend", "cycles"), .30, True),
        ("frontend_stall", "Frontend-stall / cycles", lambda o: _ratio(o, "stalled_cycles_frontend", "cycles"), .30, True),
        ("sys_cpu", "Доля system CPU процесса", _system_fraction, .20, True),
        ("cpu_util", "Main-thread task_clock / wall time", _utilization, None, True),
        ("minor_faults", "Minor page faults", lambda o: _resource(o, "minor_page_faults"), 1000, False),
        ("major_faults", "Major page faults", lambda o: _resource(o, "major_page_faults"), 0, False),
    ]
    by_key = {}
    samples = {}
    for key, label, getter, threshold, percent in specs:
        values = [v for o in good if (v := getter(o)) is not None]
        # A rate above 1 from independently scheduled events is not a probability.
        if key in {"cache_miss_rate", "branch_miss_rate", "backend_stall", "frontend_stall"}:
            if any(v > 1 for v in values):
                analysis.quality.limitations.append(f"{label}: отношение > 1; событие/интервалы требуют проверки на этом PMU.")
            values = [v for v in values if v <= 1]
        value = mean(values) if values else None
        warn = value is not None and ((value < .90) if key == "cpu_util" else threshold is not None and value > threshold)
        text = "—" if value is None else (f"{value:.1%}" if percent else f"{value:.2f}")
        indicator = Indicator(key, label, value, text, warn, len(values))
        analysis.indicators.append(indicator)
        by_key[key] = indicator
        samples[key] = values

    def high(key):
        values = samples[key]
        threshold = next(s[3] for s in specs if s[0] == key)
        predicate = (lambda v: v < .90) if key == "cpu_util" else (lambda v: v > threshold)
        return bool(values) and sum(predicate(v) for v in values) / len(values) >= .8

    def hypothesis(key, title, evidence_keys, check, alternatives, required):
        evidence = [by_key[k] for k in evidence_keys if high(k)]
        if not evidence:
            return
        missing = [by_key[k].label for k in required if not by_key[k].available]
        complete = all(i.n == len(good) for i in evidence)
        multiple = len(evidence) >= 2
        strength = "несколько согласованных признаков" if multiple and complete and analysis.quality.repeatable else "предварительная"
        analysis.hypotheses.append(Hypothesis(
            title=title, evidence="; ".join(f"{i.label}: {i.text}, n={i.n}" for i in evidence),
            check=check, weight=len(evidence), key=key, strength=strength,
            alternatives=alternatives, missing=missing,
        ))

    # Backend stalls alone do not identify memory as the cause.
    if high("miss_per_1k_ins") or high("cache_miss_rate"):
        hypothesis("memory", "Возможна проблема локальности данных / доступа к памяти",
                   ["miss_per_1k_ins" if high("miss_per_1k_ins") else "cache_miss_rate", "cpi", "backend_stall"],
                   "Сравните одинаковую работу с rows/columns или sequential/strided; затем попробуйте блокирование данных.",
                   ["Задержки вычислительных зависимостей", "Изменение объёма работы или частоты CPU"],
                   ["miss_per_1k_ins", "cpi", "backend_stall"])
    elif high("backend_stall"):
        hypothesis("backend", "Есть backend-stall; источник задержки пока не определён",
                   ["backend_stall", "cpi"], "Добавьте cache events и сравните локальность данных и вычислительные зависимости.",
                   ["Ожидание памяти", "Зависимые вычисления", "Ограничение исполнительных ресурсов"], ["miss_per_1k_ins"])
    if high("branch_miss_rate"):
        hypothesis("branches", "Возможны потери на предсказании ветвлений", ["branch_miss_rate", "cpi"],
                   "Сравните тот же алгоритм на предсказуемых данных или с branchless-вариантом.",
                   ["Ошибка ветвления может перекрываться другими задержками"], ["cpi"])
    if high("frontend_stall"):
        hypothesis("frontend", "Возможны ограничения доставки инструкций", ["frontend_stall", "cpi"],
                   "Проверьте горячий код профилировщиком и сравните вариант с меньшим размером кода.",
                   ["Промахи instruction cache", "Декодирование", "Ошибки предсказания переходов"], ["cpi"])
    if high("sys_cpu") or high("major_faults"):
        hypothesis("system", "Возможны затраты на работу ядра / обработку страниц",
                   ["sys_cpu", "minor_faults", "major_faults"],
                   "Увеличьте полезную работу; отдельно сравните first-touch и повторное использование памяти. Проверьте syscalls/I/O.",
                   ["Инициализация и загрузчик", "Аллокации", "Системные вызовы и I/O"], ["sys_cpu"])
    if high("cpu_util"):
        hypothesis("off_cpu", "Возможно ожидание или вытеснение главного потока", ["cpu_util"],
                   "Повторите на свободном CPU; трассировкой планировщика отличите вытеснение от sleep/I/O/блокировок.",
                   ["Вытеснение планировщиком", "I/O, sleep или блокировка", "Работа других потоков цели"], [])
    analysis.hypotheses.sort(key=lambda h: (-h.weight, h.key))
    analysis.quality.limitations.append("Пороги эвристические; generic cache/stall events зависят от PMU. События измеряются независимо.")
    return analysis


def format_analysis(analysis: Analysis, max_hypotheses: int = 3) -> str:
    lines = [f"Анализ: {analysis.verdict}",
             f"Качество: успешных {analysis.quality.successful}, исключено {analysis.quality.excluded}."]
    for note in analysis.quality.limitations:
        lines.append(f"  Ограничение: {note}")
    shown = [i for i in analysis.indicators if i.available]
    if shown:
        lines.append("  • " + "   ".join(f"{i.label}: {i.text} (n={i.n})" for i in shown))
    for h in analysis.hypotheses[:max(0, max_hypotheses)]:
        lines += [f"  — {h.title} [{h.strength}]", f"    наблюдения: {h.evidence}",
                  "    альтернативы: " + "; ".join(h.alternatives)]
        if h.missing:
            lines.append("    не хватает: " + ", ".join(h.missing))
        lines.append(f"    эксперимент: {h.check}")
    return "\n".join(lines)
