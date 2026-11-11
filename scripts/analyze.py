#!/usr/bin/env python3
"""Analyze saved runs, grouped by command/conditions; optionally compare a baseline."""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tui.runner import RunOutcome  # noqa: E402
from tui.schema import ResultParseError, parse_result  # noqa: E402
from tui.analysis import _identity, analyze, format_analysis  # noqa: E402
from tui.comparison import compare, format_comparison  # noqa: E402


def _collect(files: list[str]) -> list[RunOutcome]:
    outcomes = []
    seen = set()
    for arg in files:
        for name in sorted(glob.glob(arg)) or [arg]:
            path = Path(name).resolve()
            if path in seen:
                continue
            seen.add(path)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("ожидался JSON-объект")
                runs = raw.get("runs") if raw.get("kind") == "sample_series" else [raw]
                if not isinstance(runs, list):
                    raise ValueError("runs должен быть массивом")
            except (OSError, ValueError) as exc:
                print(f"Предупреждение: {path.name}: {exc}", file=sys.stderr)
                continue
            for run in runs:
                try:
                    outcomes.append(RunOutcome(parse_result(run), run))
                except (ResultParseError, OverflowError) as exc:
                    print(f"Предупреждение: пропущен запуск из {path.name}: {exc}", file=sys.stderr)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Гипотезы о задержках, качество измерений и сравнение серий")
    parser.add_argument("files", nargs="+", help="JSON запусков или серий")
    parser.add_argument("--baseline", help="JSON базовой серии для сравнения с одним вариантом")
    parser.add_argument("--equivalent-work", action="store_true", help="вы проверили, что база и вариант решают одну задачу на одинаковых данных")
    parser.add_argument("--json", action="store_true", help="структурированный результат анализа в stdout")
    parser.add_argument("--max-hypotheses", type=int, default=3)
    args = parser.parse_args(argv)
    if args.max_hypotheses < 0:
        parser.error("--max-hypotheses должен быть неотрицательным")
    if args.equivalent_work and not args.baseline:
        parser.error("--equivalent-work требует --baseline")
    outcomes = _collect(args.files)
    if not outcomes:
        print("Не удалось разобрать ни одного запуска.", file=sys.stderr)
        return 1
    if args.baseline:
        baseline = _collect([args.baseline])
        if not baseline:
            print("Базовая серия пуста.", file=sys.stderr)
            return 1
        report = compare(baseline, outcomes, equivalent_work=args.equivalent_work)
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(format_analysis(report.candidate, args.max_hypotheses))
            print(format_comparison(report))
        return 0
    # Never pool unrelated workloads from a shell wildcard into a diagnosis.
    groups = {}
    for o in outcomes:
        groups.setdefault(_identity(o), []).append(o)
    reports = []
    for group in groups.values():
        analysis = analyze(group)
        reports.append({"command": group[0].result.command, "analysis": analysis.to_dict()})
        if not args.json:
            print(f"\nКоманда: {group[0].result.command}; запусков: {len(group)}")
            print(format_analysis(analysis, args.max_hypotheses))
    if args.json:
        print(json.dumps({"groups": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
