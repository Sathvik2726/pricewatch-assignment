"""Extractor agent: one adapter per storefront turns a fetched page into an Observation.

Adapters receive the raw HTML (already fetched by the Client) and the URL. They must not do
their own networking except through the Client they are given, so throttling and cookies
stay in one place.

Adapters are registered by name; `stores.yaml` maps each store to an adapter.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..http import Client
from ..models import Observation
from .normalizer import parse_money, unit_price

ADAPTERS: dict[str, Callable[[Client, str, str, str], Observation]] = {}


def adapter(name: str):
    def deco(fn):
        ADAPTERS[name] = fn
        return fn
    return deco


def _pid_from_url(url: str) -> str:
    return urlparse(url).path.rstrip("/").split("/")[-1]


# --------------------------------------------------------------------------- level 1
@adapter("corner")
def extract_corner(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    ld = soup.find("script", type="application/ld+json")
    try:
        data = json.loads(ld.string) if ld and ld.string else {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    if isinstance(data, list):
        data = next((x for x in data if isinstance(x, dict) and x.get("@type") == "Product"), {})
    if not isinstance(data, dict):
        data = {}
    offer = data.get("offers", {})
    price_cents, currency = parse_money(str(offer.get("price", "")), offer.get("priceCurrency"))
    was = soup.find("s")
    compare, _ = parse_money(was.get_text(), currency) if was else (None, None)
    return Observation(
        store=store, product_id=data.get("sku") or _pid_from_url(url), url=url,
        name=data.get("name") or (soup.h1.get_text(strip=True) if soup.h1 else ""),
        price_cents=price_cents, currency=currency or "USD", compare_at_cents=compare,
        availability="in_stock" if "InStock" in str(offer.get("availability", "")) else "out_of_stock",
    )


# --------------------------------------------------------------------------- level 2
@adapter("maple")
def extract_maple(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one(".product__title") or soup.find("h1")
    name = title.get_text(" ", strip=True) if title else ""
    # Prefer explicit current/sale price; exclude strike-through/comparison prices.
    price_el = soup.select_one(".price--sale, [data-current-price], meta[property='product:price:amount']")
    if price_el is None:
        for candidate in soup.select(".price .price, .price [class*='price'], [itemprop='price']"):
            if candidate.find_parent(class_=re.compile(r"compare|was|old", re.I)) or candidate.get("class") and any("compare" in c or "old" in c for c in candidate.get("class", [])):
                continue
            price_el = candidate
            break
    price_text = (price_el.get("content") or price_el.get("data-current-price") or price_el.get_text(" ", strip=True)) if price_el else ""
    price_cents, currency = parse_money(price_text, "EUR")
    compare_el = soup.select_one(".price--compare, [class*='compare-at'], del, s")
    compare, _ = parse_money(compare_el.get_text(" ", strip=True), currency) if compare_el else (None, None)
    if compare is not None and price_cents is not None and price_cents >= compare:
        compare = None
    text = soup.get_text(" ", strip=True).lower()
    avail = "out_of_stock" if any(x in text for x in ("sold out", "out of stock", "ikke på lager")) else "in_stock"
    return Observation(
        store=store, product_id=_pid_from_url(url), url=url, name=name,
        price_cents=price_cents, currency=currency or "EUR", compare_at_cents=compare, availability=avail,
    )


# --------------------------------------------------------------------------- level 3
@adapter("zon")
def extract_zon(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    if "Are you a human" in html:
        return Observation(store=store, product_id=_pid_from_url(url), url=url, name="", price_cents=None,
                           currency="USD", notes=["robot check page"])
    # Identify values by semantic attributes and rendered text, never ephemeral classes.
    title = soup.find("h1") or soup.find(attrs={"itemprop": "name"})
    name = title.get_text(" ", strip=True) if title else ""
    m = re.search(r"Pack of (\d+)", name)
    pack = int(m.group(1)) if m else 1
    price_cents: Optional[int] = None
    candidates = []
    for node in soup.select("[itemprop='price'], [data-price], meta[property*='price'], [aria-label*='$'], span, div"):
        if node.name == "script" or node.find(True):
            continue
        raw = node.get("content") or node.get("data-price") or node.get_text(" ", strip=True)
        parsed, _ = parse_money(raw, "USD")
        if parsed is not None and parsed > 0:
            candidates.append((node, parsed))
    # Select a price near its own currency/price label and avoid strike-through / unit values.
    eligible = [(n, p) for n, p in candidates if not n.find_parent(["s", "del"]) and not re.search(r"per\s+(unit|piece)|\/\s*(unit|piece)", n.get_text(" ", strip=True), re.I)]
    if eligible:
        preferred = next(((n, p) for n, p in eligible if n.get("itemprop") == "price" or n.get("data-price") is not None), eligible[0])
        price_cents = preferred[1]
    text = soup.get_text(" ", strip=True).lower()
    avail = "out_of_stock" if "out of stock" in text or "currently unavailable" in text else "in_stock"
    return Observation(
        store=store, product_id=_pid_from_url(url), url=url, name=re.sub(r"\s*\(Pack of \d+\)", "", name),
        price_cents=price_cents, currency="USD", availability=avail, pack_size=pack,
        unit_price_cents=unit_price(price_cents, pack),
        notes=[] if price_cents is not None else ["no price found"],
    )


# --------------------------------------------------------------------------- levels 4-5
@adapter("shield")
def extract_shield(client: Client, store: str, url: str, html: str) -> Observation:
    # The store grants a short-lived browser session after a soft challenge. Revisit once
    # after that handshake, preserving the shared client's cookies and request headers.
    if "please wait" in html.lower() or "checking your browser" in html.lower():
        time.sleep(1.0)
        response = client.get(url, headers={"Referer": url})
        html = response.text
    soup = BeautifulSoup(html, "html.parser")
    return _extract_semantic_page(store, url, soup, "GBP")


@adapter("flux")
def extract_flux(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    obs = _extract_semantic_page(store, url, soup, "USD")
    if obs.price_cents is not None:
        return obs
    # SPA pages sometimes expose an endpoint in their own scripts. Follow only same-origin
    # API references and let the common Client enforce robots, throttling, and retries.
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    scripts = "\n".join(s.get_text() for s in soup.find_all("script") if s.get("src") is None)
    references = re.findall(r"(?:fetch|axios\.(?:get|post))\s*\(\s*['\"]([^'\"]+)['\"]|(?:['\"])(/(?:api|graphql)[^'\"]*)(?:['\"])", scripts)
    endpoints = []
    for pair in references:
        candidate = next((part for part in pair if part), "")
        if candidate and candidate.startswith("/") and candidate not in endpoints:
            endpoints.append(candidate)
    for endpoint in endpoints[:4]:
        try:
            response = client.get(origin + endpoint, headers={"Referer": url, "Accept": "application/json"})
            if response.status_code == 200:
                data = response.json()
                obs = _observation_from_payload(store, url, data, "USD")
                if obs.price_cents is not None:
                    return obs
        except (ValueError, PermissionError, OSError):
            continue
    obs.notes.append("price not present in HTML or discoverable same-origin API")
    return obs


def _extract_semantic_page(store: str, url: str, soup: BeautifulSoup, default_currency: str) -> Observation:
    title = soup.find("h1") or soup.find(attrs={"itemprop": "name"})
    name = title.get_text(" ", strip=True) if title else ""
    price = None
    currency = default_currency
    for el in soup.select("[itemprop='price'], meta[property*='price'], [data-price], [class*='price'], [id*='price']"):
        if el.find(True) or el.find_parent(["s", "del"]):
            continue
        text = el.get("content") or el.get("data-price") or el.get_text(" ", strip=True)
        val, cur = parse_money(text, default_currency)
        if val is not None and val > 0:
            price, currency = val, cur or default_currency
            break
    full_text = soup.get_text(" ", strip=True).lower()
    availability = "out_of_stock" if any(x in full_text for x in ("out of stock", "sold out", "unavailable")) else "in_stock"
    return Observation(store, _pid_from_url(url), url, name, price, currency, availability=availability,
                       notes=[] if price is not None else ["no semantic price found"])


def _observation_from_payload(store: str, url: str, payload, default_currency: str) -> Observation:
    """Find product-like price records in JSON, preferring explicit selling-price fields."""
    preferred = {"price_cents", "priceCents", "salePrice", "sale_price", "sellingPrice", "selling_price", "price"}
    rows = []
    def walk(node):
        if isinstance(node, dict):
            rows.append(node)
            for value in node.values(): walk(value)
        elif isinstance(node, list):
            for value in node: walk(value)
    walk(payload)
    for row in rows:
        keys = [k for k in row if k in preferred]
        if not keys: continue
        key = next((k for k in keys if k != "price"), keys[0])
        raw = row[key]
        currency = str(row.get("currency") or row.get("currencyCode") or default_currency)
        try:
            amount = int(raw) if "cents" in key.lower() else int(round(float(raw) * 100))
        except (TypeError, ValueError):
            continue
        if amount <= 0: continue
        return Observation(store, str(row.get("sku") or row.get("id") or _pid_from_url(url)), url,
                           str(row.get("name") or row.get("title") or ""), amount, currency,
                           availability=str(row.get("availability") or "unknown"))
    return Observation(store, _pid_from_url(url), url, "", None, default_currency, notes=["no offer found in API response"])


def extract(client: Client, store: str, adapter_name: str, url: str, html: str) -> Observation:
    fn = ADAPTERS.get(adapter_name)
    if fn is None:
        raise KeyError(f"no adapter named {adapter_name!r}")
    return fn(client, store, url, html)
