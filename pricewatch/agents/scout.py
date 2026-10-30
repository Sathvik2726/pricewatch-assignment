"""Scout agent: finds product URLs on a store's index page.

Returns absolute URLs. If the index can't be read (challenge page, rate limit, robot check),
returns an empty list and a note; the orchestrator records the note.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import Client
from .normalizer import parse_money

PRODUCT_PATH = {
    "corner": r"/stores/corner/p/[^/?#]+$",
    "maple": r"/stores/maple/products/[^/?#]+$",
    "zon": r"/stores/zon/dp/[^/?#]+$",
    "shield": r"/stores/shield/item/[^/?#]+$",
    "flux": r"/stores/flux/item/[^/?#]+$",
}


def discover(client: Client, store: str, index_url: str) -> tuple[list[str], list[str]]:
    urls, notes, hints = discover_with_hints(client, store, index_url)
    # Keep the historical two-value return contract; the orchestrator consumes these
    # optional catalog hints after discovery without changing callers' expectations.
    client._pricewatch_catalog_hints = hints  # type: ignore[attr-defined]
    return urls, notes


def discover_with_hints(client: Client, store: str, index_url: str) -> tuple[list[str], list[str], dict[str, dict]]:
    """Discover product links and capture an unambiguous catalog-card offer when present.

    Card prices are a fallback for stores that withhold their detail-page offer from a
    browser-like crawl. The detail page always has precedence in the orchestrator.
    """
    notes: list[str] = []
    r = client.get(index_url)
    if r.status_code != 200:
        notes.append(f"index returned HTTP {r.status_code}")
        return [], notes, {}
    if "Are you a human" in r.text:
        notes.append("index served a robot check")
        return [], notes, {}
    soup = BeautifulSoup(r.text, "html.parser")
    pat = re.compile(PRODUCT_PATH.get(store, r".*"))
    urls: list[str] = []
    hints: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(index_url, a["href"])
        if pat.search(href) and href not in urls:
            urls.append(href)
            if store == "zon":
                hint = _single_card_offer(a, index_url, pat)
                if hint:
                    hints[href] = {**hint, "name": a.get_text(" ", strip=True)}
    return urls, notes, hints


def _single_card_offer(anchor, base_url: str, product_pattern: re.Pattern) -> dict | None:
    """Return a card price only when its closest product container has one offer amount."""
    money = re.compile(r"(?:US\$|[$€£₹]|\b(?:USD|EUR|GBP|INR|NOK|SEK|DKK|CAD|AUD)\b)\s*[\d][\d\s,.]*(?:[.,]\s*\d{1,2})?", re.I)
    for parent in anchor.parents:
        if parent.name in {"body", "html", "[document]"}:
            break
        links = [a for a in parent.find_all("a", href=True) if product_pattern.search(urljoin(base_url, a["href"]))]
        if len(links) > 1:
            break
        matches = money.findall(parent.get_text(" ", strip=True))
        if len(matches) == 1:
            price, currency = parse_money(matches[0], "USD")
            if price is not None:
                return {"price_cents": price, "currency": currency or "USD"}
    return None
