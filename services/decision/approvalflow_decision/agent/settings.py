"""LLM provider settings — the one place the provider is chosen (M15)."""

from __future__ import annotations

import os

from pydantic import BaseModel


class LLMSettings(BaseModel):
    """Read from the environment. The provider is a single config value; the
    rest of the agent is provider-agnostic."""

    provider: str = "ollama"
    model: str = "qwen2.5:3b"
    base_url: str | None = None
    api_key: str = "not-needed"

    @classmethod
    def from_env(cls) -> LLMSettings:
        return cls(
            provider=os.environ.get("LLM_PROVIDER", "ollama").lower(),
            model=os.environ.get("LLM_MODEL", "qwen2.5:3b"),
            base_url=os.environ.get("LLM_BASE_URL"),
            api_key=os.environ.get("LLM_API_KEY", "not-needed"),
        )
