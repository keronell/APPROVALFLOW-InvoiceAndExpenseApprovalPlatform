"""The AI decisioning agent (Microsoft Agent Framework).

Fail-fast on an unexpected MAF surface (M15): if the installed agent-framework
does not expose the symbols we build against, importing this package raises
immediately rather than failing mysteriously at request time.
"""

from __future__ import annotations

import agent_framework as _af

_REQUIRED = ("Agent", "BaseChatClient", "ChatOptions", "ChatResponse", "Message", "Content")
_missing = [name for name in _REQUIRED if not hasattr(_af, name)]
if _missing:  # pragma: no cover - guards against a breaking MAF upgrade
    raise ImportError(
        "Microsoft Agent Framework is missing expected symbols "
        f"{_missing}; the installed agent-framework "
        f"({getattr(_af, '__version__', '?')}) is incompatible with this code."
    )

from .assessor import DecisionAgent  # noqa: E402
from .client import build_chat_client  # noqa: E402
from .settings import LLMSettings  # noqa: E402

__all__ = ["DecisionAgent", "LLMSettings", "build_chat_client"]
