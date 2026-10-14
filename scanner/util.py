"""Small shared helpers: dates, safe math, JSON IO, logging."""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger("scanner")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(asctime)s %(levelname)s %(message)s")


def parse_date(s: Any) -> Optional[date]:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def days_between(a: Any, b: Any) -> Optional[int]:
    da, db = parse_date(a), parse_date(b)
    if da is None or db is None:
        return None
    return (db - da).days


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def safe_div(a: Any, b: Any) -> Optional[float]:
    if not is_num(a) or not is_num(b) or b == 0:
        return None
    return a / b


def growth(cur: Any, prev: Any) -> Optional[float]:
    """Growth rate; undefined when the base is non-positive."""
    if not is_num(cur) or not is_num(prev) or prev <= 0:
        return None
    return cur / prev - 1.0


def cagr(end: Any, start: Any, years: float) -> Optional[float]:
    if not is_num(end) or not is_num(start) or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def lerp_score(x: Any, lo: float, hi: float) -> Optional[float]:
    """Map x linearly to 0..1 between lo and hi (works for hi < lo too)."""
    if not is_num(x):
        return None
    if hi == lo:
        return 1.0 if x >= hi else 0.0
    return clamp((x - lo) / (hi - lo), 0.0, 1.0)


def median(xs: Iterable[float]) -> Optional[float]:
    v = sorted(x for x in xs if is_num(x))
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def stdev(xs: Iterable[float]) -> Optional[float]:
    v = [x for x in xs if is_num(x)]
    if len(v) < 2:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def percentile_rank(value: Any, xs: Iterable[float]) -> Optional[float]:
    v = [x for x in xs if is_num(x)]
    if not is_num(value) or not v:
        return None
    below = sum(1 for x in v if x < value)
    equal = sum(1 for x in v if x == value)
    return (below + 0.5 * equal) / len(v)


def rnd(x: Any, n: int = 4) -> Any:
    if is_num(x):
        return round(float(x), n)
    return None if isinstance(x, float) else x


def clean(obj: Any) -> Any:
    """Recursively make an object JSON-safe (NaN/inf -> None, dates -> ISO)."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def write_json(path: Any, obj: Any, compact: bool = False) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(clean(obj), fh, separators=(",", ":"))
        else:
            json.dump(clean(obj), fh, indent=1)
    os.replace(tmp, p)


def read_json(path: Any, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("could not read %s: %s", p, exc)
        return default
