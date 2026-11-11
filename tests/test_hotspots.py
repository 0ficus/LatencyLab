"""Parser fixtures, subprocess boundary tests and an optional real perf smoke test.

The fake perf below is a test double, never a source of UI demo measurements.
"""
import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from textual.widgets import Button, DataTable, Input, Static

from tui.app import LatencyLabApp
from tui.hotspot_screen import HotspotScreen
from tui.hotspots import parse_report, source_preview
from tui.profiler import ProfileError, ProfileResult, profile


def report_row(samples, symbol="[.] work", source="??:0", dso="program"):
    return f"1.00%\t{samples}\t{symbol}\t{source}\t{dso}\n"


class ReportTests(unittest.TestCase):
    def test_top_five_keeps_full_denominator_and_unknown(self):
        text = "# header\n" + report_row(4, "[.] work", "/tmp/a.cpp:12") * 2
        text += report_row(2, "[unknown]")
        text += "".join(report_row(1, f"[.] f{i}") for i in range(8))
        rows, total = parse_report(text)
        self.assertEqual(total, 18)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0].symbol, "work")
        self.assertEqual(rows[0].samples, 8)
        self.assertAlmostEqual(rows[0].share, 8 / 18 * 100)
        self.assertEqual(rows[1].symbol, "[unknown]")
        self.assertLess(sum(row.share for row in rows), 100)

    def test_empty_report_is_not_a_hotspot(self):
        self.assertEqual(parse_report("# no samples\n"), ([], 0))
        self.assertEqual(parse_report(report_row(0)), ([], 0))
        with self.assertRaises(ValueError):
            parse_report("50% 5 [.] work file:12")

    def test_source_exact_path_and_edges(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source with spaces.cpp"
            path.write_text("".join(f"line {i}\n" for i in range(1, 16)))
            preview = source_preview(f"{path}:8 (discriminator 2)")
            self.assertEqual((preview.start, preview.line), (6, 8))
            self.assertEqual(preview.code, "".join(f"line {i}\n" for i in range(6, 11)))
            self.assertEqual(source_preview(f"{path}:1").start, 1)
            self.assertIsNone(source_preview(f"{path}:99"))
            path.write_text("")
            self.assertIsNone(source_preview(f"{path}:1"))
        self.assertIsNone(source_preview("??:0"))
        self.assertIsNone(source_preview("/missing/test.cpp:8"))


FAKE_PERF = '''import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
with open(os.environ["LL_TEST_ARGV"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[0] == "record":
    if os.environ.get("LL_TEST_DENIED"):
        print("perf_event_open: Permission denied", file=sys.stderr)
        sys.exit(255)
    pathlib.Path(args[args.index("-o") + 1]).write_bytes(b"test fixture, not perf data")
    sys.exit(subprocess.call(args[args.index("--") + 1:]))
else:
    sys.stdout.write(pathlib.Path(os.environ["LL_TEST_REPORT"]).read_text())
'''


@unittest.skipUnless(sys.platform == "linux" and shutil.which("taskset"), "Linux/taskset required")
class ProfilerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "perf"
        fake.write_text(f"#!{sys.executable}\n" + FAKE_PERF)
        fake.chmod(0o755)
        self.log = self.root / "argv.jsonl"
        self.report = self.root / "fixture.txt"
        self.report.write_text(report_row(30))
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "LL_TEST_ARGV": str(self.log),
               "LL_TEST_REPORT": str(self.report)}
        self.patch = patch.dict(os.environ, env)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def command(self, code, *args):
        return shlex.join([sys.executable, "-c", code, *args])

    def test_pins_target_preserves_arguments_and_separates_results(self):
        literal = "spaces ; $(not-a-command) [literal]"
        cpu = max(os.sched_getaffinity(0))
        cmd = self.command("import json, os, sys; print(json.dumps([list(os.sched_getaffinity(0)), sys.argv[1]]))", literal)
        result = profile(cmd, "cache", cpu, self.root)
        output = json.loads((result.directory / "target.stdout.txt").read_text())
        self.assertEqual(output, [[cpu], literal])
        self.assertEqual(result.total_samples, 30)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertIn("--no-inherit", calls[0])
        self.assertEqual(calls[0][calls[0].index("-e") + 1], "cache-misses:u")
        self.assertEqual(calls[0][calls[0].index("-c") + 1], "1000")
        metadata = json.loads((result.directory / "hotspots.json").read_text())
        self.assertEqual(metadata["status"], "ok")
        self.assertEqual(metadata["total_samples"], 30)
        self.assertEqual(metadata["cpu"], cpu)
        self.assertEqual(metadata["argv"][-1], literal)

    def test_denied_does_not_report_or_fallback(self):
        with patch.dict(os.environ, {"LL_TEST_DENIED": "1"}):
            with self.assertRaisesRegex(ProfileError, "Permission denied"):
                profile(self.command("pass"), "cpu", None, self.root)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)
        path = next(self.root.glob("profile-*/hotspots.json"))
        self.assertEqual(json.loads(path.read_text())["status"], "error")

    def test_target_failure_does_not_produce_hotspots(self):
        with self.assertRaisesRegex(ProfileError, "код 7"):
            profile(self.command("raise SystemExit(7)"), "cpu", None, self.root)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_timeout_and_cancel_reap_target(self):
        for should_cancel in (False, True):
            with self.subTest(cancel=should_cancel):
                pid_path = self.root / f"pid-{should_cancel}.txt"
                cmd = self.command("import os, pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)", str(pid_path))
                cancel = threading.Event()
                errors = []

                def run():
                    try:
                        profile(cmd, "cpu", None, self.root, 5 if should_cancel else 0.5, cancel)
                    except ProfileError as exc:
                        errors.append(str(exc))

                worker = threading.Thread(target=run)
                worker.start()
                deadline = time.monotonic() + 3
                while not pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(pid_path.exists())
                if should_cancel:
                    cancel.set()
                worker.join(4)
                self.assertFalse(worker.is_alive())
                self.assertIn("остановлено" if should_cancel else "Таймаут", errors[0])
                pid = int(pid_path.read_text())
                # A dead child can briefly remain a zombie until the container init reaps it.
                status = Path(f"/proc/{pid}/stat")
                self.assertTrue(not status.exists() or status.read_text().split()[2] == "Z")

    def test_invalid_cpu_never_executes_target(self):
        with self.assertRaisesRegex(ProfileError, "недоступен"):
            profile(self.command("pass"), "cpu", max(os.sched_getaffinity(0)) + 1, self.root)
        self.assertFalse(self.log.exists())


class HotspotScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_profile_select_source_and_keep_sample_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "test.cpp"
            source.write_text("first\nsecond\nthird\n")
            rows, count = parse_report(report_row(30, source=f"{source}:2") + report_row(10, "[.] unknown"))
            result = ProfileResult("cpu", 2, rows, count, root, "fixture diagnostics")
            app = LatencyLabApp(sample=7, results_dir=root)
            with patch("tui.hotspot_screen.profile", return_value=result) as profiler:
                async with app.run_test(size=(80, 24)) as pilot:
                    app.query_one("#command", Input).value = "./example --size 42"
                    app.query_one("#cpu", Input).value = "2"
                    await pilot.click("#hotspots")
                    await pilot.pause()
                    self.assertIsInstance(app.screen, HotspotScreen)
                    screen = app.screen
                    await pilot.click("#profile-cpu")
                    for _ in range(100):
                        if screen.result is not None and not screen._busy:
                            break
                        await asyncio.sleep(0.01)
                    self.assertEqual(screen.query_one("#hotspot-table", DataTable).row_count, 2)
                    self.assertIn("test.cpp:2", str(screen.query_one("#source-title", Static).render()))
                    await pilot.press("down")
                    await pilot.pause()
                    self.assertIn("Исходник недоступен", str(screen.query_one("#source-code", Static).render()))
                    profiler.assert_called_once()
                    self.assertEqual(profiler.call_args.args[:4], ("./example --size 42", "cpu", 2, root))
                    await pilot.press("escape")
                    await pilot.pause()
                    self.assertEqual(app.query_one("#sample", Input).value, "7")
                    self.assertEqual(app.outcomes, [])

    async def test_error_and_close_cancel_worker(self):
        started = threading.Event()
        stopped = threading.Event()

        def slow_profile(command, mode, cpu, results_dir, timeout, cancel):
            started.set()
            if cancel.wait(5):
                stopped.set()
                raise ProfileError("Профилирование остановлено.")
            raise ProfileError("test timeout")

        app = LatencyLabApp()
        with patch("tui.hotspot_screen.profile", side_effect=slow_profile):
            async with app.run_test(size=(80, 24)) as pilot:
                app.query_one("#command", Input).value = "./example"
                await pilot.click("#hotspots")
                await pilot.click("#profile-cache")
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(started.is_set())
                await pilot.press("escape")
                self.assertTrue(await asyncio.to_thread(stopped.wait, 2))

    async def test_permission_error_shown_in_screen(self):
        app = LatencyLabApp()
        with patch("tui.hotspot_screen.profile", side_effect=ProfileError("Permission denied")):
            async with app.run_test() as pilot:
                app.query_one("#command", Input).value = "./example"
                await pilot.click("#hotspots")
                await pilot.click("#profile-cpu")
                await pilot.pause()
                self.assertIn("Permission denied", str(app.screen.query_one("#hotspot-status", Static).render()))
                self.assertFalse(app.screen.query_one("#profile-cpu", Button).disabled)


@unittest.skipUnless(sys.platform == "linux" and shutil.which("perf") and shutil.which("g++"),
                     "Linux, perf and g++ required")
class RealPerfTests(unittest.TestCase):
    def test_real_cpu_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.cpp"
            source.write_text('''#include <chrono>
#include <iostream>
#include <sched.h>
__attribute__((noinline)) void burn() {
    volatile unsigned long value = 1;
    auto end = std::chrono::steady_clock::now() + std::chrono::seconds(1);
    while (std::chrono::steady_clock::now() < end)
        for (int i = 0; i < 10000; ++i) value = value * 1664525 + 1013904223;
}
int main() { std::cout << sched_getcpu() << std::endl; burn(); }
''')
            binary = root / "fixture"
            subprocess.run(["g++", "-g", "-O1", str(source), "-o", str(binary)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                result = profile(shlex.join([str(binary)]), "cpu", None, root, timeout=5)
            except ProfileError as exc:
                if any(reason in str(exc).lower() for reason in (
                        "permission", "operation not permitted", "perf_event_paranoid",
                        "access to performance", "perf not found for kernel", "not supported")):
                    self.skipTest(f"perf unavailable: {exc}")
                raise
            self.assertGreater(result.total_samples, 0)
            self.assertEqual(int((result.directory / "target.stdout.txt").read_text()), result.cpu)
            self.assertTrue(any(str(source) in item.location for item in result.hotspots), result.hotspots)


if __name__ == "__main__":
    unittest.main()
