"""Watcher agent: evaluates price rules against observations for each product."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Iterable

from ..models import Alert, Observation

log = logging.getLogger(__name__)


def _key(o: Observation) -> tuple[str, str]:
    return (o.store, o.product_id)


def _parse_time(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _by_product(history: Iterable[Observation]) -> dict[tuple[str, str], list[Observation]]:
    out: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in history:
        out[_key(o)].append(o)
    for observations in out.values():
        observations.sort(key=lambda o: _parse_time(o.observed_at) or datetime.min.replace(tzinfo=timezone.utc))
    return out


def rule_drop_pct(prev: Observation, cur: Observation, pct: float) -> Alert | None:
    """Public helper retained for callers and tests."""
    if prev.price_cents is None or cur.price_cents is None:
        return None
    if cur.price_cents >= prev.price_cents or cur.currency != prev.currency:
        return None
    drop = (prev.price_cents - cur.price_cents) / prev.price_cents * 100
    if drop >= pct:
        return Alert(store=cur.store, product_id=cur.product_id, rule="drop_pct",
                     message=f"{cur.name or cur.product_id}: {prev.price_cents} -> {cur.price_cents} ({drop:.1f}% drop)",
                     previous_cents=prev.price_cents, current_cents=cur.price_cents, observed_at=cur.observed_at)
    return None


def _drop_rule(prior: list[Observation], cur: Observation, cfg: dict) -> Alert | None:
    if cur.price_cents is None:
        return None
    prev = next((item for item in reversed(prior)
                 if item.price_cents is not None and item.currency == cur.currency), None)
    if prev is None:
        if any(item.price_cents is not None for item in prior):
            log.warning("drop_pct skipped for %s: no prior price in current currency", cur.product_id)
        return None
    return rule_drop_pct(prev, cur, float(cfg.get("pct", 10)))


def _daily_closes(items: list[Observation]) -> list[Observation]:
    closes: dict[object, Observation] = {}
    for item in items:
        dt = _parse_time(item.observed_at)
        if dt is not None and item.price_cents is not None:
            closes[dt.date()] = item
    return list(closes.values())


def _below_median_rule(prior: list[Observation], cur: Observation, cfg: dict) -> Alert | None:
    if cur.price_cents is None:
        return None
    end = _parse_time(cur.observed_at)
    if end is None:
        return None
    window = max(1, int(cfg.get("window_days", 30)))
    start = end - timedelta(days=window)
    recent: list[Observation] = []
    currencies: set[str] = set()
    for item in prior:
        dt = _parse_time(item.observed_at)
        # Exclude the exact start and end boundaries: start < t < end.
        if dt is not None and start < dt < end and item.price_cents is not None:
            recent.append(item)
            currencies.add(item.currency)
    basis = cfg.get("basis", "observations")
    if basis == "daily_close":
        recent = _daily_closes(recent)
    elif basis != "observations":
        log.warning("unknown below_median basis %r; skipping rule", basis)
        return None
    if len(currencies) > 1 or (currencies and cur.currency not in currencies):
        note = "below_median skipped: currency changed within the trailing window"
        if note not in cur.notes:
            cur.notes.append(note)
        log.warning("%s for %s", note, cur.product_id)
        return None
    if len(recent) < 3:
        return None

    prices = sorted(item.price_cents for item in recent)
    middle = len(prices) // 2
    med = (Decimal(prices[middle]) if len(prices) % 2 else
           (Decimal(prices[middle - 1]) + Decimal(prices[middle])) / 2)
    pct = Decimal(str(cfg.get("pct", 15)))
    # Compare against the exact median before rounding it for display.
    if med > 0 and Decimal(cur.price_cents) * 100 <= med * (Decimal(100) - pct):
        rounded_median = int(med.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return Alert(cur.store, cur.product_id, "below_median",
                     f"{cur.name or cur.product_id}: {cur.price_cents} is below median {rounded_median}",
                     rounded_median, cur.price_cents, cur.observed_at)
    return None


Rule = Callable[[list[Observation], Observation, dict], Alert | None]
RULES: dict[str, tuple[int, Rule]] = {
    "drop_pct": (10, _drop_rule),
    "below_median": (20, _below_median_rule),
}


def evaluate(history: list[Observation], new: list[Observation], rules: list[dict]) -> list[Alert]:
    """Evaluate configured rules; return the highest-priority alert per observation.

    The default below_median basis is all valid observations, matching ISSUE-2 and the
    decision log. Use basis=daily_close to select daily closing prices explicitly.
    """
    alerts: list[Alert] = []
    hist = _by_product(history)
    for cur in sorted(new, key=lambda o: _parse_time(o.observed_at) or datetime.min.replace(tzinfo=timezone.utc)):
        prior = hist.get(_key(cur), [])
        candidates: list[tuple[int, int, Alert]] = []
        for index, rule in enumerate(rules):
            typ = rule.get("type")
            registered = RULES.get(typ)
            if registered is None:
                log.warning("unknown alert rule type %r; skipping", typ)
                continue
            priority, fn = registered
            alert = fn(prior, cur, rule)
            if alert is not None:
                candidates.append((priority, -index, alert))
        if candidates:
            alerts.append(max(candidates, key=lambda item: (item[0], item[1]))[2])
        hist[_key(cur)] = prior + [cur]
    return alerts
