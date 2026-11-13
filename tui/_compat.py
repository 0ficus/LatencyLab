"""Совместимость с Python 3.8: asyncio.to_thread появился только в 3.9."""
from __future__ import annotations

import asyncio
import functools

try:  # Python 3.9+
    from asyncio import to_thread  # noqa: F401
except ImportError:  # Python 3.8 — эквивалент через run_in_executor
    def to_thread(func, *args, **kwargs):
        """Запустить func в отдельном потоке; awaitable, как asyncio.to_thread."""
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(None, functools.partial(func, *args, **kwargs))
