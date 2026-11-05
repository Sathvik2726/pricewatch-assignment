from __future__ import annotations

import os
import threading

from . import ProviderError, ProviderTimeout


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise ProviderError("pip install openai") from e
        if not os.environ.get("OPENAI_API_KEY"):
            raise ProviderError("OPENAI_API_KEY not set")
        self.client = OpenAI()
        self.model = model or os.environ.get("PRICEWATCH_MODEL", "gpt-4o-mini")
        self._usage_local = threading.local()

    @property
    def last_usage(self) -> dict[str, int] | None:
        return getattr(getattr(self, "_usage_local", None), "value", None)

    @last_usage.setter
    def last_usage(self, value: dict[str, int] | None) -> None:
        if not hasattr(self, "_usage_local"):
            self._usage_local = threading.local()
        self._usage_local.value = value

    def complete(self, system: str, user: str, *, metadata: dict, timeout: float = 30.0) -> str:
        assert "source_url" in metadata
        import openai
        try:
            r = self.client.chat.completions.create(
                model=self.model, timeout=timeout, temperature=0,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except openai.APITimeoutError as e:
            raise ProviderTimeout(str(e)) from e
        except openai.OpenAIError as e:
            raise ProviderError(str(e)) from e
        usage = getattr(r, "usage", None)
        self.last_usage = {
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        }
        return r.choices[0].message.content or ""
