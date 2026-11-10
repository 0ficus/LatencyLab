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
from datetime import datetime
from pathlib import Path
from typing import Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from .explanations import METRIC_EXPLANATIONS
from .runner import RunOutcome, RunnerError, run_runner
from .schema import Metric, RunnerResult

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
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def format_metric_value(metric: Metric) -> str:
    """Значение метрики с учётом единиц: время показываем по-человечески."""
    if metric.value is None:
        return "—"
    if metric.unit == "ns":
        return format_ns(int(metric.value))
    return format_value(metric.value)


class LatencyLabApp(App):
    TITLE = "Latency Lab"
    SUB_TITLE = "запуск программы и сбор метрик"

    CSS = """
    #controls { height: auto; padding: 1 2; }
    #controls Input { margin-bottom: 1; }
    #row { height: auto; }
    #cpu { width: 20; margin-right: 1; }
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
        runner_path: str = "runner",
        results_dir: Path = Path("results"),
        demo: bool = False,
        runner_timeout: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.runner_path = runner_path
        self.results_dir = results_dir
        self.demo = demo
        self.runner_timeout = runner_timeout
        self.demo_command = 'python3 -c "print(sum(i*i for i in range(2000000)))"'
        self.last_saved: Optional[Path] = None
        self._run_index = 0

    # --- построение интерфейса ------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="controls"):
            yield Label("Команда для запуска", classes="section-title")
            yield Input(placeholder="python demo.py --size 1024", id="command")
            with Horizontal(id="row"):
                yield Input(placeholder="CPU (пусто = авто)", id="cpu")
                yield Button("▶ Запустить", id="run", variant="primary")
                yield Button("Очистить", id="clear")
            yield Static("Готов к работе.", id="status")
        with VerticalScroll(id="results"):
            yield Static("", id="summary")
            yield Label("Метрики", classes="section-title")
            yield DataTable(id="metrics")
            yield Static("", id="resources")
            yield Label("Вывод программы (stdout)", classes="section-title")
            yield Static("", id="stdout")
            yield Label("Ошибки программы (stderr)", classes="section-title")
            yield Static("", id="stderr")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#metrics", DataTable)
        table.add_columns("Метрика", "Значение", "Ед.", "Статус", "Пояснение")
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
        self.query_one("#command", Input).value = ""
        self.query_one("#cpu", Input).value = ""
        self._clear_results()
        self._set_status("Готов к работе.")

    # --- запуск ---------------------------------------------------------------

    def _start_run(self) -> None:
        command = self.query_one("#command", Input).value.strip()
        if not command:
            self._set_status("Введите команду.", error=True)
            return

        cpu_text = self.query_one("#cpu", Input).value.strip()
        cpu: Optional[int] = None
        if cpu_text:
            if not cpu_text.isdigit():
                self._set_status("CPU должен быть целым числом или пустым.", error=True)
                return
            cpu = int(cpu_text)

        self._set_status("Выполняется…", running=True)
        self._clear_results()
        self._run(command, cpu)

    @work(exclusive=True, group="run")
    async def _run(self, command: str, cpu: Optional[int]) -> None:
        try:
            outcome = await asyncio.to_thread(
                run_runner, command, cpu, self.runner_path, self.runner_timeout
            )
        except RunnerError as exc:
            self._set_status(str(exc), error=True)
            return
        self._show_result(outcome)
        saved = self._save_result(outcome)
        self._set_status(f"Готово. Результат сохранён: {saved}", ok=True)

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
        if result.mock:
            bits.append("⚠️ СИНТЕТИЧЕСКИЕ ДАННЫЕ (мок)")
        self.query_one("#summary", Static).update("  •  ".join(bits))

    def _render_metrics(self, result: RunnerResult) -> None:
        table = self.query_one("#metrics", DataTable)
        table.clear()
        for name, metric in result.metrics.items():
            value = format_metric_value(metric)
            explanation = METRIC_EXPLANATIONS.get(name, "")
            if not metric.available and metric.reason:
                explanation = (explanation + " " if explanation else "") + f"[{metric.reason}]"
            table.add_row(name, value, metric.unit, metric.status, explanation)

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
        self.query_one("#resources", Static).update("  •  ".join(parts) if parts else "")

    def _render_output(self, result: RunnerResult) -> None:
        stdout = result.stdout[:STDOUT_PREVIEW]
        stderr = result.stderr[:STDOUT_PREVIEW]
        if result.stdout_truncated:
            stdout += "\n… (обрезано)"
        if result.stderr_truncated:
            stderr += "\n… (обрезано)"
        self.query_one("#stdout", Static).update(stdout or "(пусто)")
        self.query_one("#stderr", Static).update(stderr or "(пусто)")

    def _save_result(self, outcome: RunOutcome) -> Path:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self._run_index += 1
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        path = self.results_dir / f"run-{stamp}-{self._run_index:03d}.json"
        path.write_text(json.dumps(outcome.raw, ensure_ascii=False, indent=2), encoding="utf-8")
        self.last_saved = path
        return path

    # --- вспомогательное ------------------------------------------------------

    def _clear_results(self) -> None:
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
