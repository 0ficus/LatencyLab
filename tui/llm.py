"""ИИ-диагноз горячего кода через внешний LLM.

Поддерживаются два провайдера, выбор — переменными окружения:
- OpenAI-совместимый (по умолчанию DeepSeek; локальная Ollama через base_url);
- Anthropic Claude через официальный SDK.

Ключ читается только из env и нигде не сохраняется; в результаты и логи он
не попадает. Настройка и примеры — docs/llm.md.
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
ANTHROPIC_DEFAULT_MODEL = "claude-opus-4-8"
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
    """(base_url, model, api_key) для OpenAI-совместимого провайдера."""
    base = (os.environ.get("LATENCYLAB_LLM_BASE_URL", "") or DEFAULT_BASE_URL).strip().rstrip("/")
    model = (os.environ.get("LATENCYLAB_LLM_MODEL", "") or DEFAULT_MODEL).strip()
    key = (os.environ.get("LATENCYLAB_LLM_API_KEY", "")
           or os.environ.get("DEEPSEEK_API_KEY", "")).strip()
    return base, model, key


def _provider() -> str:
    """'anthropic' или 'openai': явный LATENCYLAB_LLM_PROVIDER либо автоопределение."""
    value = os.environ.get("LATENCYLAB_LLM_PROVIDER", "").strip().lower()
    if value in ("anthropic", "openai"):
        return value
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY", "").strip() else "openai"


def _anthropic_config() -> tuple[str, str]:
    """(model, api_key) для Anthropic."""
    model = (os.environ.get("LATENCYLAB_LLM_MODEL", "") or ANTHROPIC_DEFAULT_MODEL).strip()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return model, key


def config_issue() -> Optional[str]:
    """Подсказка, если выбранный LLM не настроен, иначе None."""
    if _provider() == "anthropic":
        _, key = _anthropic_config()
        if key:
            return None
        return ("Провайдер Claude выбран, но не задан ANTHROPIC_API_KEY. "
                "Экспортируйте ключ или переключитесь (LATENCYLAB_LLM_PROVIDER=openai).")
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
    """Один POST /chat/completions к OpenAI-совместимому API и текст ответа."""
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


def _request_anthropic(system: str, user_text: str, model: str, key: str) -> str:
    """Один запрос к Anthropic Messages API через официальный SDK."""
    try:
        import anthropic
    except ImportError as exc:
        raise LLMError(
            "Провайдер Claude требует пакет anthropic. Установите: pip install anthropic"
        ) from exc
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.AuthenticationError as exc:
        raise LLMError("Claude: неверный ANTHROPIC_API_KEY.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Claude ответил {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError(f"Не удалось обратиться к Claude: {exc}") from exc
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise LLMError("Claude вернул пустой ответ.")
    if len(text) > MAX_RESPONSE_CHARS:
        text = text[:MAX_RESPONSE_CHARS] + "…"
    return text


def diagnose(*, command: str, mode: str, cpu: Optional[int], kernel: Optional[str],
             total_samples: int, hotspots: list[Hotspot],
             previews: list[Optional[SourcePreview]],
             analysis: Optional[Analysis]) -> str:
    """Полный цикл: конфиг из env, промпт и запрос к выбранному провайдеру."""
    issue = config_issue()
    if issue is not None:
        raise LLMError(issue)
    messages = build_prompt(command=command, mode=mode, cpu=cpu, kernel=kernel,
                            total_samples=total_samples, hotspots=hotspots,
                            previews=previews, analysis=analysis)
    if _provider() == "anthropic":
        model, key = _anthropic_config()
        system = next(m["content"] for m in messages if m["role"] == "system")
        user_text = next(m["content"] for m in messages if m["role"] == "user")
        return _request_anthropic(system, user_text, model, key)
    base_url, model, key = config()
    return request_diagnosis(messages, base_url, model, key)
