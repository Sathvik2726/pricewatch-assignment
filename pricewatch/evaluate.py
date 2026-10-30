"""Eval harness for the LLM extractor.

Runs the LLM extractor over every snapshot in a directory, compares with labels.json, and
writes report.json and label_issues.json. The report schema is documented in tasks/ISSUE-3.md.
"""
from __future__ import annotations

import json
import time
import math
from collections import Counter, defaultdict
from statistics import median
from pathlib import Path

from .agents.llm_extractor import extract_with_llm
from .providers import Provider


def run(provider: Provider, snapshots_dir: str | Path, labels_path: str | Path, out_dir: str | Path) -> dict:
    snapshots_dir, out_dir = Path(snapshots_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = {row["id"]: row for row in json.loads(Path(labels_path).read_text())}

    results = []
    metrics = ("price_exact", "currency", "availability", "pack_size")
    by_store: dict[str, list[dict]] = defaultdict(list)
    errors: Counter[str] = Counter()
    latencies: list[float] = []
    token_input = token_output = 0
    baseline_correct = baseline_n = 0
    t0 = time.time()
    for snap_id, row in labels.items():
        exp = row["expected"]
        record = {"id": snap_id}
        try:
            html = (snapshots_dir / row["file"]).read_text(encoding="utf-8")
            start = time.perf_counter()
            obs = extract_with_llm(provider, row["store"], row["url"], html, cache_dir=str(out_dir / ".cache"))
            latencies.append((time.perf_counter() - start) * 1000)
            if any("malformed" in note for note in obs.notes):
                errors["malformed_output"] += 1
            record = {"id": snap_id, **{m: (
                obs.price_cents == exp.get("price_cents") if m == "price_exact" else
                obs.currency == exp.get("currency") if m == "currency" else
                obs.availability == exp.get("availability") if m == "availability" else
                obs.pack_size == exp.get("pack_size")
            ) for m in metrics}}
            # Same-page rule baseline: use the non-LLM semantic extractor; unknown stores yield zero.
            from .agents.extractor import extract
            try:
                base = extract(None, row["store"], row["store"], row["url"], html)
            except Exception:
                base = None
            baseline_n += 1
            baseline_correct += int(base is not None and base.price_cents == exp.get("price_cents"))
        except Exception as exc:
            message = str(exc).lower()
            category = "timeout" if "timeout" in message else "provider_error"
            errors[category] += 1
            record = {"id": snap_id, **{m: False for m in metrics}}
        by_store[row.get("store", "unknown")].append(record)
        results.append(record)

    def score(items: list[dict]) -> dict:
        return {m: round(sum(bool(r.get(m)) for r in items) / max(1, len(items)), 4) for m in metrics}

    sorted_latency = sorted(latencies)
    def percentile(p: float) -> int:
        if not sorted_latency: return 0
        return int(round(sorted_latency[min(len(sorted_latency)-1, math.ceil(p * len(sorted_latency))-1)]))

    report = {
        "provider": provider.name,
        "model": __import__("os").environ.get("PRICEWATCH_MODEL", getattr(provider, "model", "unknown")),
        "n": len(results),
        "overall": score(results),
        "per_store": {name: {"n": len(items), **score(items)} for name, items in sorted(by_store.items())},
        "errors": {"timeout": errors["timeout"], "malformed_output": errors["malformed_output"], "provider_error": errors["provider_error"]},
        "cost": {"input_tokens": token_input, "output_tokens": token_output, "usd_estimate": None},
        "latency_ms": {"p50": percentile(.5), "p95": percentile(.95)},
        "baseline": {"price_exact": round(baseline_correct / max(1, baseline_n), 4)},
        "elapsed_s": round(time.time() - t0, 2),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "label_issues.json").write_text("[]")
    return report
