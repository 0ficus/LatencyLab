# ИИ-диагноз: настройка LLM

Кнопка **«ИИ-диагноз»** на экране «Участки кода» отправляет внешней модели
горячий код (топ-5 мест с фрагментами исходника) и, если обычная серия уже
запускалась, её метрики и выводы `analyze`. Модель возвращает короткий вердикт,
вероятную причину задержки и 2–3 эксперимента для проверки. Это дополнение к
обычному диагнозу `analysis.py`, а не замена: без сети/ключа обычный диагноз
работает как раньше.

Поддерживаются два типа провайдеров:

- **OpenAI-совместимый** — DeepSeek (по умолчанию), локальная Ollama или любой
  другой сервер с эндпоинтом `/v1/chat/completions`.
- **Anthropic Claude** — через официальный SDK (`anthropic`).

Ключ читается только из переменных окружения, нигде не сохраняется и не
попадает в `results/` и логи.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `LATENCYLAB_LLM_PROVIDER` | `auto` | `anthropic` или `openai`. При `auto` выбирается Claude, если задан `ANTHROPIC_API_KEY`, иначе OpenAI-совместимый |
| `LATENCYLAB_LLM_MODEL` | `deepseek-chat` (openai) / `claude-opus-4-8` (anthropic) | имя модели |
| `LATENCYLAB_LLM_BASE_URL` | `https://api.deepseek.com` | база OpenAI-совместимого API (игнорируется для Claude) |
| `LATENCYLAB_LLM_API_KEY` / `DEEPSEEK_API_KEY` | — | ключ OpenAI-совместимого API; первый приоритетнее |
| `ANTHROPIC_API_KEY` | — | ключ Claude |

Переменные читаются один раз на запрос; после изменения перезапустите TUI.

## 1. DeepSeek (по умолчанию)

```bash
export DEEPSEEK_API_KEY="sk-..."
.venv/bin/python -m tui --demo matrix-columns
```

Где взять ключ: [platform.deepseek.com](https://platform.deepseek.com) → API Keys.

## 2. Локальная Ollama (без ключа, код не покидает машину)

```bash
ollama pull qwen2.5-coder:7b
ollama serve &                      # если сервер ещё не запущен
export LATENCYLAB_LLM_BASE_URL=http://localhost:11434/v1
export LATENCYLAB_LLM_MODEL=qwen2.5-coder:7b
```

При отсутствии ключа и заданном `LATENCYLAB_LLM_BASE_URL` провайдер считается
настроенным — ключ не нужен. Инференс идёт на CPU, поэтому скорость зависит от
железа; на слабых/переподписанных хостах ответ может идти долго (штатный таймаут
OpenAI-пути — 60 с, см. `REQUEST_TIMEOUT_S` в `tui/llm.py`).

## 3. Claude (Anthropic)

Нужен пакет `anthropic` (он не входит в основные зависимости):

```bash
.venv/bin/pip install anthropic
export ANTHROPIC_API_KEY="sk-ant-..."
export LATENCYLAB_LLM_PROVIDER=anthropic        # необязательно: автоопределение по ключу
export LATENCYLAB_LLM_MODEL=claude-opus-4-8     # по умолчанию
```

Модель по умолчанию — `claude-opus-4-8`. Доступные ID (см. каталог моделей Anthropic):

| Модель | Назначение |
| --- | --- |
| `claude-opus-4-8` | по умолчанию, самая сильная |
| `claude-sonnet-5` | дешевле, почти Opus-качество на коде |
| `claude-haiku-4-5` | самая быстрая и дешёвая |

Для Claude используются нативный Messages API и `ANTHROPIC_API_KEY`; параметры
`LATENCYLAB_LLM_BASE_URL` / `DEEPSEEK_API_KEY` не задействуются.

## Автоопределение провайдера

Если `LATENCYLAB_LLM_PROVIDER` не задан:

- задан `ANTHROPIC_API_KEY` → **Claude**;
- иначе → **OpenAI-совместимый** (DeepSeek по умолчанию, Ollama при заданном base_url).

Явное `LATENCYLAB_LLM_PROVIDER=openai` или `=anthropic` всегда имеет приоритет.

## Безопасность

- Ключ читается только из env: `os.environ`, без файлов, без логирования.
- Сам код и метрики передаются выбранному провайдеру. Если отправлять код наружу
  недопустимо — используйте локальную Ollama.
- При отсутствии ключа или недоступной сети кнопка «ИИ-диагноз» показывает
  понятную ошибку и не блокирует приложение.
