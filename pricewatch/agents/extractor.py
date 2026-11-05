"""Extractor agent: one adapter per storefront turns a fetched page into an Observation.

Adapters receive the raw HTML (already fetched by the Client) and the URL. They must not do
their own networking except through the Client they are given, so throttling and cookies
stay in one place.

Adapters are registered by name; `stores.yaml` maps each store to an adapter.

"""
from __future__ import annotations

import json
import hashlib
import copy
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import requests
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
    # Evaluate selector priority explicitly; select_one on a combined selector uses
    # document order, which can let a head meta tag or comparison price win.
    price_el = None
    selectors = [
        ".price--sale", "[data-current-price]", ".price__current", "[itemprop='price']",
        ".price .price", ".price [class*='price']",
    ]
    for selector in selectors:
        for candidate in soup.select(selector):
            classes = " ".join(candidate.get("class", [])) if isinstance(candidate.get("class"), list) else str(candidate.get("class", ""))
            if re.search(r"compare|was|old|list[-_ ]?price", classes, re.I):
                continue
            if candidate.find(["s", "del", "strike"]):
                continue
            if candidate.find_parent(class_=re.compile(r"compare|was|old|list[-_ ]?price", re.I)):
                continue
            candidate_text = candidate.get("content") or candidate.get("data-current-price") or candidate.get_text(" ", strip=True)
            if re.search(r"(?:/|\bper\b|\bpr\.?\s*)(?:unit|item|kg|g|l|stk\b|piece)", candidate_text, re.I):
                continue
            price_el = candidate
            break
        if price_el is not None:
            break
    if price_el is None:
        price_el = soup.select_one("meta[property='product:price:amount']")
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
    title = soup.find("h1") or soup.find(attrs={"itemprop": "name"})
    name = title.get_text(" ", strip=True) if title else ""
    m = re.search(r"Pack of (\d+)", name)
    pack = int(m.group(1)) if m else 1
    price_cents, avail, variant_links, needs_variant = _zon_main_offer(soup, title, url)
    # A variant must be selected explicitly; an arbitrary first variant may not be
    # the default offer and can create false price changes.
    if price_cents is None and needs_variant and variant_links and "variant=" not in url:
        for variant_url in variant_links[:1]:
            try:
                response = client.get(variant_url, headers={
                    "Referer": url, "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1",
                })
            except (PermissionError, requests.RequestException):
                break
            if response.status_code == 200:
                variant_soup = BeautifulSoup(response.text, "html.parser")
                variant_title = variant_soup.find("h1") or title
                value, variant_avail, _, _ = _zon_main_offer(variant_soup, variant_title, url)
                if value is not None:
                    price_cents, avail = value, variant_avail
                    if variant_title:
                        name = variant_title.get_text(" ", strip=True)
                    break
    return Observation(
        store=store, product_id=_pid_from_url(url), url=url, name=re.sub(r"\s*\(Pack of \d+\)", "", name),
        price_cents=price_cents, currency="USD", availability=avail, pack_size=pack,
        unit_price_cents=unit_price(price_cents, pack),
        notes=[] if price_cents is not None else ["variant selection required or no primary offer found"],
    )


def _zon_main_offer(soup: BeautifulSoup, title, base_url: str) -> tuple[int | None, str, list[str], bool]:
    """Read the primary offer area before the separately labeled seller list."""
    if title is None:
        return None, "unknown", [], False
    chunks: list[str] = []
    variants: list[str] = []
    for sibling in title.next_siblings:
        if not getattr(sibling, "name", None):
            continue
        if sibling.name in {"h2", "h3"} and "seller" in sibling.get_text(" ", strip=True).lower():
            break
        if sibling.find(lambda tag: tag.name in {"h2", "h3"} and "seller" in tag.get_text(" ", strip=True).lower()):
            break
        for link in sibling.find_all("a", href=True):
            if re.search(r"(?:\?|&)variant=[^&]+", link["href"]):
                selected = (link.get("aria-selected") == "true" or link.get("aria-checked") == "true"
                            or link.has_attr("data-selected") or "selected" in link.get("class", []))
                if selected:
                    variants.append(urljoin(base_url, link["href"]))
        # Drop crossed-out/list prices before considering visible current amounts.
        clean = copy.deepcopy(sibling)
        for old in clean.find_all(["s", "del"]):
            old.decompose()
        chunks.append(clean.get_text(" ", strip=True))
    text = " ".join(chunks)
    has_variant_link = any(re.search(r"(?:\?|&)variant=[^&]+", link.get("href", ""))
                           for sibling in title.next_siblings if getattr(sibling, "name", None)
                           for link in sibling.find_all("a", href=True))
    needs_variant = has_variant_link and bool(re.search(r"select|choose|pick", text, re.I))
    pack_match = re.search(r"\bpack\s+of\s+\d+\s*:\s*([^\n]+?)(?=\s+(?:list\s+price|in\s+stock|out\s+of\s+stock)|$)", text, re.I)
    price_cents = None
    if pack_match:
        price_cents, _ = parse_money(pack_match.group(1), "USD")
    elif not needs_variant:
        match = re.search(r"(?:\$|US\$)\s*[\d,]+(?:\s*\.\s*\d{2})?", text)
        if match:
            price_cents, _ = parse_money(match.group(0), "USD")
    stock_text = text.lower()
    availability = "out_of_stock" if re.search(r"out\s+of\s+stock|unavailable", stock_text) else "in_stock" if re.search(r"in\s+stock", stock_text) else "unknown"
    return price_cents, availability, variants, needs_variant


# --------------------------------------------------------------------------- levels 4-5
@adapter("shield")
def extract_shield(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    state = _embedded_state(soup)
    product = state.get("product") if isinstance(state, dict) else None
    offer = product.get("offer") if isinstance(product, dict) else None
    if isinstance(offer, dict):
        price = _shield_price_cents(offer.get("amount"), offer.get("unit"))
        stock = str(offer.get("stock") or "").upper()
        availability = "in_stock" if stock == "IN_STOCK" else "out_of_stock" if stock == "OUT_OF_STOCK" else "unknown"
        was = offer.get("was")
        compare = _shield_price_cents(was, offer.get("wasUnit", offer.get("unit")))
        return Observation(store, str(product.get("id") or _pid_from_url(url)), url,
                           str(product.get("title") or ""), price,
                           str(offer.get("currency") or "GBP"), compare_at_cents=compare,
                           availability=availability)
    return Observation(store, _pid_from_url(url), url, soup.h1.get_text(" ", strip=True) if soup.h1 else "",
                       None, "GBP", availability="unknown",
                       notes=["Shield offer state missing; no reliable price found"])


def _shield_price_cents(amount, unit) -> int | None:
    if amount is None:
        return None
    try:
        value = Decimal(str(amount))
        unit_text = str(unit or "").lower()
        if unit_text == "major" or (not unit_text and value != value.to_integral_value()):
            value *= 100
        elif unit_text not in {"", "minor"}:
            return None
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return None


@adapter("flux")
def extract_flux(client: Client, store: str, url: str, html: str) -> Observation:
    soup = BeautifulSoup(html, "html.parser")
    fallback = _extract_semantic_page(store, url, soup, "USD")
    # The app publishes the API contract in a same-origin script. Read that script rather
    # than guessing endpoints, signatures, or price units; all calls still use Client.
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    contract = None
    for script in soup.find_all("script", src=True):
        script_url = urljoin(url, script["src"])
        parsed_script = urlparse(script_url)
        if (parsed_script.scheme, parsed_script.netloc) != (urlparse(url).scheme, urlparse(url).netloc):
            continue
        cache_key = f"flux-bundle:{script_url}"
        bundle = client.cache.get(cache_key)
        if not isinstance(bundle, str):
            try:
                bundle_response = client.get(script_url, headers={"Referer": url, "Accept": "text/javascript,*/*;q=0.8"})
            except (PermissionError, requests.RequestException):
                continue
            if bundle_response.status_code != 200:
                continue
            bundle = bundle_response.text
            client.cache[cache_key] = bundle
        try:
            endpoint, query = _flux_contract(bundle)
            contract = (bundle, endpoint, query)
            break
        except ValueError:
            continue
    if contract is None:
        fallback.notes.append("Flux API contract not found in same-origin application scripts")
        return fallback
    bundle, endpoint, query = contract
    try:
        signature = _flux_signature(bundle, _product_sku(soup, url))
        build = soup.select_one('meta[name="flux-build"]')
        if build is None or not build.get("content"):
            raise ValueError("Flux build identifier missing")
        sku = _product_sku(soup, url)
        api_url = urljoin(origin, endpoint)
        payload = None
        for attempt in range(3):
            response = client.post(api_url, headers={
                "Referer": url, "Origin": origin, "Accept": "application/json",
                "Content-Type": "application/json", "X-Flux-Sig": signature,
                "X-Flux-Build": build["content"],
            }, json={"query": query, "variables": {"sku": sku}})
            if response.status_code != 200:
                fallback.notes.append(f"Flux offer API returned HTTP {response.status_code}")
                return fallback
            payload = response.json()
            product = (payload.get("data") or {}).get("product") if isinstance(payload, dict) else None
            offer = product.get("offer") if isinstance(product, dict) else None
            if not isinstance(offer, dict):
                fallback.notes.append("Flux API response did not contain a product offer")
                return fallback
            if not offer.get("stale"):
                break
        if not isinstance(offer, dict) or offer.get("stale"):
            return Observation(store, sku, url, str((product or {}).get("title") or fallback.name), None,
                               str((offer or {}).get("currency") or "USD"),
                               availability="unknown", notes=["Flux API offer remained stale after retries"])
        amount = _flux_price_cents(offer.get("amount"), offer.get("unit"))
        stock = str(offer.get("stock") or "").upper()
        availability = "in_stock" if stock == "IN_STOCK" else "out_of_stock" if stock == "OUT_OF_STOCK" else "unknown"
        return Observation(store=store, product_id=sku, url=url,
                           name=str(product.get("title") or fallback.name), price_cents=amount,
                           currency=str(offer.get("currency") or ""), availability=availability)
    except (ValueError, TypeError, KeyError, PermissionError, requests.RequestException, InvalidOperation) as exc:
        fallback.notes.append(f"Flux API extraction failed: {exc}")
    return fallback


def _product_sku(soup: BeautifulSoup, url: str) -> str:
    product = soup.select_one("[data-sku]")
    return str(product.get("data-sku")) if product and product.get("data-sku") else _pid_from_url(url)


def _embedded_state(soup: BeautifulSoup) -> dict:
    """Parse a page's inert JSON state object without executing its scripts."""
    for script in soup.find_all("script"):
        match = re.search(r"(?:window\.)?__STATE__\s*=\s*(\{.*?\})\s*;", script.get_text(), re.S)
        if not match:
            continue
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _flux_contract(bundle: str) -> tuple[str, str]:
    """Read API endpoint and GraphQL query from the served application bundle."""
    endpoint_match = re.search(r"fetch\((['\"])(.*?)\1\s*,", bundle)
    query_match = re.search(r"query\s*:\s*(['\"])(query\s*\(.*?)(?<!\\)\1", bundle)
    if endpoint_match is None or query_match is None:
        raise ValueError("Flux API contract not found in application script")
    return endpoint_match.group(2), query_match.group(2)


def _flux_signature(bundle: str, sku: str) -> str:
    """Reproduce the signature operation declared by the app's delivered JavaScript."""
    key_match = re.search(r"(?:var|let|const)\s+\w+\s*=\s*\[\s*(['\"])(.*?)\1\s*,\s*(['\"])(.*?)\3\s*\]", bundle)
    separator_match = re.search(r"String\.fromCharCode\((\d+)\)", bundle)
    if key_match is None or separator_match is None:
        raise ValueError("Flux signature parameters not found in application script")
    secret = key_match.group(2) + key_match.group(4)
    separator = chr(int(separator_match.group(1)))
    length_match = re.search(r"\.slice\(0\s*,\s*(\d+|[\w$]+)\s*\)", bundle)
    length = 24
    if length_match:
        length_value = length_match.group(1)
        if length_value.isdigit():
            length = int(length_value)
        else:
            length_decl = re.search(rf"\b{re.escape(length_value)}\s*=\s*(\d+)\b", bundle)
            if length_decl is None:
                raise ValueError("Flux signature length not found in application script")
            length = int(length_decl.group(1))
    algorithm = _flux_hash_algorithm(bundle)
    try:
        hasher = hashlib.new(algorithm.replace("-", "").lower())
    except ValueError as exc:
        raise ValueError(f"unsupported Flux signature hash {algorithm!r}") from exc
    hasher.update(f"{secret}{separator}{sku}".encode("utf-8"))
    digest = hasher.hexdigest()
    return digest[:length]


def _flux_hash_algorithm(bundle: str) -> str:
    """Read the WebCrypto digest name from the served bundle, including split literals."""
    call = re.search(r"crypto\.subtle\.digest\(\s*(\w+|(['\"])(.*?)\2)\s*,", bundle)
    if call is None:
        # Older public-seed bundles and existing fixtures use SHA-256 implicitly.
        return "SHA-256"
    if call.group(3) is not None:
        return call.group(3)
    variable = call.group(1)
    assignment = re.search(rf"\b{re.escape(variable)}\s*=\s*((?:['\"][^'\"]*['\"]\s*\+\s*)*['\"][^'\"]*['\"])", bundle)
    if assignment is None:
        raise ValueError("Flux signature hash name not found in application script")
    pieces = re.findall(r"(['\"])(.*?)\1", assignment.group(1))
    algorithm = "".join(value for _, value in pieces)
    if not algorithm:
        raise ValueError("Flux signature hash name is empty")
    return algorithm


def _flux_price_cents(amount, unit) -> int | None:
    if amount is None:
        return None
    value = Decimal(str(amount))
    if str(unit).lower() == "major":
        value *= 100
    elif str(unit).lower() != "minor":
        raise ValueError(f"unrecognized Flux amount unit {unit!r}")
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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


def extract(client: Client, store: str, adapter_name: str, url: str, html: str) -> Observation:
    fn = ADAPTERS.get(adapter_name)
    if fn is None:
        raise KeyError(f"no adapter named {adapter_name!r}")
    return fn(client, store, url, html)
