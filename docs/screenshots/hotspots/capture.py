"""Export the real Textual UI with explicitly labelled, synthetic test fixtures.

Run from the repository root: PYTHONPATH=. python3 docs/screenshots/hotspots/capture.py
No target is executed and no perf measurements are produced by this script.
"""
import asyncio
from pathlib import Path
from unittest.mock import patch

from textual.widgets import DataTable, Input, Static

from tui.app import LatencyLabApp
from tui.hotspots import parse_report
from tui.profiler import ProfileResult
from tui.runner import PROJECT_ROOT

OUTPUT = Path(__file__).resolve().parent
SOURCE = PROJECT_ROOT / "src/demos/matrix.cpp"


def fixture(mode):
    counts = ([(60, 28), (20, 27), (10, 50), (5, 49), (3, 0), (2, 44)] if mode == "cpu"
              else [(75, 28), (10, 27), (5, 29), (5, 45), (3, 0), (2, 44)])
    report = ""
    for count, line in counts:
        symbol = "columns" if line in (27, 28, 29) else ("main" if line else "[unknown]")
        location = f"{SOURCE}:{line}" if line else "??:0"
        report += f"0%\t{count}\t[.] {symbol}\t{location}\tdemo_matrix\n"
    rows, total = parse_report(report)
    return ProfileResult(mode, 0, rows, total, Path("results/DEMO-FIXTURE"),
                         "ТЕСТОВЫЕ ДАННЫЕ: только демонстрация интерфейса, не результат perf.")


async def capture(mode, size):
    app = LatencyLabApp(sample=5)
    with patch("tui.hotspot_screen.profile", return_value=fixture(mode)):
        async with app.run_test(size=size) as pilot:
            app.query_one("#command", Input).value = "./build/demo_matrix 512 10 columns"
            await pilot.click("#hotspots")
            await pilot.click(f"#profile-{mode}")
            screen = app.screen
            for _ in range(100):
                if screen.result is not None and not screen._busy:
                    break
                await asyncio.sleep(0.02)
            assert screen.result is not None and not screen._busy
            assert screen.query_one("#hotspot-table", DataTable).row_count == 5
            screen.query("#hotspot-dialog > Static").first().update(
                f"Участки кода · {'CPU' if mode == 'cpu' else 'Cache misses'} · ДЕМО / ТЕСТОВЫЕ ДАННЫЕ")
            await pilot.pause()
            name = f"{mode}-{size[0]}x{size[1]}.svg"
            app.save_screenshot(name, str(OUTPUT))
            path = OUTPUT / name
            path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n")


async def main():
    for mode, size in [("cpu", (120, 40)), ("cache", (120, 40)), ("cpu", (80, 24))]:
        await capture(mode, size)


if __name__ == "__main__":
    asyncio.run(main())
