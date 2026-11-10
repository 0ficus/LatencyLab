"""Textual TUI для Latency Lab.

Раскладка:
  - controls: поле команды, поле CPU, кнопки «Запустить»/«Очистить», статус;
  - results: сводка, таблица метрик, ресурсы, захваченный stdout/stderr.

TUI сам целевую программу не запускает: он делегирует это runner'у (в проде —
C++ бинарник, в разработке — мок) и лишь отрисовывает его JSON.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static
from rich.text import Text

from .explanations import METRIC_EXPLANATIONS
from .runner import DEFAULT_RUNNER, PROJECT_ROOT, RunOutcome, RunnerError, run_runner
from .schema import RunnerResult
from .statistics import summarize

STDOUT_PREVIEW = 4000  # сколько символов вывода программы показываем в TUI


def format_ns(ns: int) -> str:
    if ns < 1_000:
        return f"{ns} нс"
    if ns < 1_000_000:
        return f"{ns / 1e3:.2f} мкс"
    if ns < 1_000_000_000:
        return f"{ns / 1e6:.2f} мс"
    return f"{ns / 1e9:.3f} с"


def format_value(value: Optional[float]) -> str:
    if value is None:
        return "—"
    try:
        numeric = float(value)
        return f"{numeric:,.0f}" if numeric.is_integer() else f"{numeric:,.2f}"
    except (TypeError, ValueError):
        return str(value)


class LatencyLabApp(App):
    TITLE = "Latency Lab"
    SUB_TITLE = "запуск программы и сбор метрик"

    CSS = """
    #controls { height: auto; padding: 1 2; }
    #controls Input { margin-bottom: 1; }
    #row { height: auto; }
    #cpu-label { width: 5; height: 3; content-align: center middle; }
    #cpu { width: 15; margin-right: 1; }
    #sample-label { width: 7; height: 3; content-align: center middle; }
    #sample { width: 10; margin-right: 1; }
    #run { min-width: 14; width: 14; }
    #stop { min-width: 8; width: 8; }
    #clear { min-width: 10; width: 10; }
    #status { height: auto; margin-top: 1; color: $text-muted; }
    #status.error { color: $error; }
    #status.running { color: $warning; }
    #status.ok { color: $success; }
    #results { padding: 1 2; }
    #summary { height: auto; margin-bottom: 1; color: $text-muted; }
    #metrics { height: auto; max-height: 16; margin-bottom: 1; }
    #resources { height: auto; margin-bottom: 1; color: $text-muted; }
    #stdout, #stderr { height: auto; color: $text-muted; }
    .section-title { text-style: bold; height: auto; margin-top: 1; }
    """

    def __init__(
        self,
        runner_path: Optional[str] = None,
        results_dir: Path = Path("results"),
        demo: bool = False,
        runner_timeout: Optional[float] = 10,
        sample: int = 5,
    ) -> None:
        super().__init__()
        self.runner_path = runner_path or os.environ.get("LATENCYLAB_RUNNER") or DEFAULT_RUNNER
        self.results_dir = results_dir
        self.demo = demo
        self.runner_timeout = runner_timeout
        self.sample = sample
        self.demo_command = shlex.join([str(PROJECT_ROOT / "build" / "demo_target")])
        if sys.platform != "linux" or not (PROJECT_ROOT / "build" / "demo_target").is_file():
            self.demo_command = shlex.join([sys.executable, "-c", "print(sum(i*i for i in range(2000000)))"])
        self.last_saved: Optional[Path] = None
        self._run_index = 0
        self.outcomes: list[RunOutcome] = []
        self.last_summary: Optional[Path] = None
        self._busy = False
        self._cancel = threading.Event()

    # --- построение интерфейса ------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="controls"):
            yield Label("Команда для запуска", classes="section-title")
            yield Input(placeholder="python demo.py --size 1024", id="command")
            with Horizontal(id="row"):
                yield Label("CPU", id="cpu-label")
                yield Input(placeholder="CPU (пусто = авто)", id="cpu")
                yield Label("sample", id="sample-label")
                yield Input(value=str(self.sample), placeholder="1..1000", id="sample")
                yield Button("▶ Запустить", id="run", variant="primary")
                yield Button("Стоп", id="stop", disabled=True)
                yield Button("Очистить", id="clear")
            yield Static("Готов к работе.", id="status", markup=False)
        with VerticalScroll(id="results"):
            yield Static("", id="summary", markup=False)
            yield Label("Метрики", classes="section-title")
            yield DataTable(id="metrics")
            yield Static("", id="resources")
            yield Label("Вывод программы (stdout)", classes="section-title")
            yield Static("", id="stdout", markup=False)
            yield Label("Ошибки программы (stderr)", classes="section-title")
            yield Static("", id="stderr", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#metrics", DataTable)
        table.add_columns("Метрика", "Среднее", "Мин", "Макс", "n", "Пояснение")
        if self.demo:
            self.query_one("#command", Input).value = self.demo_command

    # --- события --------------------------------------------------------------

    @on(Input.Submitted, "#command")
    def _submit_command(self, event: Input.Submitted) -> None:
        self._start_run()

    @on(Button.Pressed, "#run")
    def _press_run(self, event: Button.Pressed) -> None:
        self._start_run()

    @on(Button.Pressed, "#clear")
    def _press_clear(self, event: Button.Pressed) -> None:
        if self._busy:
            return
        self.query_one("#command", Input).value = ""
        self.query_one("#cpu", Input).value = ""
        self.query_one("#sample", Input).value = str(self.sample)
        self._clear_results()
        self._set_status("Готов к работе.")

    # --- запуск ---------------------------------------------------------------

    @on(Button.Pressed, "#stop")
    def _press_stop(self, event: Button.Pressed) -> None:
        self._cancel.set()
        self._set_status("Останавливаем текущий запуск…", running=True)

    def on_unmount(self) -> None:
        self._cancel.set()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for selector in ("#command", "#cpu", "#sample", "#run", "#clear"):
            self.query_one(selector).disabled = busy
        self.query_one("#stop", Button).disabled = not busy

    def _start_run(self) -> None:
        if self._busy:
            return
        command = self.query_one("#command", Input).value.strip()
        if not command:
            self._set_status("Введите команду.", error=True)
            return

        cpu_text = self.query_one("#cpu", Input).value.strip()
        cpu: Optional[int] = None
        if cpu_text:
            if not cpu_text.isdigit() or len(cpu_text) > 4 or int(cpu_text) > 1023:
                self._set_status("CPU должен быть целым числом или пустым.", error=True)
                return
            cpu = int(cpu_text)

        sample_text = self.query_one("#sample", Input).value.strip()
        if not sample_text.isdigit() or len(sample_text) > 4 or not 1 <= int(sample_text) <= 1000:
            self._set_status("sample — число запусков от 1 до 1000.", error=True)
            return

        self._set_status("Выполняется…", running=True)
        self._clear_results()
        self._cancel = threading.Event()
        self._set_busy(True)
        self._run(command, cpu, int(sample_text))

    @work(exclusive=True, group="run")
    async def _run(self, command: str, cpu: Optional[int], sample: int) -> None:
        error = None
        try:
            for index in range(1, sample + 1):
                if self._cancel.is_set():
                    break
                self._set_status(f"Запуск {index}/{sample}…", running=True)
                outcome = await asyncio.to_thread(
                    run_runner, command, cpu, self.runner_path, self.runner_timeout, self._cancel
                )
                self.outcomes.append(outcome)
                if cpu is None:
                    cpu = outcome.result.cpu
                self._show_result(outcome)
                self._save_result(outcome)
                if outcome.result.status != "ok" or outcome.result.exit_code != 0:
                    error = outcome.result.error or f"{outcome.result.status}, код {outcome.result.exit_code}"
                    break
            if self.outcomes:
                self.last_summary = self._save_summary(command, sample)
            successful = sum(item.result.status == "ok" and item.result.exit_code == 0 for item in self.outcomes)
            if self._cancel.is_set():
                self._set_status(f"Остановлено. Успешных запусков: {successful}/{sample}.")
            elif error:
                self._set_status(f"Ошибка: {error}. Успешных запусков: {successful}/{sample}.", error=True)
            else:
                self._set_status(f"Готово: {successful}/{sample}. Сводка: {self.last_summary}", ok=True)
        except (RunnerError, OSError) as exc:
            if self.outcomes and self.last_summary is None:
                try:
                    self.last_summary = self._save_summary(command, sample)
                except OSError:
                    pass  # Original write/runner error is shown below; raw runs may already be saved.
            self._set_status(str(exc), error=True)
        except asyncio.CancelledError:
            self._cancel.set()
            raise
        finally:
            self._busy = False
            if self.is_running:
                self._set_busy(False)

    # --- отрисовка результата -------------------------------------------------

    def _show_result(self, outcome: RunOutcome) -> None:
        result = outcome.result
        self._render_summary(result)
        self._render_metrics(result)
        self._render_resources(result)
        self._render_output(result)

    def _render_summary(self, result: RunnerResult) -> None:
        bits = [
            f"Команда: {result.command}",
            f"Выход: {result.exit_code}",
            f"Время: {format_ns(result.elapsed_ns)}",
        ]
        if result.cpu is not None:
            bits.append(f"CPU: {result.cpu}")
        bits.append(f"Успешно: {sum(item.result.status == 'ok' and item.result.exit_code == 0 for item in self.outcomes)}/{len(self.outcomes)}")
        if result.mock:
            bits.append("⚠️ СИНТЕТИЧЕСКИЕ ДАННЫЕ (мок)")
        self.query_one("#summary", Static).update("  •  ".join(bits))

    def _render_metrics(self, result: RunnerResult) -> None:
        table = self.query_one("#metrics", DataTable)
        table.clear()
        for name, row in summarize(self.outcomes).items():
            def formatted(value):
                if value is None:
                    return "—"
                return format_ns(round(value)) if row["unit"] == "ns" else format_value(value)
            explanation = METRIC_EXPLANATIONS.get("elapsed" if name == "elapsed_ns" else name, "")
            metric = result.metrics.get(name)
            if row["n"] == 0 and metric and metric.reason:
                explanation += " " + str(metric.reason)
            table.add_row(
                name, formatted(row["mean"]), formatted(row["min"]), formatted(row["max"]),
                str(row["n"]), Text(explanation),
            )

    def _render_resources(self, result: RunnerResult) -> None:
        res = result.resources
        if res is None:
            self.query_one("#resources", Static).update("")
            return
        parts = []
        if res.user_cpu_us is not None:
            parts.append(f"CPU user: {res.user_cpu_us / 1e3:.1f} мс")
        if res.system_cpu_us is not None:
            parts.append(f"CPU system: {res.system_cpu_us / 1e3:.1f} мс")
        if res.minor_page_faults is not None:
            parts.append(f"minor faults: {res.minor_page_faults}")
        if res.major_page_faults is not None:
            parts.append(f"major faults: {res.major_page_faults}")
        self.query_one("#resources", Static).update("Последний запуск: " + "  •  ".join(parts) if parts else "")

    def _render_output(self, result: RunnerResult) -> None:
        stdout = result.stdout[:STDOUT_PREVIEW]
        stderr = result.stderr[:STDOUT_PREVIEW]
        if result.stdout_truncated or len(result.stdout) > STDOUT_PREVIEW:
            stdout += "\n… (обрезано)"
        if result.stderr_truncated or len(result.stderr) > STDOUT_PREVIEW:
            stderr += "\n… (обрезано)"
        self.query_one("#stdout", Static).update(stdout or "(пусто)")
        self.query_one("#stderr", Static).update(stderr or "(пусто)")

    def _save_result(self, outcome: RunOutcome) -> Path:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._run_index += 1
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        path = self.results_dir / f"run-{stamp}-{self._run_index:03d}-{uuid.uuid4().hex[:6]}.json"
        path.write_text(json.dumps(outcome.raw, ensure_ascii=False, indent=2), encoding="utf-8")
        self.last_saved = path
        return path

    def _save_summary(self, command: str, sample: int) -> Path:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        path = self.results_dir / f"series-{datetime.now():%Y-%m-%d-%H%M%S}-{uuid.uuid4().hex[:6]}.json"
        data = {
            "schema_version": 1, "kind": "sample_series", "command": command,
            "sample_requested": sample, "completed": len(self.outcomes),
            "successful": sum(item.result.status == "ok" and item.result.exit_code == 0 for item in self.outcomes),
            "cancelled": self._cancel.is_set(), "statistics": summarize(self.outcomes),
            "runs": [item.raw for item in self.outcomes],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    # --- вспомогательное ------------------------------------------------------

    def _clear_results(self) -> None:
        self.outcomes = []
        self.last_summary = None
        self.query_one("#summary", Static).update("")
        self.query_one("#metrics", DataTable).clear()
        self.query_one("#resources", Static).update("")
        self.query_one("#stdout", Static).update("")
        self.query_one("#stderr", Static).update("")

    def _set_status(
        self,
        text: str,
        *,
        error: bool = False,
        running: bool = False,
        ok: bool = False,
    ) -> None:
        status = self.query_one("#status", Static)
        status.update(text)
        for cls in ("error", "running", "ok"):
            status.remove_class(cls)
        if error:
            status.add_class("error")
        if running:
            status.add_class("running")
        if ok:
            status.add_class("ok")
