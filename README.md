# LatencyLab

Python TUI запускает внешний бинарник через C++ runner и собирает метрики с помощью `MetricsCollector`.

## Быстрый запуск на Linux

Нужны CMake 3.16+, компилятор C++17 и Python 3.10+.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 2
python3 -m tui --demo
```

`--demo` подставляет собранный `build/demo_target`. В поле команды можно указать собственный бинарник с аргументами. `CPU` выбирает логический процессор; пустое поле означает первый разрешённый CPU. `sample` задаёт число последовательных запусков. Каждый запуск создаёт новый процесс на одном и том же CPU.

В TUI отображаются среднее/min/max по успешным запускам, stdout/stderr последнего запуска и пояснения к метрикам. Кнопка «Стоп» завершает текущий процесс и серию. Исходные данные сохраняются в `results/run-*.json`, сводка — в `results/series-*.json`.

## C++ runner отдельно

```bash
./build/runner --cpu 0 --timeout-ms 10000 -- ./build/demo_target 32 4
```

Аргументы передаются напрямую через `execvp`, без shell. Измеряется главный поток цели в пользовательском режиме, включая `exec` и загрузчик; остальные потоки/процессы не входят в аппаратные счётчики. Данные `wait4` относятся к ресурсам целевого процесса. Кеши не очищаются.

Если Linux/VM запрещает `perf_event_open`, метрики сохраняются как `null` с причиной. Время и resource counters продолжают отображаться; аппаратные значения не подставляются. Нативный runner требует Linux, на macOS возможен только просмотр TUI с явно выбранным моком.

## Как разложен код

```text
src/runner.cpp            точка входа
src/runner/options.cpp    аргументы CLI
src/runner/process.cpp    affinity, fork/exec, ожидание, MetricsCollector
src/runner/capture.cpp    захват stdout/stderr
src/runner/json.cpp       сериализация результата
src/runner/posix.hpp      RAII для pipe и дочернего процесса
src/metrics.cpp           существующий модуль сбора метрик
tui/runner.py             вызов C++ через subprocess
tui/statistics.py         среднее/min/max по sample
tui/app.py                Textual TUI
```

## Проверки

```bash
ctest --test-dir build --output-on-failure
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

CTest проверяет библиотеку метрик и Linux runner. Python-тесты проверяют статистику и реальный TUI через Textual Pilot: число запусков, остановку, закрытие приложения и обработку ошибок. Тест аппаратных cycles пропускается, если PMU недоступен.

Подробнее: [TUI](tui/README.md), [интеграция MetricsCollector](docs/integration.md).
