from pricewatch.agents.llm_extractor import clean_html, extract_with_llm


class StaticProvider:
    name = "static"

    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, system, user, *, metadata, timeout=30.0):
        self.calls += 1
        assert metadata["source_url"]
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response


def test_clean_html_keeps_price_metadata_and_marks_struck_prices():
    cleaned = clean_html('''<meta property="product:price:amount" content="119.00">
    <nav>menu</nav><h1>Widget</h1><span class="old-price">$149.00</span>
    <span>$119.00</span>''')
    assert "product:price:amount: 119.00" in cleaned
    assert "STRUCK-THROUGH: $149.00" in cleaned
    assert "menu" not in cleaned
    assert "Widget" in cleaned


def test_llm_price_text_converts_inr_grouping_and_accepts_missing_optional_fields():
    html = "<html lang='en-IN'><h1>Phone</h1><div>₹1,08,947</div><div>In stock</div></html>"
    obs = extract_with_llm(StaticProvider({
        "name": "Phone", "price_text": "₹1,08,947", "currency": "INR", "availability": "InStock"
    }), "store", "https://shop.in/p/phone", html)
    assert obs.price_cents == 10894700
    assert obs.currency == "INR"
    assert obs.availability == "in_stock"
    assert obs.pack_size == 1
    assert obs.notes == []


def test_llm_price_text_handles_zero_and_three_decimal_currencies():
    jpy = extract_with_llm(StaticProvider({
        "price_text": "¥1,200", "currency": "JPY", "availability": "InStock"
    }), "store", "https://shop.jp/p/1", "<h1>Item</h1><b>¥1,200</b>")
    kwd = extract_with_llm(StaticProvider({
        "price_text": "KWD 1.234", "currency": "KWD", "availability": "available"
    }), "store", "https://shop.kw/p/1", "<h1>Item</h1><b>KWD 1.234</b>")
    assert jpy.price_cents == 1200
    assert kwd.price_cents == 1234


def test_llm_uses_jsonld_offer_when_model_has_no_price():
    html = '''<h1>Pack of tea</h1><script type="application/ld+json">
    {"@type":"Product","name":"Tea Pack","multipack":3,"offers":{"price":"19.99",
    "priceCurrency":"USD","availability":"https://schema.org/InStock"}}</script>'''
    obs = extract_with_llm(StaticProvider({
        "name": "Tea Pack", "price_text": None, "currency": None, "availability": "unknown"
    }), "store", "https://shop.example/p/tea", html)
    assert obs.price_cents == 1999
    assert obs.currency == "USD"
    assert obs.availability == "in_stock"
    assert obs.pack_size == 3
    assert "JSON-LD" in obs.notes[0]


def test_llm_discards_price_text_not_present_in_page():
    obs = extract_with_llm(StaticProvider({
        "price_text": "$999.00", "currency": "USD", "availability": "in_stock"
    }), "store", "https://shop.example/p/1", "<h1>Widget</h1><b>$199.00</b>")
    assert obs.price_cents is None
    assert "price text not found" in obs.notes[0]


def test_llm_retries_once_for_non_json_and_caches_the_repaired_response(tmp_path):
    provider = StaticProvider(["not JSON", {
        "price_text": "$19.99", "currency": "USD", "availability": "available"
    }])
    cache = str(tmp_path / "cache")
    html = "<h1>Widget</h1><span>$19.99</span>"
    first = extract_with_llm(provider, "store", "https://shop.example/p/1", html, cache_dir=cache)
    assert first.price_cents == 1999
    assert provider.calls == 2
    second = extract_with_llm(provider, "store", "https://shop.example/p/1", html, cache_dir=cache)
    assert second.price_cents == 1999
    assert provider.calls == 2
