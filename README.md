# LatencyLab

Python TUI запускает внешний бинарник через C++ runner и собирает метрики с помощью `MetricsCollector`.

## Быстрый запуск на Linux

Нужны компилятор C++17 и Python 3.8+. CMake (3.16+) ставится через pip, если его нет в системе.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 2
python3 -m tui --demo
```

`--demo` подставляет собранный `build/demo_target`. В поле команды можно указать собственный бинарник с аргументами. `CPU` выбирает логический процессор; пустое поле означает первый разрешённый CPU. `sample` задаёт число последовательных запусков. Каждый запуск создаёт новый процесс на одном и том же CPU.

В TUI отображаются среднее/min/max по успешным запускам, stdout/stderr последнего запуска и пояснения к метрикам. Ниже метрик — панель «Диагноз»: производные индикаторы (CPI, доля промахов кеша, backend/frontend-stall, доля system CPU, утилизация CPU) и ранжированные гипотезы о причинах задержки с подсказкой, что проверить. Тот же диагноз по сохранённым результатам печатает `python3 scripts/analyze.py results/series-*.json`. Кнопка «Стоп» завершает текущий процесс и серию. Исходные данные сохраняются в `results/run-*.json`, сводка — в `results/series-*.json`.

В списке над командой доступны готовые демо: массив подряд и с большим шагом, бинарный поиск, умножение матриц по строкам и столбцам. Выбор заполняет команду, параметры можно редактировать.

```bash
python3 -m tui --demo array-sequential
python3 -m tui --demo binary-search
python3 -m tui --demo matrix-columns
```

Все команды и параметры: [готовые демо](docs/demos.md).

[Скриншоты и результаты 25 демонстрационных запусков](docs/reports/2026-11-10/README.md) — пять экранов TUI, исходные JSON и параметры среды. Аппаратные perf-счётчики в этой среде недоступны.

## C++ runner отдельно

```bash
./build/runner --cpu 0 --timeout-ms 10000 -- ./build/demo_target 32 4
```

Аргументы передаются напрямую через `execvp`, без shell. Измеряется главный поток цели в пользовательском режиме, включая `exec` и загрузчик; остальные потоки/процессы не входят в аппаратные счётчики. Данные `wait4` относятся к ресурсам целевого процесса. Кеши не очищаются.

Если Linux/VM запрещает `perf_event_open`, метрики сохраняются как `null` с причиной. Время и resource counters продолжают отображаться; аппаратные значения не подставляются. Нативный runner требует Linux, на macOS возможен только просмотр TUI с явно выбранным моком.

### Разблокировка аппаратных счётчиков

Если в таблице метрик вместо значений — `null` с причиной `perf_event_open: Permission denied`, ядро запрещает непривилегированный доступ (`/proc/sys/kernel/perf_event_paranoid` ≥ 3). Опустите порог один раз (нужен root):

```bash
scripts/enable_perf.sh            # спросит подтверждение
scripts/enable_perf.sh --yes      # сразу, без вопроса
```

Скрипт проверяет текущее значение и меняет его только при необходимости. Настройка действует до перезагрузки; для постоянства добавьте `kernel.perf_event_paranoid=1` в `/etc/sysctl.d/*.conf`.

## Как разложен код

```text
src/runner.cpp            точка входа
src/runner/options.cpp    аргументы CLI
src/runner/process.cpp    affinity, fork/exec, ожидание, MetricsCollector
src/runner/capture.cpp    захват stdout/stderr
src/runner/json.cpp       сериализация результата
src/runner/posix.hpp      RAII для pipe и дочернего процесса
src/metrics.cpp           существующий модуль сбора метрик
src/demos/               массив, бинарный поиск, умножение матриц
tui/runner.py             вызов C++ через subprocess
tui/demos.py              готовые команды для списка демо
tui/statistics.py         среднее/min/max по sample
tui/app.py                Textual TUI
```

## Проверки

```bash
ctest --test-dir build --output-on-failure
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

CTest проверяет библиотеку метрик, Linux runner и корректность вычислений демо. Python-тесты проверяют статистику и реальный TUI через Textual Pilot: число запусков, выбор демо, остановку, закрытие приложения и обработку ошибок. Тест аппаратных cycles пропускается, если PMU недоступен.

Подробнее: [TUI](tui/README.md), [интеграция MetricsCollector](docs/integration.md).
