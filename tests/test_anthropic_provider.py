from types import SimpleNamespace

import pytest

from pricewatch.providers import ProviderError
from pricewatch.providers.anthropic_provider import AnthropicProvider


def test_anthropic_request_uses_supported_arguments_and_records_usage():
    calls = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text='{"price_cents": 100}')],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=12, output_tokens=4),
            )

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.client = SimpleNamespace(messages=Messages())
    provider.model = "claude-haiku-test"
    provider.last_usage = None

    assert provider.complete("sys", "page", metadata={"source_url": "https://shop.example/p/1"}) == '{"price_cents": 100}'
    assert "temperature" not in calls[0]
    assert calls[0]["timeout"] == 20.0
    assert provider.last_usage == {"input_tokens": 12, "output_tokens": 4}


def test_anthropic_provider_wraps_unexpected_sdk_errors():
    class Messages:
        def create(self, **kwargs):
            raise TypeError("unsupported SDK argument")

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.client = SimpleNamespace(messages=Messages())
    provider.model = "claude-haiku-test"
    provider.last_usage = None

    with pytest.raises(ProviderError, match="TypeError"):
        provider.complete("sys", "page", metadata={"source_url": "https://shop.example/p/1"})
