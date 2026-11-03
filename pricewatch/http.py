"""One HTTP client for the whole swarm.

Responsibilities: browser-like headers, cookie persistence per host, a polite per-host
throttle, and retries on transient failures. Every agent goes through `Client.get`.
"""
from __future__ import annotations

import ast
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


def script_challenge_answer(script: str, salt: str, proof: str) -> tuple[str, float]:
    """Evaluate the small digest recipe served by the mock Shield challenge."""
    variables: dict[str, str] = {}
    salt_name = re.search(r"\b([\w$]+)\s*=\s*[\w$]+\.dataset\.s\b", script)
    proof_name = re.search(r"\b([\w$]+)\s*=\s*[\w$]+\.dataset\.p\b", script)
    if salt_name:
        variables[salt_name.group(1)] = salt
    if proof_name:
        variables[proof_name.group(1)] = proof

    for match in re.finditer(
        r"\b([\w$]+)\s*=\s*\[\s*\.\.\.\s*([\w$]+)\s*\]\.reverse\(\)\.join\(\s*(['\"])(.*?)\3\s*\)",
        script,
    ):
        target, source, _, joiner = match.groups()
        if source in variables:
            variables[target] = joiner.join(reversed(variables[source]))

    for match in re.finditer(r"\b([\w$]+)\s*=\s*String\.fromCharCode\((\d+)\)", script):
        variables[match.group(1)] = chr(int(match.group(2)))

    digest_call = re.search(r"crypto\.subtle\.digest\(\s*(?:(['\"])(.*?)\1|([\w$]+))\s*,", script)
    algorithm = "SHA-256"
    if digest_call:
        if digest_call.group(2) is not None:
            algorithm = digest_call.group(2)
        else:
            variable = digest_call.group(3)
            assignment = re.search(
                rf"\b{re.escape(variable)}\s*=\s*((?:['\"][^'\"]*['\"]\s*\+\s*)*['\"][^'\"]*['\"])",
                script,
            )
            if assignment is None:
                raise ValueError("Shield challenge hash name not found in script")
            string_parts = re.findall(r"(['\"])(.*?)\1", assignment.group(1))
            algorithm = "".join(value for _, value in string_parts)

    payload_expression = _call_argument(script, ".encode")
    if payload_expression is not None:
        payload = _evaluate_challenge_expression(payload_expression, variables)
    elif salt_name is None and proof_name is None:
        # Compatibility with the original mock challenge and its test fixture.
        payload = f"{salt}|{proof}"
    else:
        raise ValueError("Shield challenge digest input not found in script")

    try:
        digest = hashlib.new(algorithm.replace("-", "").lower(), payload.encode("utf-8")).hexdigest()
    except ValueError as exc:
        raise ValueError(f"unsupported Shield challenge hash {algorithm!r}") from exc

    slice_match = re.search(r"\.slice\(\s*(\d+)\s*,\s*(\d+)\s*\)", script)
    start, end = (int(slice_match.group(1)), int(slice_match.group(2))) if slice_match else (0, 16)
    delay_match = re.search(r"setTimeout\(\s*[\w$]+\s*,\s*(\d+)\s*\)", script)
    delay = min(int(delay_match.group(1)) / 1000, 60.0) if delay_match else 1.3
    return digest[start:end], delay


def _evaluate_challenge_expression(expression: str, variables: dict[str, str]) -> str:
    expression = expression.strip()
    if expression.startswith("`") and expression.endswith("`"):
        body = expression[1:-1]
        return re.sub(r"\$\{([\w$]+)\}", lambda m: variables[m.group(1)], body)

    parts = re.finditer(
        r"String\.fromCharCode\(\s*(\d+)\s*\)|(['\"](?:\\.|[^'\"])*['\"]|[\w$]+)",
        expression,
    )
    values: list[str] = []
    position = 0
    for match in parts:
        if not re.fullmatch(r"\s*\+?\s*", expression[position:match.start()]):
            raise ValueError("unsupported Shield challenge digest expression")
        if match.group(1) is not None:
            value = chr(int(match.group(1)))
        else:
            token = match.group(2)
            if token[0] in "'\"":
                value = ast.literal_eval(token)
            elif token in variables:
                value = variables[token]
            else:
                raise ValueError(f"Shield challenge variable {token!r} was not resolved")
        values.append(str(value))
        position = match.end()
    if not values or not re.fullmatch(r"\s*\+?\s*", expression[position:]):
        raise ValueError("unsupported Shield challenge digest expression")
    return "".join(values)


def _call_argument(script: str, method: str) -> str | None:
    """Return a JavaScript call's first argument while respecting nested calls/strings."""
    call = re.search(rf"{re.escape(method)}\s*\(", script)
    if call is None:
        return None
    start = call.end()
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(script)):
        char = script[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return script[start:index].strip()
            depth -= 1
    raise ValueError(f"unterminated JavaScript call {method}")


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
        scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.I | re.S)
        script = next((body for body in scripts if "fetch(" in body), "")
        try:
            answer, delay = script_challenge_answer(script, s, p)
        except (ValueError, KeyError, SyntaxError):
            return False
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        # The served browser code waits before submitting; keep the same delay and
        # route the proof through Client so robots policy and Crawl-delay still apply.
        time.sleep(delay)
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
