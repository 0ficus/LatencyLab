import asyncio
import json
import math
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

from textual.widgets import Button, DataTable, Input, Select, Static

from tui.app import LatencyLabApp
from tui.runner import RunOutcome
from tui.schema import ResultParseError, parse_result
from tui.statistics import summarize
from tui.demos import PRESETS

RUNNER = Path(os.environ.get("LATENCYLAB_TEST_RUNNER", "build/runner")).resolve()


def outcome(value, *, status="ok", elapsed=1):
    data = {
        "schema_version": 1, "command": "test", "status": status,
        "exit_code": 0 if status == "ok" else 7, "elapsed_ns": elapsed,
        "metrics": {"cycles": {"status": "available" if value is not None else "unavailable", "value": value}},
    }
    return RunOutcome(parse_result(data), data)


class StatisticsTests(unittest.TestCase):
    def test_zero_is_real_missing_is_not_zero_failed_runs_are_excluded(self):
        rows = summarize([outcome(0), outcome(20), outcome(None), outcome(999, status="target_error")])
        self.assertEqual(rows["cycles"], {"unit": "count", "n": 2, "mean": 10, "min": 0, "max": 20})
        self.assertEqual(rows["elapsed_ns"]["n"], 3)
        self.assertIsNone(rows["instructions"]["mean"])

    def test_unknown_schema_and_available_nan_are_rejected(self):
        data = outcome(1).raw
        data["schema_version"] = 99
        with self.assertRaises(ResultParseError):
            parse_result(data)
        data["schema_version"] = 1
        data["metrics"]["cycles"]["value"] = math.nan
        with self.assertRaises(ResultParseError):
            parse_result(data)


@unittest.skipUnless(sys.platform == "linux" and RUNNER.is_file(), "Built Linux runner required")
class RealTUITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    async def wait_done(self, app):
        for _ in range(300):
            if not app._busy:
                return
            await asyncio.sleep(0.03)
        self.fail("TUI worker did not finish")

    async def test_sample_field_runs_three_real_processes_and_saves_statistics(self):
        marker = self.directory / "launches with spaces.txt"
        code = "import os,sys,time;open(sys.argv[1],'a').write('run\\n');print(sorted(os.sched_getaffinity(0)));print('[literal markup]');time.sleep(0.1)"
        command = shlex.join([sys.executable, "-c", code, str(marker)])
        app = LatencyLabApp(runner_path=str(RUNNER), results_dir=self.directory, sample=1)
        async with app.run_test(size=(140, 46)) as pilot:
            app.query_one("#command", Input).value = command
            app.query_one("#sample", Input).value = "3"
            cpu = max(os.sched_getaffinity(0))
            app.query_one("#cpu", Input).value = str(cpu)
            await pilot.click("#run")
            self.assertTrue(app.query_one("#run", Button).disabled)
            self.assertFalse(app.query_one("#stop", Button).disabled)
            await self.wait_done(app)
            self.assertEqual(marker.read_text().splitlines(), ["run"] * 3)
            self.assertEqual(len(app.outcomes), 3)
            self.assertTrue(all(not item.result.mock and item.result.status == "ok" for item in app.outcomes))
            self.assertTrue(all(item.result.cpu == cpu for item in app.outcomes))
            self.assertTrue(all(item.result.stdout.startswith(str([cpu])) for item in app.outcomes))
            self.assertEqual(len(list(self.directory.glob("run-*.json"))), 3)
            summary = json.loads(app.last_summary.read_text())
            self.assertEqual(summary["sample_requested"], 3)
            self.assertEqual(summary["statistics"]["elapsed_ns"]["n"], 3)
            self.assertEqual(app.query_one("#metrics", DataTable).row_count, 5)
            self.assertTrue(app.query_one("#status", Static).has_class("ok"))
            screenshot = os.environ.get("LATENCYLAB_SCREENSHOT")
            if screenshot:
                path = Path(screenshot)
                app.save_screenshot(filename=path.name, path=str(path.parent))

    async def test_cancel_stops_current_binary_and_the_series(self):
        marker = self.directory / "pid"
        code = "import os,time,sys;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(30)"
        app = LatencyLabApp(runner_path=str(RUNNER), results_dir=self.directory, runner_timeout=30)
        async with app.run_test(size=(140, 46)) as pilot:
            app.query_one("#command", Input).value = shlex.join([sys.executable, "-c", code, str(marker)])
            await pilot.click("#run")
            for _ in range(100):
                if marker.exists():
                    break
                await asyncio.sleep(0.03)
            self.assertTrue(marker.exists())
            await pilot.click("#stop")
            await self.wait_done(app)
            self.assertEqual(len(app.outcomes), 1)
            self.assertEqual(app.outcomes[0].result.status, "cancelled")
            self.assertEqual(json.loads(app.last_summary.read_text())["successful"], 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)

    async def test_invalid_sample_does_not_start_a_process(self):
        app = LatencyLabApp(runner_path=str(RUNNER), results_dir=self.directory)
        async with app.run_test(size=(140, 46)) as pilot:
            app.query_one("#command", Input).value = "/bin/true"
            app.query_one("#sample", Input).value = "0"
            await pilot.click("#run")
            self.assertFalse(app._busy)
            self.assertEqual(app.outcomes, [])
            self.assertTrue(app.query_one("#status", Static).has_class("error"))

    async def test_closing_app_stops_the_running_binary(self):
        marker = self.directory / "closing-pid"
        code = "import os,time,sys;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(30)"
        app = LatencyLabApp(runner_path=str(RUNNER), results_dir=self.directory, runner_timeout=30)
        async with app.run_test(size=(140, 46)) as pilot:
            app.query_one("#command", Input).value = shlex.join([sys.executable, "-c", code, str(marker)])
            await pilot.click("#run")
            for _ in range(100):
                if marker.exists():
                    break
                await asyncio.sleep(0.03)
            self.assertTrue(marker.exists())
        pid = int(marker.read_text())
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.03)
        else:
            self.fail("Closing TUI left its target running")

    async def test_failed_binary_is_shown_as_error_and_excluded_from_statistics(self):
        app = LatencyLabApp(runner_path=str(RUNNER), results_dir=self.directory)
        async with app.run_test(size=(140, 46)) as pilot:
            app.query_one("#command", Input).value = "/no-such-latencylab-program"
            await pilot.click("#run")
            await self.wait_done(app)
            self.assertEqual(len(app.outcomes), 1)
            self.assertEqual(app.outcomes[0].result.status, "launch_error")
            self.assertEqual(summarize(app.outcomes)["elapsed_ns"]["n"], 0)
            self.assertTrue(app.query_one("#status", Static).has_class("error"))

    async def test_standard_80_column_terminal_can_launch_a_binary(self):
        app = LatencyLabApp(runner_path=str(RUNNER), results_dir=self.directory, sample=1)
        async with app.run_test(size=(80, 24)) as pilot:
            app.query_one("#command", Input).value = "/bin/true"
            self.assertTrue(await pilot.click("#run"))
            await self.wait_done(app)
            self.assertEqual(len(app.outcomes), 1)
            self.assertEqual(app.outcomes[0].result.status, "ok")

    async def test_builtin_presets_launch_real_executables(self):
        app = LatencyLabApp(runner_path=str(RUNNER), results_dir=self.directory, sample=1)
        async with app.run_test(size=(140, 46)) as pilot:
            for name in PRESETS:
                with self.subTest(preset=name):
                    app.query_one("#demo-select", Select).value = name
                    await pilot.pause()
                    command = app.query_one("#command", Input).value
                    self.assertIn("demo_", command)
                    self.assertEqual(app.query_one("#sample", Input).value, "1")
                    await pilot.click("#run")
                    await self.wait_done(app)
                    self.assertEqual(len(app.outcomes), 1)
                    self.assertEqual(app.outcomes[0].result.status, "ok")
                    self.assertIn("checksum=", app.outcomes[0].result.stdout)

    async def test_keyboard_demo_selection_on_small_terminal(self):
        app = LatencyLabApp(runner_path=str(RUNNER), results_dir=self.directory, sample=1)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.click("#demo-select")
            await pilot.press("end", "enter")
            self.assertEqual(app.query_one("#demo-select", Select).value, "matrix-columns")
            self.assertTrue(app.query_one("#command", Input).value.endswith("256 3 columns"))
            await pilot.click("#run")
            await self.wait_done(app)
            self.assertEqual(app.outcomes[0].result.status, "ok")
            self.assertIn("order=ijk", app.outcomes[0].result.stdout)


if __name__ == "__main__":
    unittest.main()
