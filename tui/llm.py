"""Один HTTPS-запрос к OpenAI-совместимому LLM для объяснения горячего кода.

Провайдер настраивается переменными окружения; по умолчанию DeepSeek. Тот же
клиент без изменений работает с локальной Ollama (без ключа и без отправки
кода наружу) — см. docs/hotspots.md. Ключ читается только из env и нигде не
сохраняется; в результаты и логи он не попадает.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

from .analysis import Analysis, format_analysis
from .hotspots import Hotspot, SourcePreview

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
REQUEST_TIMEOUT_S = 60
MAX_RESPONSE_CHARS = 8000

SYSTEM_PROMPT = (
    "Ты — инженер по производительности. Тебе передают горячие фрагменты "
    "исходного кода, найденные профилировщиком perf, и аппаратные счётчики "
    "одного запуска. Объясни наиболее вероятную причину задержки в этом коде "
    "и предложи 2–3 конкретных эксперимента, чтобы подтвердить или опровергнуть "
    "гипотезу.\n\n"
    "Правила:\n"
    "- Отвечай на русском, кратко и по делу.\n"
    "- Ссылайся на конкретные строки кода и числа метрик; не выдумывай значений, "
    "которых нет во входных данных.\n"
    "- Счётчики зависят от PMU и могут быть шумными; если данных мало — честно "
    "скажи, чего не хватает.\n"
    "- Не предлагай переписать весь код; предлагай минимальное изменение и способ "
    "его проверки.\n"
    "- Если причина неоднозначна, перечисли альтернативы по убыванию вероятности.\n"
    "- Ограничься кратким ответом: вердикт, вероятная причина, эксперименты, оговорки."
)


class LLMError(RuntimeError):
    """LLM недоступен или вернул некорректный ответ; обычный диагноз продолжает работать."""


def config() -> tuple[str, str, str]:
    """(base_url, model, api_key) из переменных окружения."""
    base = (os.environ.get("LATENCYLAB_LLM_BASE_URL", "") or DEFAULT_BASE_URL).strip().rstrip("/")
    model = (os.environ.get("LATENCYLAB_LLM_MODEL", "") or DEFAULT_MODEL).strip()
    key = (os.environ.get("LATENCYLAB_LLM_API_KEY", "")
           or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
    return base, model, key


def config_issue() -> Optional[str]:
    """Подсказка, если LLM не настроен (нет ключа и не задан локальный base_url), иначе None."""
    base_url, _, key = config()
    if key or base_url != DEFAULT_BASE_URL.rstrip("/"):
        return None
    return ("Не задан ключ LLM. Экспортируйте DEEPSEEK_API_KEY (или "
            "LATENCYLAB_LLM_API_KEY); для локального запуска без ключа задайте "
            "LATENCYLAB_LLM_BASE_URL (например http://localhost:11434/v1).")


def _format_snippet(preview: Optional[SourcePreview]) -> str:
    if preview is None:
        return "(исходник недоступен: соберите бинарник с -g и сохраните исходники)"
    lines = []
    for offset, text in enumerate(preview.code.splitlines()):
        number = preview.start + offset
        marker = ">>" if number == preview.line else "  "
        lines.append(f"{marker} {number:4d}  {text}")
    return "\n".join(lines)


def build_prompt(
    *,
    command: str,
    mode: str,
    cpu: Optional[int],
    kernel: Optional[str],
    total_samples: int,
    hotspots: list[Hotspot],
    previews: list[Optional[SourcePreview]],
    analysis: Optional[Analysis],
) -> list[dict]:
    """Составить сообщения для LLM: контекст, метрики и горячий код."""
    sections = [
        f"Команда: {command}",
        f"Профиль: событие «{mode}», CPU {cpu if cpu is not None else 'авто'}, "
        f"kernel {kernel or 'неизвестен'}, сэмплов {total_samples}.",
    ]
    if analysis is not None:
        sections.append("Метрики и выводы обычной серии:\n" + format_analysis(analysis))
    else:
        sections.append("Метрики обычной серии не переданы: "
                        "интерпретируй только распределение сэмплов и код.")
    code = []
    for item, preview in zip(hotspots, previews):
        code.append(f"\n### {item.symbol} — {item.location} "
                    f"(доля сэмплов {item.share:.1f}%, n={item.samples})")
        code.append(_format_snippet(preview))
    sections.append("Горячий код (по убыванию доли сэмплов):" + "\n".join(code))
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def request_diagnosis(messages: list[dict], base_url: str, model: str,
                      key: str, timeout: float = REQUEST_TIMEOUT_S) -> str:
    """Один POST /chat/completions и текст ответа."""
    payload = json.dumps({"model": model, "messages": messages,
                          "temperature": 0.2, "stream": False}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(base_url + "/chat/completions",
                                     data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise LLMError(f"LLM ответил {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Не удалось обратиться к LLM ({exc.reason}); "
                       "проверьте сеть и LATENCYLAB_LLM_BASE_URL.") from exc
    except (TimeoutError, OSError) as exc:
        raise LLMError(f"Таймаут или ошибка соединения с LLM: {exc}") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Неожиданный ответ LLM: {str(data)[:300]}") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM вернул пустой ответ.")
    content = content.strip()
    if len(content) > MAX_RESPONSE_CHARS:
        content = content[:MAX_RESPONSE_CHARS] + "…"
    return content


def diagnose(*, command: str, mode: str, cpu: Optional[int], kernel: Optional[str],
             total_samples: int, hotspots: list[Hotspot],
             previews: list[Optional[SourcePreview]],
             analysis: Optional[Analysis]) -> str:
    """Полный цикл: конфиг из env, промпт и запрос."""
    base_url, model, key = config()
    issue = config_issue()
    if issue is not None:
        raise LLMError(issue)
    messages = build_prompt(command=command, mode=mode, cpu=cpu, kernel=kernel,
                            total_samples=total_samples, hotspots=hotspots,
                            previews=previews, analysis=analysis)
    return request_diagnosis(messages, base_url, model, key)
