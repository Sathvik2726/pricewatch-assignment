"""Orchestrator: runs scout -> fetch -> extract per store, concurrently across stores.

One Client is shared by all workers so cookies and throttling are global. Concurrency is
across stores AND across products within a store (see `workers`).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from .agents import extractor, scout
from .http import Client
from .models import Observation

log = logging.getLogger("pricewatch")


def scan_store(client: Client, base_url: str, store: str, cfg: dict, workers: int = 4) -> list[Observation]:
    index_url = base_url + cfg["index"]
    urls, notes = scout.discover(client, store, index_url)
    price_hints = getattr(client, "_pricewatch_catalog_hints", {})
    for n in notes:
        log.warning("%s: %s", store, n)
    if not urls:
        return []

    def one(url: str) -> Observation:
        r = client.get(url, headers={
            "Referer": index_url,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        })
        obs = extractor.extract(client, store, cfg["adapter"], url, r.text)
        hint = price_hints.get(url)
        if obs.price_cents is None and hint:
            obs.price_cents = hint["price_cents"]
            obs.currency = hint["currency"]
            if not obs.name:
                obs.name = hint.get("name", "")
            obs.unit_price_cents = int(round(obs.price_cents / obs.pack_size)) if obs.pack_size > 1 else None
            obs.notes.append("primary offer unavailable; using the sole price on the catalog card")
        if r.status_code != 200 and obs.price_cents is None:
            obs.notes.append(f"HTTP {r.status_code}")
        return obs

    out: list[Observation] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, u): u for u in urls}
        for f in as_completed(futs):
            try:
                out.append(f.result())
            except Exception as e:  # keep the swarm alive
                log.exception("%s: failed %s: %s", store, futs[f], e)
    return sorted(out, key=lambda o: o.product_id)


def scan(cfg: dict, stores: list[str] | None = None, workers: int = 4) -> list[Observation]:
    client = Client(min_interval=0.0)
    targets = {k: v for k, v in cfg["stores"].items() if not stores or k in stores}
    out: list[Observation] = []
    with ThreadPoolExecutor(max_workers=len(targets) or 1) as ex:
        futs = {ex.submit(scan_store, client, cfg["base_url"], name, c, workers): name for name, c in targets.items()}
        for f in as_completed(futs):
            out.extend(f.result())
    return out
