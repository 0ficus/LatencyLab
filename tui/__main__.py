"""Точка входа TUI:  python3 -m tui [--runner ...] [--demo]"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .app import LatencyLabApp
from .runner import DEFAULT_RUNNER


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tui", description="Latency Lab TUI")
    parser.add_argument(
        "--runner",
        default=None,
        help="путь/команда runner'а (по умолчанию $LATENCYLAB_RUNNER или build/runner)",
    )
    parser.add_argument("--results-dir", default="results", help="куда сохранять JSON результатов")
    parser.add_argument("--demo", action="store_true", help="подставить демо-команду в поле ввода")
    parser.add_argument("--sample", type=int, default=5, help="число запусков программы (1..1000)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=10,
        help="максимальное время одного запуска в секундах (по умолчанию 10)",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.sample <= 1000:
        parser.error("--sample должен быть от 1 до 1000")

    runner = args.runner or os.environ.get("LATENCYLAB_RUNNER") or DEFAULT_RUNNER
    app = LatencyLabApp(
        runner_path=runner,
        results_dir=Path(args.results_dir),
        demo=args.demo,
        runner_timeout=args.timeout,
        sample=args.sample,
    )
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
