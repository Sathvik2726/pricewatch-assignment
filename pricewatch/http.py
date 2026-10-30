"""One HTTP client for the whole swarm.

Responsibilities: browser-like headers, cookie persistence per host, a polite per-host
throttle, and retries on transient failures. Every agent goes through `Client.get`.
"""
from __future__ import annotations

import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 PriceWatch/0.1",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class Client:
    def __init__(self, min_interval: float = 0.0, timeout: float = 10.0, retries: int = 3, respect_robots: bool = True):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=32)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.min_interval = min_interval  # seconds between requests to the same host
        self.timeout = timeout
        self.retries = retries
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            cached = self._robots.get(origin, ...)
        if cached is ...:
            robots_url = origin + "/robots.txt"
            try:
                response = self.session.get(robots_url, timeout=self.timeout)
                if response.status_code == 404:
                    parser = None
                elif response.status_code >= 400:
                    parser = RobotFileParser(robots_url)
                    parser.parse(["User-agent: *", "Disallow: /"])
                else:
                    parser = RobotFileParser(robots_url)
                    parser.parse(response.text.splitlines())
            except requests.RequestException:
                # Fail closed when robots policy cannot be obtained.
                parser = RobotFileParser(robots_url)
                parser.parse(["User-agent: *", "Disallow: /"])
            with self._lock:
                self._robots[origin] = parser
            cached = parser
        return True if cached is None else cached.can_fetch(self.session.headers.get("User-Agent", "*"), url)

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            wait = self._last.get(host, 0) + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def request(self, method: str, url: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        if self.respect_robots and not urlparse(url).path.endswith("/robots.txt") and not self._allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                r = self.session.request(method, url, **kw)
            except requests.RequestException as e:  # network blip
                last_exc = e
                if attempt < self.retries:
                    time.sleep(min(8.0, 0.5 * (2 ** attempt)))
                continue
            if r.status_code in (429, 503) and attempt < self.retries:
                delay = r.headers.get("Retry-After", "")
                try:
                    seconds = max(0.0, float(delay))
                except ValueError:
                    try:
                        seconds = max(0.0, (parsedate_to_datetime(delay).timestamp() - time.time()))
                    except (TypeError, ValueError, OverflowError):
                        seconds = min(8.0, 0.5 * (2 ** attempt))
                time.sleep(min(seconds, 60.0))
                continue
            return r
        if last_exc:
            raise last_exc
        return r  # type: ignore[possibly-undefined]

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)
