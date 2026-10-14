"""Configuration loading. Secrets come only from environment variables, never from files."""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_settings(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    p = Path(path) if path else ROOT / "config" / "settings.yaml"
    with open(p, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg["_root"] = str(ROOT)
    return cfg


def sec_user_agent() -> str:
    """SEC requires a descriptive User-Agent with contact details (name + email)."""
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua or "@" not in ua:
        raise RuntimeError(
            "Set the SEC_USER_AGENT environment variable / GitHub secret to e.g. "
            "'AI Opportunity Scanner your.name@example.com' (SEC fair-access policy)."
        )
    return ua


def anthropic_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return k or None


def load_extra_universe(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    p = ROOT / cfg["universe"].get("extra_tickers_file", "config/universe_extra.csv")
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as fh:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def load_watchlist(cfg: Dict[str, Any]) -> List[str]:
    p = ROOT / cfg.get("alerts", {}).get("watchlist_file", "config/watchlist.txt")
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip().upper()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def public_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Config safe to publish on the dashboard (no secrets live in cfg, but strip internals)."""
    return {k: v for k, v in cfg.items() if not k.startswith("_")}
