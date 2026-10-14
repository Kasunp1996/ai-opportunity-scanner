"""Throttled HTTP client with retries (used for SEC EDGAR, which allows max 10 requests/second)."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

import requests

from .util import log


class ThrottledClient:
    def __init__(self, user_agent: str, max_per_second: float = 8.0, timeout: float = 30.0):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.min_interval = 1.0 / max_per_second
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last = 0.0
        self.requests_made = 0

    def _wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._last + self.min_interval - now
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()

    def get_json(self, url: str, retries: int = 4) -> Optional[Dict[str, Any]]:
        r = self.get(url, retries)
        if r is None:
            return None
        try:
            return r.json()
        except ValueError:
            log.warning("non-JSON response from %s", url)
            return None

    def get_text(self, url: str, retries: int = 3) -> Optional[str]:
        r = self.get(url, retries)
        return r.text if r is not None else None

    def get(self, url: str, retries: int = 4) -> Optional[requests.Response]:
        backoff = 1.0
        for attempt in range(retries):
            self._wait()
            try:
                r = self.session.get(url, timeout=self.timeout)
                self.requests_made += 1
            except requests.RequestException as exc:
                log.warning("GET %s failed (%s), attempt %d", url, exc, attempt + 1)
                time.sleep(backoff)
                backoff *= 2
                continue
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                log.warning("GET %s -> %s, backing off %.1fs", url, r.status_code, backoff)
                time.sleep(backoff)
                backoff *= 2
                continue
            log.warning("GET %s -> HTTP %s", url, r.status_code)
            return None
        return None
