"""Orchestrate store discovery and extraction under a single scan deadline."""
from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Callable

from .agents import extractor, scout
from .http import Client
from .models import Observation

log = logging.getLogger("pricewatch")
Observer = Callable[[Observation], None]


def scan_store(client: Client, base_url: str, store: str, cfg: dict, workers: int = 4,
               deadline: float | None = None, on_observation: Observer | None = None) -> list[Observation]:
    index_url = base_url + cfg["index"]
    urls, notes, price_hints = scout.discover_with_hints(client, store, index_url, deadline)
    for note in notes:
        log.warning("%s: %s", store, note)
    if not urls:
        return []

    def one(url: str) -> Observation:
        response = client.get(url, headers={
            "Referer": index_url,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }, _deadline=deadline)
        obs = extractor.extract(client, store, cfg["adapter"], url, response.text)
        hint = price_hints.get(url) if cfg.get("catalog_card_fallback", False) else None
        if obs.price_cents is None and hint:
            obs.price_cents = hint["price_cents"]
            obs.currency = hint["currency"]
            if not obs.name:
                obs.name = hint.get("name", "")
            obs.unit_price_cents = int(round(obs.price_cents / obs.pack_size)) if obs.pack_size > 1 else None
            obs.notes.append("primary offer unavailable; using the sole price on the catalog card")
        if response.status_code != 200 and obs.price_cents is None:
            obs.notes.append(f"HTTP {response.status_code}")
        return obs

    out: list[Observation] = []
    max_workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending: dict[Future[Observation], str] = {}
        urls_iter = iter(urls)
        exhausted = False
        while pending or not exhausted:
            while not exhausted and len(pending) < max_workers:
                if deadline is not None and time.monotonic() >= deadline:
                    exhausted = True
                    break
                try:
                    url = next(urls_iter)
                except StopIteration:
                    exhausted = True
                    break
                pending[executor.submit(one, url)] = url
            if not pending:
                break
            timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
            done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                url = pending.pop(future)
                try:
                    obs = future.result()
                    out.append(obs)
                    if on_observation is not None:
                        on_observation(obs)
                except Exception as exc:
                    log.warning("%s: failed %s (%s)", store, url, type(exc).__name__)
        for future in pending:
            future.cancel()
    return sorted(out, key=lambda obs: obs.product_id)


def scan(cfg: dict, stores: list[str] | None = None, workers: int = 4, deadline_seconds: float = 200,
         on_observation: Observer | None = None) -> list[Observation]:
    client = Client(min_interval=0.0)
    deadline = time.monotonic() + max(0.0, deadline_seconds)
    client.deadline = deadline
    targets = {k: v for k, v in cfg["stores"].items() if not stores or k in stores}
    out: list[Observation] = []
    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as executor:
        pending: dict[Future[list[Observation]], str] = {
            executor.submit(scan_store, client, cfg["base_url"], name, store_cfg, workers,
                            deadline, on_observation): name
            for name, store_cfg in targets.items()
        }
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, _ = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                store = pending.pop(future)
                try:
                    out.extend(future.result())
                except Exception as exc:
                    log.warning("%s: scan stopped (%s)", store, type(exc).__name__)
        for future in pending:
            future.cancel()
    for future in pending:
        if future.done() and not future.cancelled():
            try:
                out.extend(future.result())
            except Exception as exc:
                log.warning("store scan ended without partial result (%s)", type(exc).__name__)
    return sorted(out, key=lambda obs: (obs.store, obs.product_id))
