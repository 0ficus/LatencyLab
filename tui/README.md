# Latency Lab — TUI

Python TUI (Textual) + прослойка до C++ runner'а. TUI сам программу не запускает —
он вызывает runner, который запускает целевую программу, собирает метрики и печатает
JSON; TUI этот JSON показывает и сохраняет в `results/`.

## Установка

```bash
pip install -r requirements.txt
```

## Запуск

С настоящим C++ runner'ом (бинарник `runner` в PATH):

```bash
python3 -m tui
```

Пока runner'а нет — с моком (эмуляция того же контракта, метрики синтетические):

```bash
python3 -m tui --runner "python3 tui/mock_runner.py --mock-metrics" --demo
```

Флаг `--demo` подставляет демо-команду в поле ввода, а `--mock-metrics`
заставляет мок подставить явно помеченные синтетические метрики (без него мок
честно показывает всё как «недоступно»). Путь к runner'у также можно задать
переменной окружения `LATENCYLAB_RUNNER`, например
`LATENCYLAB_RUNNER="python3 tui/mock_runner.py --mock-metrics"`.

Внутри TUI: введите команду → «Запустить» (или Enter). Статус сменится на
«Выполняется…», затем появится сводка, таблица метрик и вывод программы.
Результат каждого запуска сохраняется в `results/run-*.json`.

## Контракт runner'а

TUI вызывает runner так:

```text
runner --command "CMD" [--cpu N]
```

Runner выполняет `CMD` в шелле и печатает в stdout **ровно один JSON-объект**
(ничего больше; вывод самой программы передаётся внутри JSON, а не вперемешку):

```json
{
  "schema_version": 1,
  "mock": false,
  "command": "python demo.py",
  "cpu": 0,
  "exit_code": 0,
  "elapsed_ns": 123456789,
  "kernel": "6.8.0-138-generic",
  "perf_event_paranoid": "4",
  "resources": {
    "source": "getrusage(RUSAGE_CHILDREN)",
    "user_cpu_us": 1234,
    "system_cpu_us": 56,
    "minor_page_faults": 0,
    "major_page_faults": 0
  },
  "metrics": {
    "cycles": {
      "status": "available",
      "unit": "count",
      "value": 12345.0,
      "raw_value": 12345,
      "time_enabled_ns": 1000,
      "time_running_ns": 900,
      "errno": 0,
      "reason": null
    }
  },
  "stdout": "вывод программы",
  "stderr": "",
  "stdout_truncated": false,
  "stderr_truncated": false
}
```

Поля `metrics` совпадают с форматом Егора (`metrics_json` из `docs/integration.md`).
Поля, помеченные `mock`/`mock-metrics`, — синтетика только для предпросмотра UI.

См. также: `schema.py` (модель и валидация), `runner.py` (вызов и разбор).
