"""A deliberately small source-hotspot view, separate from normal measurements."""
from __future__ import annotations

import asyncio
from pathlib import Path
import threading

from rich.syntax import Syntax
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from .analysis import Analysis
from .hotspots import source_preview
from .llm import LLMError, config_issue, diagnose
from .profiler import ProfileError, ProfileResult, profile


class HotspotScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Закрыть")]
    DEFAULT_CSS = """
    HotspotScreen { align: center middle; }
    #hotspot-dialog { width: 96%; height: 95%; border: round $primary; padding: 0 1; }
    #hotspot-actions { height: 3; }
    #hotspot-actions Button { min-width: 8; margin-right: 1; }
    #profile-cpu { width: 10; }
    #profile-cache { width: 18; }
    #llm-diagnose { width: 16; }
    #hotspot-note, #hotspot-status, #source-title, #profile-diagnostics, #llm-diagnosis { height: auto; }
    #hotspot-note, #profile-diagnostics, #llm-diagnosis { color: $text-muted; }
    #hotspot-table { height: 7; }
    #source-code { height: auto; margin: 1 0; }
    """

    def __init__(self, command: str, cpu: int | None, results_dir: Path,
                 timeout: float | None, analysis: Analysis | None = None,
                 kernel: str | None = None) -> None:
        super().__init__()
        self.command, self.cpu = command, cpu
        self.results_dir, self.timeout = results_dir, timeout
        self.analysis, self.kernel = analysis, kernel
        self.result: ProfileResult | None = None
        self._cancel = threading.Event()
        self._busy = False
        self._llm_busy = False

    def compose(self) -> ComposeResult:
        with Container(id="hotspot-dialog"):
            yield Static("Участки кода · топ-5", markup=False)
            yield Static("Один отдельный запуск на одном CPU; sample здесь не применяется.",
                         id="hotspot-note", markup=False)
            with Horizontal(id="hotspot-actions"):
                yield Button("CPU", id="profile-cpu", variant="primary")
                yield Button("Cache misses", id="profile-cache")
                yield Button("ИИ-диагноз", id="llm-diagnose")
                yield Button("Стоп", id="profile-stop", disabled=True)
                yield Button("Закрыть", id="profile-close")
            with VerticalScroll():
                yield Static(self.command, id="hotspot-status", markup=False)
                yield DataTable(id="hotspot-table", cursor_type="row")
                yield Static("Выберите CPU или Cache misses.", id="source-title", markup=False)
                yield Static("", id="source-code", markup=False)
                yield Static("", id="profile-diagnostics", markup=False)
                yield Static("", id="llm-diagnosis", markup=False)

    def on_mount(self) -> None:
        self.query_one("#hotspot-table", DataTable).add_columns(
            "Доля сэмплов", "n", "Функция", "Файл:строка", "Бинарник")

    @on(Button.Pressed, "#profile-cpu")
    @on(Button.Pressed, "#profile-cache")
    def _start(self, event: Button.Pressed) -> None:
        if self._busy:
            return
        self.result = None
        self.query_one("#hotspot-table", DataTable).clear()
        self.query_one("#source-title", Static).update("")
        self.query_one("#source-code", Static).update("")
        self.query_one("#profile-diagnostics", Static).update("")
        self.query_one("#llm-diagnosis", Static).update("")
        self._cancel = threading.Event()
        self._set_busy(True)
        self.query_one("#hotspot-status", Static).update("Собираем профиль…")
        self._profile("cpu" if event.button.id == "profile-cpu" else "cache")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for name in ("#profile-cpu", "#profile-cache"):
            self.query_one(name, Button).disabled = busy
        self.query_one("#profile-stop", Button).disabled = not busy
        self._refresh_llm_button()

    def _refresh_llm_button(self) -> None:
        self.query_one("#llm-diagnose", Button).disabled = self._busy or self._llm_busy

    @work(exclusive=True, group="profile")
    async def _profile(self, mode: str) -> None:
        try:
            result = await asyncio.to_thread(profile, self.command, mode, self.cpu,
                                             self.results_dir, self.timeout, self._cancel)
            if not self.is_mounted:
                return
            self.result = result
            table = self.query_one("#hotspot-table", DataTable)
            for item in result.hotspots:
                table.add_row(f"{item.share:.1f}%", str(item.samples), Text(item.symbol),
                              Text(item.location), Text(item.dso))
            message = (f"CPU {result.cpu} · {result.total_samples} сэмплов. "
                       "Доля сэмплов события, а не процент задержки.")
            if result.total_samples < 20:
                message += " Мало сэмплов: увеличьте объём работы в команде."
            if mode == "cache":
                message += " Строка приблизительная: возможен сдвиг IP (skid)."
            self.query_one("#hotspot-status", Static).update(message)
            self.query_one("#profile-diagnostics", Static).update(
                f"Сохранено: {result.directory}\n{result.diagnostics}")
            if result.hotspots:
                table.move_cursor(row=0)
                self._show_source(0)
                table.focus()
            else:
                self.query_one("#source-title", Static).update("Сэмплы не получены; ранжировать участки нельзя.")
        except (ProfileError, OSError) as exc:
            if self.is_mounted:
                self.query_one("#hotspot-status", Static).update(str(exc))
        except asyncio.CancelledError:
            self._cancel.set()
            raise
        finally:
            if self.is_mounted:
                self._set_busy(False)

    @on(DataTable.RowHighlighted, "#hotspot-table")
    def _highlight(self, event: DataTable.RowHighlighted) -> None:
        self._show_source(event.cursor_row)

    def _show_source(self, index: int) -> None:
        if self.result is None or not 0 <= index < len(self.result.hotspots):
            return
        item = self.result.hotspots[index]
        preview = source_preview(item.location)
        self.query_one("#source-title", Static).update(f"{item.symbol} · {item.location}")
        view = self.query_one("#source-code", Static)
        if preview is None:
            view.update("Исходник недоступен. Соберите бинарник с -g (RelWithDebInfo) "
                        "и сохраните исходники по пути из профиля. Для Python виден нативный интерпретатор.")
        else:
            view.update(Syntax(preview.code, "cpp", line_numbers=True, start_line=preview.start,
                               highlight_lines={preview.line}, word_wrap=True))

    @on(Button.Pressed, "#llm-diagnose")
    def _press_llm(self) -> None:
        if self._busy or self._llm_busy:
            return
        if self.result is None or not self.result.hotspots:
            self.query_one("#llm-diagnosis", Static).update(
                "Сначала получите профиль (CPU или Cache misses).")
            return
        issue = config_issue()
        if issue is not None:
            self.query_one("#llm-diagnosis", Static).update(f"ИИ-диагноз недоступен: {issue}")
            return
        self._llm_busy = True
        self._refresh_llm_button()
        self.query_one("#llm-diagnosis", Static).update("Запрашиваем ИИ-диагноз…")
        self._run_llm()

    @work(exclusive=True, group="llm")
    async def _run_llm(self) -> None:
        try:
            result = self.result
            previews = [source_preview(item.location) for item in result.hotspots]
            answer = await asyncio.to_thread(
                diagnose, command=self.command, mode=result.mode, cpu=result.cpu,
                kernel=self.kernel, total_samples=result.total_samples,
                hotspots=result.hotspots, previews=previews, analysis=self.analysis)
            if self.is_mounted:
                self.query_one("#llm-diagnosis", Static).update(answer)
        except LLMError as exc:
            if self.is_mounted:
                self.query_one("#llm-diagnosis", Static).update(f"ИИ-диагноз недоступен: {exc}")
        except asyncio.CancelledError:
            raise
        finally:
            self._llm_busy = False
            if self.is_mounted:
                self._refresh_llm_button()

    @on(Button.Pressed, "#profile-stop")
    def _stop(self) -> None:
        self._cancel.set()

    @on(Button.Pressed, "#profile-close")
    def action_close(self) -> None:
        self._cancel.set()
        self.dismiss()

    def on_unmount(self) -> None:
        self._cancel.set()
