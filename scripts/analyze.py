#!/usr/bin/env python3
"""CLI-диагноз причин задержки по сохранённым результатам LatencyLab.

Читает results/series-*.json (со сводкой серии) или отдельные results/run-*.json
и печатает производные индикаторы и гипотезы о причинах задержки.

Примеры:
    python3 scripts/analyze.py results/series-*.json
    python3 scripts/analyze.py results/run-*.json
    python3 scripts/analyze.py results/series-*.json --max-hypotheses 5
"""
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
from tui.analysis import analyze, format_analysis  # noqa: E402


def _collect(files: list[str]) -> list[RunOutcome]:
    outcomes: list[RunOutcome] = []
    for arg in files:
        expanded = glob.glob(arg) or [arg]
        for name in expanded:
            path = Path(name)
            if not path.is_file():
                print(f"  предупреждение: нет файла {path}", file=sys.stderr)
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"  предупреждение: не удалось прочитать {path}: {exc}", file=sys.stderr)
                continue
            if raw.get("kind") == "sample_series":
                runs = raw.get("runs", [])
                for run in runs:
                    _append(outcomes, run, path.name)
            else:
                _append(outcomes, raw, path.name)
    return outcomes


def _append(outcomes: list[RunOutcome], raw: dict, source: str) -> None:
    try:
        outcomes.append(RunOutcome(parse_result(raw), raw))
    except ResultParseError as exc:
        print(f"  предупреждение: пропущен запуск из {source}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Диагноз причин задержки по результатам LatencyLab")
    parser.add_argument("files", nargs="+", help="results/series-*.json или results/run-*.json")
    parser.add_argument("--max-hypotheses", type=int, default=3, help="сколько гипотез показывать")
    args = parser.parse_args(argv)

    outcomes = _collect(args.files)
    if not outcomes:
        print("Не удалось разобрать ни одного запуска.", file=sys.stderr)
        return 1

    print(f"Запусков: {len(outcomes)}, команда: {outcomes[0].result.command}")
    print(format_analysis(analyze(outcomes), args.max_hypotheses))
    return 0


if __name__ == "__main__":
    sys.exit(main())
