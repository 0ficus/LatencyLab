"""Built-in executable presets. Selecting one only fills the editable command field."""
from pathlib import Path
import shlex

PRESETS = {
    "array-sequential": ("Массив: последовательный обход", "demo_array", ("32", "8", "sequential")),
    "array-strided": ("Массив: обход с большим шагом", "demo_array", ("32", "8", "strided")),
    "binary-search": ("Бинарный поиск", "demo_binary_search", ("1048576", "200000")),
    "matrix-rows": ("Умножение матриц: по строкам (ikj)", "demo_matrix", ("256", "3", "rows")),
    "matrix-columns": ("Умножение матриц: по столбцам (ijk)", "demo_matrix", ("256", "3", "columns")),
}
CHOICES = [("Свой бинарник", "custom"), ("Прежнее демо: большой шаг", "legacy")] + [
    (label, name) for name, (label, _, _) in PRESETS.items()
]
DEMO_NAMES = ("legacy", *PRESETS)


def command(name: str, build_directory: Path) -> str:
    _, executable, arguments = PRESETS[name]
    return shlex.join([str(build_directory / executable), *arguments])
