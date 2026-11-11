"""Descriptive comparison of two series; causal support requires explicit controls."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import median

from .analysis import Analysis, _event, _ratio, _successful, analyze
from .runner import RunOutcome


@dataclass
class Change:
    key: str
    label: str
    baseline: float
    candidate: float
    ratio: float | None  # undefined when the baseline is zero
    baseline_n: int
    candidate_n: int


@dataclass
class Comparison:
    baseline: Analysis
    candidate: Analysis
    changes: list[Change] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    supported_hypothesis: str | None = None
    comparable: bool = False

    def to_dict(self):
        return asdict(self)


def _branch_rate(outcome):
    value = _ratio(outcome, "branch_misses", "branches")
    return value if value is not None and value <= 1 else None


def compare(baseline: list[RunOutcome], candidate: list[RunOutcome], *, equivalent_work=False) -> Comparison:
    report = Comparison(analyze(baseline), analyze(candidate))
    a, b = _successful(baseline), _successful(candidate)
    if not a or not b:
        report.limitations.append("Для сравнения нужны успешные запуски обоих вариантов.")
        return report
    if not report.baseline.quality.comparable or not report.candidate.quality.comparable:
        report.limitations.append("Внутри серии смешаны команды или условия; сравнение остановлено.")
        return report
    for label, getter in (
        ("CPU", lambda o: o.result.cpu), ("kernel", lambda o: o.result.kernel),
        ("scope", lambda o: o.raw.get("scope")),
    ):
        left, right = {getter(o) for o in a}, {getter(o) for o in b}
        if left != right or None in left or None in right or "" in left or "" in right:
            report.limitations.append(f"Не совпадают или не описаны условия {label}; сравнение остановлено.")
    if any(o.result.mock for o in a + b):
        report.limitations.append("Мок: сравнение не подтверждает причины в реальной программе.")
    if report.limitations:
        return report
    report.comparable = True
    specs = [
        ("elapsed_ns", "Wall time", lambda o: o.result.elapsed_ns),
        ("instructions", "Instructions", lambda o: _event(o, "instructions")),
        ("cpi", "CPI", lambda o: _ratio(o, "cycles", "instructions")),
        ("miss_per_1k_ins", "Cache misses на 1k инструкций", lambda o: _ratio(o, "cache_misses", "instructions", 1000)),
        ("branch_miss_rate", "Ошибки ветвлений / branches", _branch_rate),
    ]
    for key, label, getter in specs:
        left = [v for o in a if (v := getter(o)) is not None]
        right = [v for o in b if (v := getter(o)) is not None]
        if not left or not right:
            continue
        x, y = median(left), median(right)
        report.changes.append(Change(key, label, x, y, y / x if x else None, len(left), len(right)))
    changes = {change.key: change for change in report.changes}
    time = changes["elapsed_ns"]
    report.observations.append(f"Медиана wall time: база {time.baseline / 1e6:.2f} мс → вариант {time.candidate / 1e6:.2f} мс ({time.ratio:.2f}×).")
    if not equivalent_work:
        report.limitations.append("Эквивалентность работы не подтверждена пользователем; сравниваются только наблюдения.")
    repeatable = report.baseline.quality.repeatable and report.candidate.quality.repeatable
    if not repeatable:
        report.limitations.append("Недостаточно повторов, слишком короткие запуски или большой разброс; повторите обе серии.")
    cv = (report.baseline.quality.elapsed_cv or 0) + (report.candidate.quality.elapsed_cv or 0)
    distinguishable = abs(time.ratio - 1) > max(.10, 2 * cv)
    if not distinguishable:
        report.limitations.append("Различие времени мало относительно разброса/эвристического порога; эффект не выделен.")
    ins = changes.get("instructions")
    same_instruction_count = (ins is not None and ins.ratio is not None and .8 <= ins.ratio <= 1.2
                              and ins.baseline_n == len(a) and ins.candidate_n == len(b))
    if not same_instruction_count:
        report.limitations.append("Нет полного сопоставимого числа инструкций (допуск ±20%); изменение объёма работы не исключено.")
    if equivalent_work and repeatable and distinguishable and same_instruction_count:
        for key, title in (("miss_per_1k_ins", "локальности данных / доступа к памяти"),
                           ("branch_miss_rate", "предсказания ветвлений")):
            change = changes.get(key)
            if not change or change.ratio is None or change.baseline_n != len(a) or change.candidate_n != len(b):
                continue
            same_direction = ((time.ratio > 1 and change.ratio > 1.2) or
                              (time.ratio < 1 and change.ratio < .8))
            if same_direction:
                report.supported_hypothesis = (
                    f"Изменения времени и «{change.label}» согласованы при близком числе инструкций. "
                    f"Это поддерживает гипотезу о влиянии {title}, но не доказывает причину."
                )
                break
    report.limitations.append("Убедитесь, что использована одна физическая машина и PMU: модель CPU пока не сохранена.")
    report.limitations.append("Серии измерены отдельно: порядок, частота CPU и фон не контролируются. Повторите с чередованием A/B.")
    return report


def format_comparison(report: Comparison) -> str:
    lines = ["Сравнение с базовой серией:", *["  " + s for s in report.observations]]
    for change in report.changes:
        if change.key == "elapsed_ns":
            continue
        ratio = "база равна нулю" if change.ratio is None else f"{change.ratio:.2f}×"
        lines.append(f"  {change.label}: {change.baseline:.3g} → {change.candidate:.3g}, {ratio} (n={change.baseline_n}/{change.candidate_n})")
    if report.supported_hypothesis:
        lines.append("  " + report.supported_hypothesis)
    else:
        lines.append("  Причина изменения времени не установлена.")
    lines += ["  Ограничение: " + s for s in report.limitations]
    return "\n".join(lines)
