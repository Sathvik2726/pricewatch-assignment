import json

from pricewatch.evaluate import run
from pricewatch.providers import ProviderTimeout


class MixedProvider:
    name = "test"

    def complete(self, system, user, *, metadata, timeout=30.0):
        assert metadata["source_url"]
        if metadata["source_url"].endswith("timeout"):
            raise ProviderTimeout("request timeout")
        if metadata["source_url"].endswith("malformed"):
            return "not json"
        return '{"name":"Widget","price_cents":1234,"currency":"USD","availability":"in_stock","pack_size":1}'


def test_eval_keeps_going_and_caches(tmp_path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    for name in ("ok", "timeout", "malformed"):
        (snapshots / f"{name}.html").write_text("<h1>Widget</h1>")
    labels = [
        {"id": name, "file": f"{name}.html", "store": "unknown", "url": f"https://x/{name}",
         "expected": {"name": "Widget", "price_cents": 1234, "currency": "USD", "availability": "in_stock", "pack_size": 1}}
        for name in ("ok", "timeout", "malformed")
    ]
    label_file = tmp_path / "labels.json"
    label_file.write_text(json.dumps(labels))
    out = tmp_path / "out"
    first = run(MixedProvider(), snapshots, label_file, out)
    second = run(MixedProvider(), snapshots, label_file, out)
    assert first["n"] == second["n"] == 3
    assert first["errors"]["timeout"] == 1
    assert first["overall"]["price_exact"] == 0.3333
    assert len(list((out / ".cache").glob("*.json"))) == 2
    assert (out / "report.json").exists() and (out / "label_issues.json").exists()
