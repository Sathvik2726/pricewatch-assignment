"""Watcher agent: compares new observations against history and raises alerts.

History is a list of observations ordered by `observed_at`. Rules come from alerts.yaml.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Iterable

from ..models import Alert, Observation


def _key(o: Observation) -> tuple[str, str]:
    return (o.store, o.product_id)


def _by_product(history: Iterable[Observation]) -> dict[tuple[str, str], list[Observation]]:
    out: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in history:
        out[_key(o)].append(o)
    for v in out.values():
        v.sort(key=lambda o: o.observed_at)
    return out


def rule_drop_pct(prev: Observation, cur: Observation, pct: float) -> Alert | None:
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


def evaluate(history: list[Observation], new: list[Observation], rules: list[dict]) -> list[Alert]:
    """Evaluate `rules` for each observation in `new` against `history` (which must not include `new`)."""
    alerts: list[Alert] = []
    hist = _by_product(history)
    for cur in sorted(new, key=lambda o: o.observed_at):
        prior = hist.get(_key(cur), [])
        prev = prior[-1] if prior else None
        fired: Alert | None = None
        for rule in rules:
            typ = rule.get("type")
            if typ == "below_median":
                window = max(1, int(rule.get("window_days", 30)))
                try:
                    end = datetime.fromisoformat(cur.observed_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                start = end - timedelta(days=window)
                recent = []
                currencies = set()
                for item in prior:
                    if item.price_cents is None:
                        continue
                    try:
                        dt = datetime.fromisoformat(item.observed_at.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if start <= dt < end:
                        recent.append(item)
                        currencies.add(item.currency)
                if len(currencies) > 1 or (currencies and cur.currency not in currencies):
                    cur.notes.append("below_median skipped: currency changed within the trailing window")
                    continue
                if len(recent) >= 3 and cur.price_cents is not None:
                    med = int(round(median(i.price_cents for i in recent)))
                    if med > 0 and cur.price_cents <= med * (1 - float(rule.get("pct", 15)) / 100):
                        fired = Alert(cur.store, cur.product_id, "below_median",
                                      f"{cur.name or cur.product_id}: {cur.price_cents} is below median {med}",
                                      med, cur.price_cents, cur.observed_at)
                        break
            elif typ == "drop_pct" and prev is not None and fired is None:
                fired = rule_drop_pct(prev, cur, float(rule.get("pct", 10)))
        if fired:
            alerts.append(fired)
        hist[_key(cur)] = prior + [cur]
    return alerts
