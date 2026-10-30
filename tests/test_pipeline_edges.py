from datetime import datetime, timezone

import requests

from pricewatch.agents.extractor import extract_corner
from pricewatch.agents.normalizer import parse_money
from pricewatch.agents.watcher import evaluate
from pricewatch.http import Client
from pricewatch.models import Observation


def observation(price, day, currency="USD"):
    return Observation("corner", "p", "u", "item", price, currency,
                       observed_at=f"2026-09-{day:02}T00:00:00+00:00")


def test_malformed_corner_jsonld_falls_back_to_visible_html():
    result = extract_corner(Client(respect_robots=False), "corner", "https://example.test/p/1",
                            '<script type="application/ld+json">{broken</script><h1>Widget</h1>')
    assert result.name == "Widget"
    assert result.price_cents is None


def test_norwegian_and_indian_grouping():
    assert parse_money("kr 2 926,00", "NOK") == (292600, "NOK")
    assert parse_money("₹99,398", "INR") == (9939800, "INR")


def test_below_median_uses_all_observations_and_wins():
    history = [observation(10000, 1), observation(5000, 2), observation(10000, 3)]
    cur = observation(4000, 4)
    alerts = evaluate(history, [cur], [
        {"type": "drop_pct", "pct": 10},
        {"type": "below_median", "pct": 15, "window_days": 30},
    ])
    assert len(alerts) == 1
    assert alerts[0].rule == "below_median"
    assert alerts[0].previous_cents == 10000


def test_drop_uses_previous_price_and_requires_same_currency():
    alerts = evaluate([observation(10000, 1)], [observation(8000, 2)], [{"type": "drop_pct", "pct": 10}])
    assert "20.0% drop" in alerts[0].message
    assert evaluate([observation(10000, 1, "USD")], [observation(8000, 2, "EUR")],
                    [{"type": "drop_pct", "pct": 10}]) == []


def test_retry_after_is_respected_for_429(monkeypatch):
    client = Client(respect_robots=False, retries=1)
    responses = [
        type("Response", (), {"status_code": 429, "headers": {"Retry-After": "2"}})(),
        type("Response", (), {"status_code": 200, "headers": {}})(),
    ]
    monkeypatch.setattr(client.session, "request", lambda *a, **k: responses.pop(0))
    waits = []
    monkeypatch.setattr("pricewatch.http.time.sleep", waits.append)
    result = client.get("https://example.test/page")
    assert result.status_code == 200
    assert waits == [2.0]


def test_robots_disallow_blocks_page_request(monkeypatch):
    client = Client(respect_robots=True)
    response = type("Response", (), {"status_code": 200, "text": "User-agent: *\nDisallow: /private"})()
    calls = []
    monkeypatch.setattr(client.session, "get", lambda *a, **k: (calls.append(a[0]) or response))
    try:
        client.get("https://example.test/private/product")
    except PermissionError:
        pass
    else:
        raise AssertionError("robots disallow must block the page fetch")
    assert calls == ["https://example.test/robots.txt"]
