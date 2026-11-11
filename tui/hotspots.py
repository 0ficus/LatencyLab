"""Small, fixed-format perf report parser and source preview."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Hotspot:
    symbol: str
    location: str
    dso: str
    samples: int
    share: float

    def to_dict(self) -> dict:
        return asdict(self)


def parse_report(report: str) -> tuple[list[Hotspot], int]:
    """Return top five rows; shares use ALL samples, including unresolved rows.

    perf fields: overhead, sample, symbol, srcline, dso; separator is TAB.
    We deliberately calculate sample shares rather than reinterpret perf's
    period-weighted overhead as a percentage of wall-clock latency.
    """
    counts: dict[tuple[str, str, str], int] = {}
    for row in report.splitlines():
        if not row.strip() or row.lstrip().startswith("#"):
            continue
        fields = [field.strip() for field in row.split("\t")]
        if len(fields) != 5 or not fields[1].isdigit():
            raise ValueError("Неожиданный формат perf report (ожидались 5 полей через TAB).")
        _, samples, symbol, location, dso = fields
        symbol = re.sub(r"^\[[^]]+\]\s*", "", symbol)
        key = (symbol or "[unknown]", location or "??:0", dso or "[unknown]")
        counts[key] = counts.get(key, 0) + int(samples)
    total = sum(counts.values())
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [Hotspot(*key, count, count / total * 100)
            for key, count in ranked[:5] if count], total


@dataclass(frozen=True)
class SourcePreview:
    path: Path
    line: int
    start: int
    code: str


def source_preview(location: str) -> SourcePreview | None:
    """Read only the path recorded by perf; don't guess another same-named file."""
    match = re.fullmatch(r"(.+):(\d+)(?: \(discriminator \d+\))?", location)
    if not match or int(match[2]) == 0:
        return None
    path, line = Path(match[1]), int(match[2])
    try:
        # Stream the file: a preview should not read an arbitrarily large file.
        lines = []
        number = 0
        start = max(1, line - 2)
        with path.open(encoding="utf-8", errors="replace") as source:
            for number, text in enumerate(source, 1):
                if start <= number <= line + 2:
                    lines.append(text)
                if number >= line + 2:
                    break
        if number < line:
            return None
    except OSError:
        return None
    return SourcePreview(path, line, start, "".join(lines))
