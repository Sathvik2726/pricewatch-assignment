import json
import yaml

from pricewatch import providers
from pricewatch.cli import main
from pricewatch.models import Observation


def test_llm_extract_accepts_an_unconfigured_store(tmp_path, capsys):
    html = tmp_path / "product.html"
    html.write_text("<h1>Unknown product</h1>")

    result = main([
        "extract", "--url", "https://new-store.example/product/sku-1",
        "--html", str(html), "--llm", "--provider", "echo",
    ])

    assert result == 0
    observation = json.loads(capsys.readouterr().out)
    assert observation["store"] == "new-store.example"
    assert observation["product_id"] == "sku-1"
    assert observation["source"] == "llm"
    assert observation["notes"] == []


def test_llm_extract_prints_observation_for_unexpected_provider_error(tmp_path, capsys, monkeypatch):
    html = tmp_path / "product.html"
    html.write_text("<h1>Unknown product</h1>")
    timeouts = []

    class BrokenProvider:
        name = "broken"

        def complete(self, system, user, *, metadata, timeout=30.0):
            timeouts.append(timeout)
            raise TypeError("SDK signature changed")

    monkeypatch.setattr(providers, "load_provider", lambda name=None: BrokenProvider())
    result = main([
        "extract", "--url", "https://new-store.example/product/sku-1",
        "--html", str(html), "--llm", "--provider", "anthropic",
    ])

    assert result == 0
    observation = json.loads(capsys.readouterr().out)
    assert observation["store"] == "new-store.example"
    assert observation["source"] == "llm"
    assert observation["notes"] == ["LLM extraction error: TypeError"]
    assert observation["price_cents"] is None
    assert timeouts == [20.0]


def test_scan_flushes_observations_as_they_arrive(tmp_path, monkeypatch):
    from pricewatch import orchestrator

    stores_file = tmp_path / "stores.yaml"
    stores_file.write_text(yaml.safe_dump({"base_url": "http://unused", "stores": {}}))
    output = tmp_path / "partial.jsonl"
    observation = Observation("corner", "p1", "https://x/p1", "Widget", 100, "USD")

    def fake_scan(cfg, stores, workers, deadline_seconds, on_observation):
        on_observation(observation)
        return [observation]

    monkeypatch.setattr(orchestrator, "scan", fake_scan)
    assert main(["scan", "--stores-file", str(stores_file), "--out", str(output), "--deadline", "2"]) == 0
    assert json.loads(output.read_text().splitlines()[0])["product_id"] == "p1"
