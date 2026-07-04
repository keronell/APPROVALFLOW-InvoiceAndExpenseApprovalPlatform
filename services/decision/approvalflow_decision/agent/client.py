"""Chat-client factory — the single provider-specific line (M15).

Swapping providers is one config value (``LLM_PROVIDER``). Everything else in
the agent is provider-agnostic. Unknown providers fail fast.
"""

from __future__ import annotations

from agent_framework import BaseChatClient
from approvalflow_shared.logging import get_logger

from .settings import LLMSettings
from .stub_client import StubChatClient

log = get_logger()

# OpenAI-compatible providers → the same client, different base_url/model.
_OPENAI_COMPATIBLE = {"ollama", "groq", "gemini", "openai"}


def build_chat_client(settings: LLMSettings | None = None) -> BaseChatClient:
    settings = settings or LLMSettings.from_env()
    provider = settings.provider

    if provider == "stub":
        log.info("llm_provider_selected", provider="stub")
        return StubChatClient()

    if provider in _OPENAI_COMPATIBLE:
        # Imported lazily so the stub path needs no OpenAI client at all.
        from agent_framework.openai import OpenAIChatClient

        log.info("llm_provider_selected", provider=provider, model=settings.model)
        return OpenAIChatClient(
            settings.model,
            base_url=settings.base_url,
            api_key=settings.api_key,
        )

    allowed = ", ".join(sorted(_OPENAI_COMPATIBLE))
    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use one of: stub, {allowed}.")
