from pricewatch.agents.watcher import evaluate
from pricewatch.models import Observation


def obs(price, at, pid="P1"):
    return Observation(store="corner", product_id=pid, url="u", name="n", price_cents=price, currency="USD", observed_at=at)


def test_drop_fires():
    alerts = evaluate([obs(10000, "2026-09-01T00:00:00")], [obs(8000, "2026-09-02T00:00:00")], [{"type": "drop_pct", "pct": 10}])
    assert len(alerts) == 1 and alerts[0].rule == "drop_pct"


def test_no_history_no_alert():
    assert evaluate([], [obs(8000, "2026-09-02T00:00:00")], [{"type": "drop_pct", "pct": 10}]) == []


def test_increase_no_alert():
    assert evaluate([obs(8000, "2026-09-01T00:00:00")], [obs(10000, "2026-09-02T00:00:00")], [{"type": "drop_pct", "pct": 10}]) == []


def test_drop_pct_message_percentage():
    # Regression test for the alert copy — keep in sync with rule_drop_pct.
    alerts = evaluate([obs(10000, "2026-09-01T00:00:00")], [obs(8000, "2026-09-02T00:00:00")], [{"type": "drop_pct", "pct": 10}])
    assert "20.0% drop" in alerts[0].message


def test_below_median_mixed_currency_skips_with_observation_note():
    current = obs(5000, "2026-09-04T00:00:00")
    current.currency = "EUR"
    history = [obs(10000, f"2026-09-0{day}T00:00:00") for day in (1, 2, 3)]
    assert evaluate(history, [current], [{"type": "below_median", "pct": 15, "window_days": 30}]) == []
    assert any("currency changed" in note for note in current.notes)


def test_below_median_exact_percentage_boundary_fires():
    history = [obs(price, f"2026-09-0{day}T00:00:00Z") for day, price in enumerate((170, 170, 170), 1)]
    current = obs(119, "2026-09-04T00:00:00Z")
    alerts = evaluate(history, [current], [{"type": "below_median", "pct": 30}])
    assert len(alerts) == 1
    assert alerts[0].previous_cents == 170


def test_even_history_median_is_compared_exactly_and_rounded_half_up():
    history = [obs(price, f"2026-09-0{day}T00:00:00Z")
               for day, price in enumerate((100, 100, 101, 101), 1)]
    current = obs(100, "2026-09-05T00:00:00Z")
    alerts = evaluate(history, [current], [{"type": "below_median", "pct": 0}])
    assert len(alerts) == 1
    assert alerts[0].previous_cents == 101


def test_even_median_does_not_lose_precision_for_large_prices():
    base = 10**17
    history = [obs(base, "2026-09-01T00:00:00Z"), obs(base, "2026-09-02T00:00:00Z"),
               obs(base + 1, "2026-09-03T00:00:00Z"), obs(base + 1, "2026-09-04T00:00:00Z")]
    current = obs(base, "2026-09-05T00:00:00Z")
    alerts = evaluate(history, [current], [{"type": "below_median", "pct": 0}])
    assert len(alerts) == 1
    assert alerts[0].previous_cents == base + 1


def test_daily_close_basis_weights_days_equally():
    history = [obs(100, f"2026-09-01T{hour:02}:00:00Z") for hour in range(12)]
    history.extend([obs(200, "2026-09-02T12:00:00Z"), obs(200, "2026-09-03T12:00:00Z")])
    current = obs(120, "2026-09-04T12:00:00Z")
    assert evaluate(history, [current], [{"type": "below_median", "pct": 15}]) == []
    daily = evaluate(history, [current], [{"type": "below_median", "pct": 15, "basis": "daily_close"}])
    assert len(daily) == 1


def test_below_median_excludes_observation_on_window_start():
    history = [obs(100, "2026-08-05T12:00:00Z"),
               obs(100, "2026-08-20T12:00:00Z"),
               obs(100, "2026-09-01T12:00:00Z")]
    current = obs(50, "2026-09-04T12:00:00Z")
    # The first prior item is exactly 30 days before the current timestamp and is excluded.
    assert evaluate(history, [current], [{"type": "below_median", "pct": 15, "window_days": 30}]) == []


def test_drop_pct_ignores_null_last_observation():
    history = [obs(10000, "2026-09-01T00:00:00Z"), obs(None, "2026-09-02T00:00:00Z")]
    current = obs(8000, "2026-09-03T00:00:00Z")
    alerts = evaluate(history, [current], [{"type": "drop_pct", "pct": 10}])
    assert len(alerts) == 1


def test_below_median_requires_three_in_window_prices():
    history = [obs(100, "2026-09-01T00:00:00Z"), obs(100, "2026-09-02T00:00:00Z")]
    assert evaluate(history, [obs(50, "2026-09-03T00:00:00Z")],
                    [{"type": "below_median", "pct": 15}]) == []
