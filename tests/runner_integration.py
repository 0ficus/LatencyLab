"""Real Linux processes, affinity, output capture and MetricsCollector integration."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

RUNNER = Path(sys.argv.pop(1)).resolve() if len(sys.argv) > 1 else Path("build/runner").resolve()


class RunnerIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.cpu = max(os.sched_getaffinity(0))

    def run_target(self, code, *args, timeout_ms=2000):
        proc = subprocess.run(
            [str(RUNNER), "--cpu", str(self.cpu), "--timeout-ms", str(timeout_ms),
             "--", sys.executable, "-c", code, *args],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_target_is_pinned_and_python_caller_is_not(self):
        before = os.sched_getaffinity(0)
        result = self.run_target("import os,json;print(json.dumps(sorted(os.sched_getaffinity(0))))")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(json.loads(result["stdout"]), [self.cpu])
        self.assertEqual(os.sched_getaffinity(0), before)
        self.assertTrue(result["affinity_applied"])

    def test_spaces_and_shell_characters_are_literal(self):
        argument = 'literal $(not-a-command); [red] "quoted"'
        result = self.run_target("import sys;print(sys.argv[1]);print('stderr',file=sys.stderr)", argument)
        self.assertEqual(result["stdout"].strip(), argument)
        self.assertEqual(result["stderr"].strip(), "stderr")

    def test_large_output_is_capped_without_deadlock(self):
        result = self.run_target("import sys;sys.stdout.write('o'*200000);sys.stderr.write('e'*200000)")
        self.assertEqual(result["stdout"], "o" * 65536)
        self.assertEqual(result["stderr"], "e" * 65536)
        self.assertTrue(result["stdout_truncated"])
        self.assertTrue(result["stderr_truncated"])

    def test_failure_and_missing_binary_are_distinct(self):
        result = self.run_target("raise SystemExit(7)")
        self.assertEqual((result["status"], result["exit_code"]), ("target_error", 7))
        proc = subprocess.run([str(RUNNER), "--", "/does-not-exist-latencylab"], capture_output=True, text=True, timeout=5)
        result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "launch_error")
        self.assertIn("execvp", result["error"])

    def test_timeout_kills_target(self):
        started = time.monotonic()
        result = self.run_target("import time;time.sleep(30)", timeout_ms=80)
        self.assertEqual(result["status"], "timeout")
        self.assertLess(time.monotonic() - started, 3)

    def test_disallowed_cpu_is_rejected_before_launch(self):
        disallowed = next((cpu for cpu in range(1024) if cpu not in os.sched_getaffinity(0)), None)
        if disallowed is None:
            self.skipTest("All supported CPU IDs are allowed")
        proc = subprocess.run([str(RUNNER), "--cpu", str(disallowed), "--", "/bin/true"],
                              capture_output=True, text=True, timeout=3)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("affinity", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_signal_cancellation_reaps_target(self):
        marker = self.directory / "pid"
        code = "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(30)"
        proc = subprocess.Popen(
            [str(RUNNER), "--timeout-ms", "30000", "--", sys.executable, "-c", code, str(marker)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(marker.exists())
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout)["status"], "cancelled")
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_metrics_and_real_resource_counters_have_valid_contract(self):
        result = self.run_target("x=bytearray(8*1024*1024);print(sum(x))")
        self.assertGreater(result["elapsed_ns"], 0)
        self.assertGreater(result["resources"]["minor_page_faults"], 0)
        self.assertEqual(result["resources"]["source"], "wait4")
        self.assertEqual(set(result["metrics"]), {
            "cycles", "instructions", "cache_misses", "cache_references", "branch_misses",
            "branches", "stalled_cycles_backend", "stalled_cycles_frontend", "task_clock",
            "context_switches", "cpu_migrations",
        })
        for metric in result["metrics"].values():
            if metric["status"] == "unavailable":
                self.assertIsNone(metric["value"])
                self.assertTrue(metric["reason"])
            else:
                self.assertGreater(metric["time_running_ns"], 0)
                expected = metric["raw_value"] * metric["time_enabled_ns"] / metric["time_running_ns"]
                self.assertAlmostEqual(metric["value"], expected, delta=max(1e-6, abs(expected) * 1e-12))

    def test_hardware_cycles_when_pmu_is_accessible(self):
        result = self.run_target("print(sum(range(1000000)))")
        metric = result["metrics"]["cycles"]
        if metric["status"] != "available":
            self.skipTest("Host PMU is unavailable: " + metric["reason"])
        self.assertGreater(metric["value"], 0)


if __name__ == "__main__":
    unittest.main()
