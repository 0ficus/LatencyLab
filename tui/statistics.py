"""Only mean/min/max over successful samples; unavailable counters stay unavailable."""
from __future__ import annotations

from statistics import mean

from .runner import RunOutcome
from .schema import KNOWN_METRICS


def summarize(outcomes: list[RunOutcome]) -> dict:
    successful = [item.result for item in outcomes if item.result.status == "ok" and item.result.exit_code == 0]
    result = {}
    for name in ("elapsed_ns", *KNOWN_METRICS):
        values = []
        unit = "ns" if name == "elapsed_ns" or name == "task_clock" else "count"
        for sample in successful:
            if name == "elapsed_ns":
                values.append(sample.elapsed_ns)
            else:
                metric = sample.metrics.get(name)
                if metric is not None and metric.available and metric.value is not None:
                    values.append(metric.value)
        result[name] = {
            "unit": unit, "n": len(values), "mean": mean(values) if values else None,
            "min": min(values) if values else None, "max": max(values) if values else None,
        }
    return result
