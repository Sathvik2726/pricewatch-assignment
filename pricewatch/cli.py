"""pricewatch command line. Other tooling scripts against these commands; keep existing flags stable.

  pricewatch scan    --stores-url URL [--store NAME ...] [--out FILE.jsonl] [--db FILE]
  pricewatch extract --url URL [--html FILE] [--store NAME] [--llm] [--provider NAME]
  pricewatch watch   --history FILE.jsonl --new FILE.jsonl [--rules alerts.yaml] [--out alerts.json]
  pricewatch eval    --snapshots DIR --labels FILE [--provider NAME] [--out DIR]
  pricewatch serve   [--port 8000] [--db FILE]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

from . import config, storage
from .agents import extractor
from .http import Client
from .models import Observation


def cmd_scan(a: argparse.Namespace) -> int:
    from . import orchestrator
    cfg = config.load_stores(a.stores_file, a.stores_url)
    obs = orchestrator.scan(cfg, a.store or None, workers=a.workers)
    if a.db:
        storage.History(a.db).add(obs)
    if a.out:
        storage.write_jsonl(a.out, obs)
    else:
        for o in obs:
            print(json.dumps(o.to_dict()))
    logging.info("scanned %d products", len(obs))
    return 0


def cmd_extract(a: argparse.Namespace) -> int:
    cfg = config.load_stores(a.stores_file)
    store = a.store or next((s for s in cfg["stores"] if f"/stores/{s}/" in a.url), None)
    # The LLM path is specifically the fallback for stores without a configured
    # adapter, so it must remain usable for an unfamiliar URL without --store.
    if store is None and a.llm:
        store = urlparse(a.url).hostname or "unknown"
    if store is None:
        print("cannot infer --store from url", file=sys.stderr)
        return 2
    client = Client()
    html = Path(a.html).read_text() if a.html else client.get(a.url).text
    if a.llm:
        from .agents.llm_extractor import extract_with_llm
        from .providers import load_provider
        obs = extract_with_llm(load_provider(a.provider), store, a.url, html)
    else:
        obs = extractor.extract(client, store, cfg["stores"].get(store, {}).get("adapter", store), a.url, html)
    print(json.dumps(obs.to_dict(), indent=2))
    return 0


def cmd_watch(a: argparse.Namespace) -> int:
    from .agents import watcher
    history = storage.read_jsonl(a.history)
    new = storage.read_jsonl(a.new)
    rules = config.load_rules(a.rules)
    alerts = [x.to_dict() for x in watcher.evaluate(history, new, rules)]
    text = json.dumps(alerts, indent=2)
    if a.out:
        Path(a.out).write_text(text)
    else:
        print(text)
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    from . import evaluate
    from .providers import load_provider
    report = evaluate.run(load_provider(a.provider), a.snapshots, a.labels, a.out)
    print(json.dumps(report, indent=2))
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn
    from .server import make_app
    uvicorn.run(make_app(a.db), host="0.0.0.0", port=a.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    p = argparse.ArgumentParser(prog="pricewatch")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan"); s.set_defaults(fn=cmd_scan)
    s.add_argument("--stores-url", default=None); s.add_argument("--stores-file", default="stores.yaml")
    s.add_argument("--store", action="append"); s.add_argument("--out"); s.add_argument("--db"); s.add_argument("--workers", type=int, default=4)

    s = sub.add_parser("extract"); s.set_defaults(fn=cmd_extract)
    s.add_argument("--url", required=True); s.add_argument("--html"); s.add_argument("--store"); s.add_argument("--stores-file", default="stores.yaml")
    s.add_argument("--llm", action="store_true"); s.add_argument("--provider")

    s = sub.add_parser("watch"); s.set_defaults(fn=cmd_watch)
    s.add_argument("--history", required=True); s.add_argument("--new", required=True)
    s.add_argument("--rules", default="alerts.yaml"); s.add_argument("--out")

    s = sub.add_parser("eval"); s.set_defaults(fn=cmd_eval)
    s.add_argument("--snapshots", default="eval/snapshots"); s.add_argument("--labels", default="eval/labels.json")
    s.add_argument("--provider"); s.add_argument("--out", default="eval/out")

    s = sub.add_parser("serve"); s.set_defaults(fn=cmd_serve)
    s.add_argument("--port", type=int, default=8000); s.add_argument("--db", default="pricewatch.db")

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
