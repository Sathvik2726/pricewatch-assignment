from datetime import datetime, timezone
import hashlib

import requests
import pytest

from pricewatch.agents.extractor import extract_corner, extract_flux, extract_shield, extract_zon, _flux_price_cents, _flux_signature, _shield_price_cents
from pricewatch.agents.scout import discover_with_hints
from pricewatch.agents.normalizer import parse_money
from pricewatch.agents.watcher import evaluate
from pricewatch.http import Client, RobotsPolicy, script_challenge_answer
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
    assert parse_money("kr 9 114,40 ekskl. mva", "NOK") == (911440, "NOK")
    assert parse_money("kr 2 066,-", "NOK") == (206600, "NOK")
    assert parse_money("1.299 €", "EUR") == (129900, "EUR")
    assert parse_money("ABC 10.00", "ABC") == (1000, "ABC")


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


def test_retry_after_over_limit_returns_response_without_retrying(monkeypatch):
    client = Client(respect_robots=False, retries=2, max_retry_after=30)
    response = type("Response", (), {"status_code": 429, "headers": {"Retry-After": "120"}})()
    calls, waits = [], []
    monkeypatch.setattr(client.session, "request", lambda *a, **k: (calls.append(a) or response))
    monkeypatch.setattr("pricewatch.http.time.sleep", waits.append)
    assert client.get("https://example.test/page") is response
    assert len(calls) == 1
    assert waits == []


def test_client_stops_before_an_expired_scan_deadline():
    client = Client(respect_robots=False)
    with pytest.raises(TimeoutError, match="deadline"):
        client.get("https://example.test/page", _deadline=0)


def test_http_client_hands_shield_challenge_through_same_session(monkeypatch):
    challenge = '<div data-s="salt" data-p="proof"></div><script>fetch("/stores/shield/challenge")</script>'
    replies = [
        type("R", (), {"status_code": 503, "headers": {"Retry-After": "2"}, "text": challenge})(),
        type("R", (), {"status_code": 200, "headers": {}, "text": '{"ok":true}'})(),
        type("R", (), {"status_code": 200, "headers": {}, "text": "product page"})(),
    ]
    calls = []
    client = Client(respect_robots=False, retries=1)
    monkeypatch.setattr(client.session, "request", lambda method, url, **kwargs: (calls.append((method, url, kwargs)) or replies.pop(0)))
    waits = []
    monkeypatch.setattr("pricewatch.http.time.sleep", waits.append)
    response = client.get("https://example.test/stores/shield/")
    assert response.text == "product page"
    assert [call[0] for call in calls] == ["GET", "POST", "GET"]
    assert calls[1][2]["json"]["a"] == __import__("hashlib").sha256(b"salt|proof").hexdigest()[:16]
    assert waits == [1.3]


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


def test_robots_longest_match_wildcard_and_tie_allow():
    policy = RobotsPolicy.parse([
        "User-agent: *", "Allow: /", "Disallow: /stores/zon/dp/*/offers-all",
        "Disallow: /private$", "Allow: /private$",
    ])
    assert not policy.can_fetch("PriceWatch/0.1", "https://example.test/stores/zon/dp/ABC/offers-all")
    assert policy.can_fetch("PriceWatch/0.1", "https://example.test/stores/zon/dp/ABC")
    assert policy.can_fetch("PriceWatch/0.1", "https://example.test/private")
    assert policy.can_fetch("PriceWatch/0.1", "https://example.test/private/child")


def test_robots_crawl_delay_limits_requests(monkeypatch):
    client = Client(min_interval=0)
    parser = type("Robots", (), {"crawl_delay": lambda self, user_agent: 1})()
    client._robots["https://example.test"] = parser
    clock = iter([10.0, 10.1])
    waits = []
    monkeypatch.setattr("pricewatch.http.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("pricewatch.http.time.sleep", waits.append)
    client._throttle("https://example.test/one")
    client._throttle("https://example.test/two")
    assert waits == [pytest.approx(0.9)]


def test_flux_major_and_minor_units():
    assert _flux_price_cents("1032.00", "major") == 103200
    assert _flux_price_cents(103200, "minor") == 103200


def test_flux_signature_uses_hash_algorithm_declared_by_bundle():
    bundle = '''var _0x1=["fx_c145c8","5d3fa08693fbd3"],_0x2=String.fromCharCode(47),_0x3=32,_0x4="SHA-"+"1";
    function _0x5(e){return crypto.subtle.digest(_0x4,new TextEncoder().encode(e))}
    async function _0x6(e){return _0x5(_0x1.join("")+_0x2+e).slice(0,_0x3)}'''
    expected = hashlib.sha1(b"fx_c145c85d3fa08693fbd3/FLX-3C3T04").hexdigest()[:32]
    assert _flux_signature(bundle, "FLX-3C3T04") == expected


def test_flux_reads_served_contract_and_maps_offer(monkeypatch):
    bundle = '''var _0x1=["salt-a","salt-b"],_0x2=String.fromCharCode(124),_0x3=24;
    fetch("/stores/flux/api/graphql",{body:JSON.stringify({query:"query($sku:String!){product(sku:$sku){sku title offer{amount unit currency stock stale}}}"})});'''
    client = Client(respect_robots=False)
    monkeypatch.setattr(client, "get", lambda *a, **k: type("Response", (), {"status_code": 200, "text": bundle})())
    captured = {}
    def post(*args, **kwargs):
        captured.update(kwargs)
        return type("Response", (), {"status_code": 200, "json": lambda self: {"data": {"product": {
            "sku": "FLX-1", "title": "Flux Lamp", "offer": {"amount": "59.95", "unit": "major",
            "currency": "USD", "stock": "OUT_OF_STOCK", "stale": False}}}}})()
    monkeypatch.setattr(client, "post", post)
    html = '<meta name="flux-build" content="build-x"><div data-sku="FLX-1"><h1>Lamp</h1></div><script src="/stores/flux/bundle.js"></script>'
    result = extract_flux(client, "flux", "https://example.test/stores/flux/item/FLX-1", html)
    assert captured["json"]["variables"]["sku"] == "FLX-1"
    assert captured["headers"]["X-Flux-Sig"] == _flux_signature(bundle, "FLX-1")
    assert result.price_cents == 5995 and result.currency == "USD"
    assert result.name == "Flux Lamp" and result.availability == "out_of_stock"
    assert result.notes == []


def test_zon_requires_explicitly_selected_variant(monkeypatch):
    client = Client(respect_robots=False)
    visited = []
    variant_page = '''<h1>Water Bottle (Pack of 12)</h1>
    <div>Colour: <a href="?variant=black" aria-selected="true">Black</a></div>
    <div><div>$ 54 . 37 / count</div><div>Pack of 12: $ 652 . 44</div>
    <div>List Price: <s>$804.37</s></div><div>In Stock</div></div>
    <div><h3>Other sellers on Zon</h3><div>$ 842 . 72 from Seller</div></div>'''
    monkeypatch.setattr(client, "get", lambda url, **kwargs: (
        visited.append(url) or type("Response", (), {"status_code": 200, "text": variant_page})()
    ))
    base_url = "https://example.test/stores/zon/dp/ABC123"
    initial = '''<h1>Water Bottle (Pack of 12)</h1><div>Colour:
    <a href="?variant=black">Black</a></div><div>Select a colour to see price</div>
    <div><h3>Other sellers on Zon</h3><div>$ 842 . 72 from Seller</div></div>'''
    result = extract_zon(client, "zon", base_url, initial)
    assert visited == []
    assert result.price_cents is None


def test_zon_follows_explicitly_selected_variant(monkeypatch):
    client = Client(respect_robots=False)
    visited = []
    variant_page = '''<h1>Bottle</h1><div>$652.44</div><div>In Stock</div>'''
    monkeypatch.setattr(client, "get", lambda url, **kwargs: (
        visited.append(url) or type("Response", (), {"status_code": 200, "text": variant_page})()
    ))
    initial = '''<h1>Bottle</h1><div><a href="?variant=black" aria-selected="true">Black</a></div>
    <div>Select a colour to see price</div>'''
    result = extract_zon(client, "zon", "https://example.test/stores/zon/dp/ABC", initial)
    assert visited == ["https://example.test/stores/zon/dp/ABC?variant=black"]
    assert result.price_cents == 65244


def test_zon_does_not_substitute_other_sellers_when_no_variant_offer():
    html = '''<h1>Desk Lamp</h1><div>Select a colour to see price</div>
    <div><h3>Other sellers on Zon</h3><div>$ 842 . 72 from Seller</div></div>'''
    result = extract_zon(Client(respect_robots=False), "zon", "https://example.test/stores/zon/dp/NOPE", html)
    assert result.price_cents is None


def test_scout_captures_only_one_unambiguous_zon_card_price(monkeypatch):
    client = Client(respect_robots=False)
    html = '''<div><a href="/stores/zon/dp/ONE">Water Bottle (Pack of 12)</a><span>$ 652.44</span></div>
    <div><a href="/stores/zon/dp/TWO">Headphones</a><span>—</span></div>
    <div><a href="/stores/zon/dp/THREE">Lamp</a><span>$99.00</span><span>$120.00</span></div>'''
    monkeypatch.setattr(client, "get", lambda *a, **k: type("R", (), {"status_code": 200, "text": html})())
    urls, notes, hints = discover_with_hints(client, "zon", "https://example.test/stores/zon/")
    assert len(urls) == 3 and notes == []
    assert hints[urls[0]]["price_cents"] == 65244
    assert urls[1] not in hints
    assert urls[2] not in hints


def test_shield_does_not_duplicate_client_challenge_solver(monkeypatch):
    client = Client(respect_robots=False)
    challenge = '''<div id="cf-c" data-s="salt" data-p="proof"></div>
    <script>fetch("/stores/shield/challenge",{method:"POST"})</script>
    <p>Checking your browser before accessing Shield</p>'''
    page = '''<h1 id="title">Wool Throw</h1><div id="offer"><strong id="price">£1,268.36</strong>
    <span id="stock">In stock</span></div>'''
    result = extract_shield(client, "shield", "https://example.test/stores/shield/item/P1", challenge)
    assert result.price_cents is None
    assert "no reliable price" in result.notes[0]


def test_shield_amount_major_and_minor_units():
    assert _shield_price_cents("1268.36", None) == 126836
    assert _shield_price_cents(1268.36, None) == 126836
    assert _shield_price_cents(126836, None) == 126836
    assert _shield_price_cents("1268.36", "major") == 126836
    assert _shield_price_cents(126836, "minor") == 126836


def test_flux_stale_offer_retries_twice_then_returns_no_price(monkeypatch):
    client = Client(respect_robots=False)
    bundle = '''var _0x1=["salt-a","salt-b"],_0x2=String.fromCharCode(124),_0x3=24;
    fetch("/api/graphql",{body:JSON.stringify({query:"query($sku:String!){product(sku:$sku){offer{amount unit currency stock stale}}}"})});'''
    monkeypatch.setattr(client, "get", lambda *a, **k: type("R", (), {"status_code": 200, "text": bundle})())
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        offer = {"amount": 5995, "unit": "minor", "currency": "USD", "stock": "IN_STOCK", "stale": True}
        return type("R", (), {"status_code": 200, "json": lambda self: {"data": {"product": {"title": "Lamp", "offer": offer}}}})()
    monkeypatch.setattr(client, "post", post)
    html = '<meta name="flux-build" content="b"><div data-sku="S1"><h1>Lamp</h1></div><script src="/bundle.js"></script>'
    result = extract_flux(client, "flux", "https://example.test/item/S1", html)
    assert len(calls) == 3
    assert result.price_cents is None
    assert "remained stale" in result.notes[0]


def test_shield_challenge_uses_served_salt_transform_hash_and_delay():
    script = '''(async()=>{const e=document.getElementById("cf-c"),t=e.dataset.s,n=e.dataset.p,q=[...t].reverse().join(""),
    r=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(q+"/"+n)),
    a=[...new Uint8Array(r)].map(x=>x.toString(16).padStart(2,"0")).join("").slice(8,24);
    await new Promise(z=>setTimeout(z,1300));await fetch("/stores/shield/challenge",{method:"POST"})})();'''
    expected = hashlib.sha256(b"salt/secret").hexdigest()[8:24]
    assert script_challenge_answer(script, "tlas", "secret") == (expected, 1.3)


def test_shield_reads_offer_from_inert_application_state():
    html = '''<h1>Loading…</h1><strong id="price">Loading…</strong>
    <script>window.__STATE__={"product":{"id":"P2","title":"Aurel Wool Throw",
    "offer":{"amount":126836,"currency":"GBP","was":150000,"stock":"IN_STOCK"}}};</script>'''
    result = extract_shield(Client(respect_robots=False), "shield", "https://example.test/item/P2", html)
    assert result.name == "Aurel Wool Throw"
    assert result.price_cents == 126836 and result.compare_at_cents == 150000
