"""Command line entry point.

  python -m scanner run      [--force]   live daily run (needs SEC_USER_AGENT; network)
  python -m scanner demo                 offline run on fictional demo companies
  python -m scanner universe             rebuild the SIC-filtered universe now
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT, load_settings, sec_user_agent
from .pipeline import LiveSource, copy_static, run
from .util import setup_logging


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scanner")
    ap.add_argument("command", choices=["run", "demo", "universe"])
    ap.add_argument("--site", default=None, help="output folder for the dashboard (default _site / _site_demo)")
    ap.add_argument("--data", default=None, help="folder for committed history (default data / demo_data)")
    ap.add_argument("--cache", default=str(ROOT / ".cache"), help="SEC download cache")
    ap.add_argument("--config", default=None)
    ap.add_argument("--force", action="store_true", help="run even if today was not a trading day")
    ap.add_argument("--log", default="INFO")
    a = ap.parse_args(argv)
    setup_logging(a.log)
    cfg = load_settings(a.config)

    if a.command == "demo":
        from .demo import DEMO_TODAY, DemoSource
        site = Path(a.site or ROOT / "_site_demo")
        data = Path(a.data or ROOT / "demo_data")
        copy_static(ROOT / "site", site)
        now = datetime(DEMO_TODAY.year, DEMO_TODAY.month, DEMO_TODAY.day, 21, 30, tzinfo=timezone.utc)
        res = run(DemoSource(), cfg, site, data, force=True, now=now)
        (site / "data" / "DEMO").write_text("synthetic demo data", encoding="utf-8")
        print(res["report"]["headline"])
        return 0

    src = LiveSource(sec_user_agent(), Path(a.cache))
    if a.command == "universe":
        from .universe import build_universe
        u = build_universe(src.sec, cfg, str(Path(a.data or ROOT / "data") / "universe.json"), force=True)
        print(f"universe: {u['count']} companies")
        return 0

    site = Path(a.site or ROOT / "_site")
    data = Path(a.data or ROOT / "data")
    copy_static(ROOT / "site", site)
    res = run(src, cfg, site, data, force=a.force)
    if res.get("skipped"):
        print(res["reason"])
        return 0
    print(res["report"]["headline"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
