"""Synthetic fixtures test reasoning; they are never presented as host measurements."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tui.analysis import analyze, format_analysis
from tui.comparison import compare, format_comparison
from tui.runner import RunOutcome
from tui.schema import parse_result

ROOT = Path(__file__).resolve().parent.parent


def run(*, command="test", elapsed=100_000_000, cpu=0, mock=False, values=None,
        resources=None, running=100, enabled=100, status="ok"):
    data = {
        "schema_version": 1, "command": command, "status": status,
        "exit_code": 0 if status == "ok" else 1, "elapsed_ns": elapsed,
        "cpu": cpu, "kernel": "test-kernel", "scope": "target_main_thread_user_space",
        "mock": mock, "metrics": {},
    }
    for name, value in (values or {}).items():
        data["metrics"][name] = {"status": "available", "value": value,
                                 "time_enabled_ns": enabled, "time_running_ns": running}
    if resources:
        data["resources"] = {"source": "wait4", **resources}
    return RunOutcome(parse_result(data), data)


def series(**kwargs):
    return [run(**kwargs) for _ in range(3)]


def indicator(report, key):
    return next(i for i in report.indicators if i.key == key)


class QualityAndRulesTests(unittest.TestCase):
    def test_memory_hypothesis_has_evidence_alternatives_and_experiment(self):
        report = analyze(series(values={"cycles": 3e6, "instructions": 1e6, "cache_misses": 50_000,
                                        "stalled_cycles_backend": 1.5e6}))
        h = report.hypotheses[0]
        self.assertEqual(h.key, "memory")
        self.assertEqual(h.strength, "несколько согласованных признаков")
        self.assertGreaterEqual(h.weight, 2)
        self.assertTrue(h.alternatives and h.check)
        self.assertIn("наблюдения", format_analysis(report))
        self.assertNotIn("LLC", format_analysis(report))

    def test_cache_rate_only_is_a_limited_hypothesis_with_the_actual_evidence(self):
        report = analyze(series(values={"cache_misses": 100, "cache_references": 200}))
        h = report.hypotheses[0]
        self.assertEqual(h.key, "memory")
        self.assertIn("cache_misses/cache_references", h.evidence)
        self.assertEqual(h.strength, "предварительная")
        self.assertTrue(h.missing)

    def test_backend_stall_alone_does_not_identify_memory(self):
        report = analyze(series(values={"cycles": 1e6, "stalled_cycles_backend": 500_000}))
        self.assertEqual([h.key for h in report.hypotheses], ["backend"])
        self.assertIn("не определён", report.verdict)
        self.assertEqual(report.hypotheses[0].strength, "предварительная")

    def test_ratios_use_matching_runs_and_zero_is_not_missing(self):
        report = analyze([run(values={"cycles": 100, "instructions": 100}),
                          run(values={"cycles": 900, "instructions": 300})])
        self.assertEqual(indicator(report, "cpi").value, 2)  # not (100+900)/(100+300)
        report = analyze([run(values={"cycles": 100}), run(values={"instructions": 100})])
        self.assertIsNone(indicator(report, "cpi").value)
        report = analyze(series(values={"cache_misses": 0, "instructions": 100}))
        self.assertEqual(indicator(report, "miss_per_1k_ins").value, 0)
        self.assertEqual(report.hypotheses, [])

    def test_bad_or_unknown_perf_timing_is_not_evidence(self):
        for timing in ({"running": 20}, {"running": 0}, {"enabled": 0}, {"running": 101}):
            with self.subTest(timing=timing):
                report = analyze(series(values={"cycles": 1000, "instructions": 1}, **timing))
                self.assertIsNone(indicator(report, "cpi").value)
                self.assertTrue(any("timing" in note for note in report.quality.limitations))

    def test_short_noisy_or_synthetic_series_does_not_get_strong_support(self):
        for samples in (series(elapsed=1, values={"cycles": 1000, "instructions": 1, "cache_misses": 10}),
                        series(mock=True, values={"cycles": 1000, "instructions": 1, "cache_misses": 10}),
                        [run(elapsed=t, values={"cycles": 1000, "instructions": 1, "cache_misses": 10})
                         for t in [10_000_000, 100_000_000, 1_000_000_000]]):
            report = analyze(samples)
            self.assertFalse(report.quality.repeatable)
            self.assertTrue(all(h.strength == "предварительная" for h in report.hypotheses))

    def test_mixed_commands_or_cpus_do_not_produce_a_pooled_diagnosis(self):
        for other in (run(command="different"), run(cpu=1)):
            report = analyze([run(), other])
            self.assertFalse(report.quality.comparable)
            self.assertEqual(report.hypotheses, [])

    def test_failed_runs_are_excluded(self):
        report = analyze(series() + [run(status="timeout", values={"cycles": 1e9})])
        self.assertEqual(report.quality.successful, 3)
        self.assertEqual(report.quality.excluded, 1)

    def test_resource_hypothesis_works_without_hardware(self):
        report = analyze(series(resources={"user_cpu_us": 30_000, "system_cpu_us": 40_000,
                                          "minor_page_faults": 10_000}))
        self.assertEqual(report.hypotheses[0].key, "system")
        self.assertNotIn("аппаратные счётчики недоступны", report.verdict)

    def test_high_cpi_alone_does_not_name_a_cause(self):
        report = analyze(series(values={"cycles": 1000, "instructions": 10}))
        self.assertEqual(report.hypotheses, [])

    def test_task_clock_is_not_replaced_by_process_wide_cpu_time(self):
        report = analyze(series(resources={"user_cpu_us": 10_000, "system_cpu_us": 0}))
        self.assertIsNone(indicator(report, "cpu_util").value)
        report = analyze(series(values={"task_clock": 30_000_000}))
        self.assertEqual(report.hypotheses[0].key, "off_cpu")
        self.assertGreaterEqual(len(report.hypotheses[0].alternatives), 2)

    def test_unphysical_rate_is_not_a_percentage(self):
        report = analyze(series(values={"cache_misses": 1000, "cache_references": 10}))
        self.assertIsNone(indicator(report, "cache_miss_rate").value)
        self.assertEqual(report.hypotheses, [])

    def test_branch_and_frontend_rules_keep_their_distinct_alternatives(self):
        samples = series(values={"cycles": 3e6, "instructions": 1e6,
                                 "branches": 1000, "branch_misses": 100,
                                 "stalled_cycles_frontend": 1.5e6})
        hypotheses = {h.key: h for h in analyze(samples).hypotheses}
        self.assertEqual(set(hypotheses), {"branches", "frontend"})
        self.assertIn("branchless", hypotheses["branches"].check)
        self.assertGreaterEqual(len(hypotheses["frontend"].alternatives), 2)

    def test_partial_event_coverage_cannot_get_strong_support(self):
        samples = series(values={"cycles": 3e6, "instructions": 1e6})
        samples[0] = run(values={"cycles": 3e6, "instructions": 1e6, "cache_misses": 100_000})
        report = analyze(samples)
        self.assertEqual(indicator(report, "miss_per_1k_ins").n, 1)
        self.assertEqual(report.hypotheses[0].strength, "предварительная")

    def test_single_outlier_does_not_trigger_a_series_hypothesis(self):
        report = analyze([run(values={"branch_misses": x, "branches": 1000}) for x in [1, 1, 1, 1, 500]])
        self.assertEqual(report.hypotheses, [])


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.base = series(values={"cycles": 1e6, "instructions": 1e6, "cache_misses": 10_000})
        self.variant = series(command="variant", elapsed=200_000_000,
                              values={"cycles": 2e6, "instructions": 1.01e6, "cache_misses": 50_000})

    def test_equivalent_stable_work_and_matching_changes_support_a_hypothesis(self):
        report = compare(self.base, self.variant, equivalent_work=True)
        self.assertTrue(report.comparable)
        self.assertIn("локальности", report.supported_hypothesis)
        self.assertIn("не доказывает", report.supported_hypothesis)
        self.assertEqual(report.changes[0].ratio, 2)

    def test_equivalence_is_not_inferred_from_time_or_checksum(self):
        report = compare(self.base, self.variant)
        self.assertTrue(report.comparable)
        self.assertIsNone(report.supported_hypothesis)

    def test_changed_work_unknown_hardware_or_noise_blocks_causal_support(self):
        for candidate in (series(command="variant", elapsed=200_000_000,
                                 values={"instructions": 5e6, "cache_misses": 1e6}),
                          series(elapsed=200_000_000), self.variant[:1],
                          series(elapsed=105_000_000, values={"instructions": 1e6, "cache_misses": 50_000})):
            self.assertIsNone(compare(self.base, candidate, equivalent_work=True).supported_hypothesis)

    def test_cpu_or_scope_mismatch_and_mock_are_not_comparable(self):
        for kwargs in ({"cpu": 1}, {"cpu": None}, {"mock": True}):
            report = compare(self.base, series(**kwargs), equivalent_work=True)
            self.assertFalse(report.comparable)
            self.assertEqual(report.changes, [])
        bad = copy.deepcopy(self.variant)
        for o in bad:
            o.raw["scope"] = "other"
        self.assertFalse(compare(self.base, bad).comparable)

    def test_missing_metrics_still_allow_descriptive_wall_time(self):
        report = compare(series(), series(command="variant", elapsed=200_000_000), equivalent_work=True)
        self.assertTrue(report.comparable)
        self.assertEqual(len(report.changes), 1)
        self.assertIsNone(report.supported_hypothesis)
        self.assertIn("не установлена", format_comparison(report))

    def test_reduced_branch_misses_can_support_an_improvement(self):
        base = series(values={"cycles": 3e6, "instructions": 1e6,
                              "branches": 1000, "branch_misses": 100})
        candidate = series(command="branchless", elapsed=50_000_000,
                           values={"cycles": 1e6, "instructions": 1e6,
                                   "branches": 1000, "branch_misses": 10})
        report = compare(base, candidate, equivalent_work=True)
        self.assertIn("ветвлений", report.supported_hypothesis)
        self.assertLess(report.changes[0].ratio, 1)

    def test_invalid_branch_rate_does_not_support_a_cause(self):
        base = series(values={"instructions": 1e6, "branches": 10, "branch_misses": 100})
        candidate = series(command="candidate", elapsed=200_000_000,
                           values={"instructions": 1e6, "branches": 10, "branch_misses": 1000})
        report = compare(base, candidate, equivalent_work=True)
        self.assertIsNone(report.supported_hypothesis)
        self.assertNotIn("branch_miss_rate", [c.key for c in report.changes])

    def test_zero_baseline_does_not_divide_by_zero(self):
        base = series(values={"instructions": 1e6, "cache_misses": 0})
        report = compare(base, self.variant, equivalent_work=True)
        change = next(c for c in report.changes if c.key == "miss_per_1k_ins")
        self.assertIsNone(change.ratio)
        self.assertIsNone(report.supported_hypothesis)


class CLITests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts/analyze.py"), *map(str, args)],
                              capture_output=True, text=True, timeout=10)

    def test_glob_does_not_pool_different_commands(self):
        with tempfile.TemporaryDirectory() as d:
            for i, command in enumerate(["one", "two"]):
                Path(d, f"{i}.json").write_text(json.dumps(run(command=command).raw))
            result = self.invoke(str(Path(d, "*.json")), "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["groups"]), 2)

    def test_cli_serializes_comparison_and_handles_bad_input(self):
        with tempfile.TemporaryDirectory() as d:
            base, variant = Path(d, "base.json"), Path(d, "variant.json")
            for path, command, elapsed in ((base, "base", 100_000_000), (variant, "variant", 200_000_000)):
                path.write_text(json.dumps({"kind": "sample_series", "runs": [o.raw for o in series(command=command, elapsed=elapsed)]}))
            result = self.invoke(variant, "--baseline", base, "--equivalent-work", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["comparable"])
            variant.write_text('[]')
            self.assertEqual(self.invoke(variant).returncode, 1)
            variant.write_text('{"kind":"sample_series","runs":null}')
            self.assertEqual(self.invoke(variant).returncode, 1)


if __name__ == "__main__":
    unittest.main()
