"""Reproduces one of the symptoms reported in tasks/ISSUE-1.md."""
from pricewatch.agents.extractor import extract_maple
from pricewatch.http import Client

HTML = """<html><body><div class="product"><h1 class="product__title">Kestrel Lumen Notebook</h1>
<div class="price"><span class="price price--compare"><s>937,20 €</s></span> <span class="price price--sale">720,92 €</span></div>
<p class="muted">In stock · prices shown in EUR</p></div></body></html>"""


def test_maple_sale_item_reports_sale_price():
    o = extract_maple(Client(), "maple", "http://x/stores/maple/products/kestrel-lumen-notebook", HTML)
    assert o.price_cents == 72092
    assert o.currency == "EUR"


def test_maple_selector_priority_beats_earlier_meta_and_comparison_price():
    html = '''<meta property="product:price:amount" content="937.20">
    <div class="price"><span class="price--compare"><s>937,20 €</s></span>
    <span class="price--sale">720,92 €</span><span>20,03 € / unit</span></div>'''
    o = extract_maple(Client(), "maple", "http://x/stores/maple/products/item", html)
    assert o.price_cents == 72092
