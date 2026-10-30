"""SQLite history plus JSONL import/export (JSONL is the interchange format)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import Observation

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store TEXT NOT NULL, product_id TEXT NOT NULL, observed_at TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs ON observations(store, product_id, observed_at);
"""


class History:
    def __init__(self, path: str | Path = "pricewatch.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def add(self, obs: Iterable[Observation]) -> None:
        self.conn.executemany(
            "INSERT INTO observations(store, product_id, observed_at, data) "
            "SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM observations WHERE store=? AND product_id=? AND observed_at=?)",
            [(o.store, o.product_id, o.observed_at, json.dumps(o.to_dict()), o.store, o.product_id, o.observed_at) for o in obs],
        )
        self.conn.commit()

    def all(self) -> list[Observation]:
        rows = self.conn.execute("SELECT data FROM observations ORDER BY observed_at").fetchall()
        return [Observation.from_dict(json.loads(r[0])) for r in rows]

    def latest(self) -> list[Observation]:
        rows = self.conn.execute(
            "SELECT data FROM observations o WHERE observed_at = (SELECT MAX(observed_at) FROM observations "
            "WHERE store=o.store AND product_id=o.product_id) ORDER BY store, product_id").fetchall()
        return [Observation.from_dict(json.loads(r[0])) for r in rows]


def read_jsonl(path: str | Path) -> list[Observation]:
    p = Path(path)
    if not p.exists():
        return []
    return [Observation.from_dict(json.loads(line)) for line in p.read_text().splitlines() if line.strip()]


def write_jsonl(path: str | Path, obs: Iterable[Observation], append: bool = False) -> None:
    with open(path, "a" if append else "w") as f:
        for o in obs:
            f.write(json.dumps(o.to_dict()) + "\n")
