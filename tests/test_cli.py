import json

from pricewatch import providers
from pricewatch.cli import main


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

    class BrokenProvider:
        name = "broken"

        def complete(self, system, user, *, metadata, timeout=30.0):
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
