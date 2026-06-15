"""LLM provider abstraction.

The harness is provider-agnostic: it speaks the canonical types in ``base`` and
lets concrete providers (``openai_provider``, ``bedrock_provider``) translate to/from
their wire formats. Add a provider by implementing ``LLMProvider`` and registering it
in ``registry.build_provider``. See ``docs/ADDING_A_PROVIDER.md``.
"""
from app.providers.base import (  # noqa: F401
    LLMProvider,
    StreamDelta,
    ToolCall,
    ToolSpec,
)
