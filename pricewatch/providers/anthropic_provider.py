from __future__ import annotations

import os

from . import ProviderError, ProviderTimeout


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise ProviderError("pip install anthropic") from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError("ANTHROPIC_API_KEY not set")
        # Keep retry time inside the grader's per-page budget. The caller can
        # still set a tighter timeout for evaluation runs.
        self.client = anthropic.Anthropic(max_retries=1, timeout=20.0)
        self.model = model or os.environ.get("PRICEWATCH_MODEL", "claude-haiku-4-5")
        self.last_usage: dict[str, int] | None = None

    def complete(self, system: str, user: str, *, metadata: dict, timeout: float = 20.0) -> str:
        assert "source_url" in metadata
        import anthropic
        try:
            r = self.client.messages.create(
                model=self.model, max_tokens=512, system=system, timeout=timeout,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APITimeoutError as e:
            raise ProviderTimeout(str(e)) from e
        except anthropic.AnthropicError as e:
            raise ProviderError(str(e)) from e
        except Exception as e:
            raise ProviderError(f"unexpected Anthropic provider error: {type(e).__name__}") from e
        usage = getattr(r, "usage", None)
        self.last_usage = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
        if getattr(r, "stop_reason", None) == "refusal":
            raise ProviderError("Anthropic refused the extraction request")
        text = "".join(getattr(b, "text", "") for b in (getattr(r, "content", None) or []))
        if not text.strip():
            raise ProviderError("Anthropic returned no text content")
        return text
