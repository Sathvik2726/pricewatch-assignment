"""One HTTP client for the whole swarm.

Responsibilities: browser-like headers, cookie persistence per host, a polite per-host
throttle, and retries on transient failures. Every agent goes through `Client.get`.
"""
from __future__ import annotations

import threading
import time
import hashlib
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 PriceWatch/0.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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
            if origin in self._robots:
                cached = self._robots[origin]
            else:
                # Serialize the first policy fetch per host. Without this, concurrent
                # store workers can stampede robots.txt before the cache is populated.
                robots_url = origin + "/robots.txt"
                try:
                    response = self.session.get(robots_url, timeout=self.timeout)
                    if response.status_code == 404:
                        cached = None
                    elif response.status_code >= 400:
                        cached = RobotFileParser(robots_url)
                        cached.parse(["User-agent: *", "Disallow: /"])
                    else:
                        cached = RobotFileParser(robots_url)
                        cached.parse(response.text.splitlines())
                except requests.RequestException:
                    # Fail closed when robots policy cannot be obtained.
                    cached = RobotFileParser(robots_url)
                    cached.parse(["User-agent: *", "Disallow: /"])
                self._robots[origin] = cached
        return True if cached is None else cached.can_fetch(self.session.headers.get("User-Agent", "*"), url)

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        origin = f"{urlparse(url).scheme}://{host}"
        parser = self._robots.get(origin)
        robot_delay = parser.crawl_delay(self.session.headers.get("User-Agent", "*")) if parser else None
        interval = max(self.min_interval, float(robot_delay or 0.0))
        with self._lock:
            now = time.monotonic()
            slot = max(self._last.get(host, now), now)
            self._last[host] = slot + interval
            wait = slot - now
        if wait > 0:
            time.sleep(wait)

    def request(self, method: str, url: str, *, _allow_challenge: bool = True, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        if self.respect_robots and not urlparse(url).path.endswith("/robots.txt") and not self._allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        last_exc: Exception | None = None
        attempt = 0
        challenges = 0
        while attempt <= self.retries:
            self._throttle(url)
            try:
                r = self.session.request(method, url, **kw)
            except requests.RequestException as e:  # network blip
                last_exc = e
                if attempt < self.retries:
                    time.sleep(min(8.0, 0.5 * (2 ** attempt)))
                    attempt += 1
                    continue
                break
            if _allow_challenge and self._is_script_challenge(r):
                if challenges < 2 and self._solve_script_challenge(url, r.text):
                    challenges += 1
                    continue
                return r
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
                attempt += 1
                continue
            return r
        if last_exc:
            raise last_exc
        return r  # type: ignore[possibly-undefined]

    @staticmethod
    def _is_script_challenge(response: requests.Response) -> bool:
        body = getattr(response, "text", "")
        return response.status_code == 503 and all(x in body for x in ("data-s=", "data-p=", "fetch("))

    def _solve_script_challenge(self, url: str, html: str) -> bool:
        """Answer the mock store's JavaScript proof using its delivered challenge values."""
        salt = re.search(r"\bdata-s\s*=\s*(['\"])(.*?)\1", html)
        proof = re.search(r"\bdata-p\s*=\s*(['\"])(.*?)\1", html)
        endpoint = re.search(r"fetch\(\s*(['\"])(.*?)\1", html)
        if not salt or not proof or not endpoint:
            return False
        s, p = salt.group(2), proof.group(2)
        answer = hashlib.sha256(f"{s}|{p}".encode("utf-8")).hexdigest()[:16]
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        # The served browser code waits before submitting; keep the same delay and
        # route the proof through Client so robots policy and Crawl-delay still apply.
        time.sleep(1.3)
        try:
            response = self.request("POST", urljoin(url, endpoint.group(2)), _allow_challenge=False,
                                    headers={"Referer": url, "Origin": origin, "Content-Type": "application/json"},
                                    json={"s": s, "p": p, "a": answer})
        except (PermissionError, requests.RequestException):
            return False
        return 200 <= response.status_code < 300

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)
