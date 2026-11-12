"""Промпт и HTTP-клиент ИИ-диагноза; сеть заменяется фейком, ключ не нужен."""
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error

from tui.analysis import Analysis, Hypothesis, Indicator
from tui.hotspots import Hotspot, SourcePreview
from tui.llm import (
    LLMError,
    build_prompt,
    config,
    config_issue,
    diagnose,
    request_diagnosis,
)


class _FakeResponse:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._text.encode("utf-8")


def _chat(content):
    return json.dumps({"choices": [{"message": {"content": content}}]})


def _sample_hotspots():
    hotspot = Hotspot("work", "/tmp/a.cpp:10", "prog", 80, 60.0)
    preview = SourcePreview(Path("/tmp/a.cpp"), 10, 8,
                            "line8\nline9\nline10\nline11\nline12\n")
    return [hotspot], [preview]


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test",
                                     "LATENCYLAB_LLM_API_KEY": "",
                                     "LATENCYLAB_LLM_BASE_URL": "",
                                     "LATENCYLAB_LLM_MODEL": ""}):
            base, model, key = config()
        self.assertEqual(base, "https://api.deepseek.com")
        self.assertEqual(model, "deepseek-chat")
        self.assertEqual(key, "sk-test")

    def test_env_override_and_ollama_base(self):
        with patch.dict(os.environ, {
            "LATENCYLAB_LLM_BASE_URL": "http://localhost:11434/v1/",
            "LATENCYLAB_LLM_MODEL": "qwen2.5-coder:7b",
            "LATENCYLAB_LLM_API_KEY": "abc",
        }):
            base, model, key = config()
        self.assertEqual(base, "http://localhost:11434/v1")
        self.assertEqual(model, "qwen2.5-coder:7b")
        self.assertEqual(key, "abc")

    def test_config_issue_absent_with_key_or_local_base(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k",
                                     "LATENCYLAB_LLM_API_KEY": "",
                                     "LATENCYLAB_LLM_BASE_URL": ""}):
            self.assertIsNone(config_issue())
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "",
                                     "LATENCYLAB_LLM_API_KEY": "",
                                     "LATENCYLAB_LLM_BASE_URL": "http://localhost:11434/v1"}):
            self.assertIsNone(config_issue())

    def test_config_issue_message_without_key(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "",
                                     "LATENCYLAB_LLM_API_KEY": "",
                                     "LATENCYLAB_LLM_BASE_URL": ""}):
            self.assertIn("DEEPSEEK_API_KEY", config_issue())


class PromptTests(unittest.TestCase):
    def test_prompt_contains_code_context_and_metrics(self):
        hotspots, previews = _sample_hotspots()
        analysis = Analysis(
            indicators=[Indicator("cpi", "CPI (тактов на инструкцию)", 1.9, "1.90", True, 5)],
            hypotheses=[Hypothesis(title="Возможна проблема локальности данных",
                                   evidence="cache_misses: 40.00, n=5",
                                   check="Сравните rows/columns")],
        )
        messages = build_prompt(command="./example --size 42", mode="cpu", cpu=2,
                                kernel="6.8.0", total_samples=100,
                                hotspots=hotspots, previews=previews, analysis=analysis)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        user = messages[1]["content"]
        self.assertIn("Команда: ./example --size 42", user)
        self.assertIn("kernel 6.8.0", user)
        self.assertIn("сэмплов 100", user)
        self.assertIn("work — /tmp/a.cpp:10", user)
        self.assertIn(">>", user)          # маркер горячей строки
        self.assertIn("line10", user)
        self.assertIn("CPI", user)         # метрики обычной серии
        self.assertIn("локальности данных", user)

    def test_prompt_without_metrics_marks_it(self):
        hotspots, previews = _sample_hotspots()
        messages = build_prompt(command="./example", mode="cache", cpu=None,
                                kernel=None, total_samples=10,
                                hotspots=hotspots, previews=previews, analysis=None)
        user = messages[1]["content"]
        self.assertIn("Метрики обычной серии не переданы", user)
        self.assertNotIn("CPI", user)

    def test_missing_source_is_marked(self):
        hotspots, _ = _sample_hotspots()
        messages = build_prompt(command="./example", mode="cpu", cpu=None,
                                kernel=None, total_samples=10,
                                hotspots=hotspots, previews=[None], analysis=None)
        self.assertIn("исходник недоступен", messages[1]["content"])


class RequestTests(unittest.TestCase):
    def test_returns_content_and_sends_bearer(self):
        with patch("urllib.request.urlopen",
                   return_value=_FakeResponse(_chat("причина: промахи кеша"))) as urlopen:
            result = request_diagnosis([{"role": "user", "content": "x"}],
                                       "https://api.deepseek.com", "deepseek-chat", "k")
        self.assertEqual(result, "причина: промахи кеша")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer k")
        body = json.loads(request.data)
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(body["messages"][0]["content"], "x")

    def test_http_error_surfaces_code(self):
        error = urllib.error.HTTPError("https://api.deepseek.com/chat/completions",
                                       401, "Unauthorized", {}, io.BytesIO(b"bad key"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(LLMError, "401"):
                request_diagnosis([{"role": "user", "content": "x"}],
                                  "https://api.deepseek.com", "deepseek-chat", "k")

    def test_empty_content_is_an_error(self):
        with patch("urllib.request.urlopen", return_value=_FakeResponse(_chat("   "))):
            with self.assertRaisesRegex(LLMError, "пустой"):
                request_diagnosis([{"role": "user", "content": "x"}],
                                  "https://api.deepseek.com", "deepseek-chat", "k")


class DiagnoseTests(unittest.TestCase):
    def test_missing_key_with_default_base_is_clear(self):
        hotspots, previews = _sample_hotspots()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "",
                                     "LATENCYLAB_LLM_API_KEY": "",
                                     "LATENCYLAB_LLM_BASE_URL": ""}):
            with self.assertRaisesRegex(LLMError, "DEEPSEEK_API_KEY"):
                diagnose(command="./example", mode="cpu", cpu=None, kernel=None,
                         total_samples=10, hotspots=hotspots, previews=previews,
                         analysis=None)

    def test_local_base_needs_no_key(self):
        hotspots, previews = _sample_hotspots()
        with patch.dict(os.environ, {"LATENCYLAB_LLM_BASE_URL": "http://localhost:11434/v1",
                                     "DEEPSEEK_API_KEY": "", "LATENCYLAB_LLM_API_KEY": ""}):
            with patch("urllib.request.urlopen",
                       return_value=_FakeResponse(_chat("локальный ответ"))) as urlopen:
                result = diagnose(command="./example", mode="cpu", cpu=None, kernel=None,
                                  total_samples=10, hotspots=hotspots, previews=previews,
                                  analysis=None)
        self.assertEqual(result, "локальный ответ")
        request = urlopen.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
