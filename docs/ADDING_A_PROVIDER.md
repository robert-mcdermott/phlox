# Adding a Provider

A provider adapts a model backend to Phlox's canonical streaming interface. The two
built-ins cover most needs:

- **`OpenAIProvider`** — any OpenAI-compatible endpoint (OpenAI, Ollama, vLLM, LiteLLM,
  LM Studio, …) by varying `endpoint`/`base_url`. Most "new providers" are just a new
  **profile**, not new code — and for the two built-in types you don't even need to touch
  `config.yml`: an admin can add/edit profiles **live** in **Settings → (Admin)
  Configuration** (see [AUTH.md](AUTH.md) §admin config). This guide covers writing a new
  provider *type* (new code), which still starts from a config profile.
- **`BedrockProvider`** — AWS Bedrock Converse API.

Only write a new class for a backend that is neither OpenAI-compatible nor Bedrock
(e.g. a native Anthropic, Cohere, or Google SDK).

## 1. Implement `LLMProvider`

`backend/app/providers/base.py` defines the contract. Implement `stream`:

```python
# backend/app/providers/my_provider.py
from collections.abc import Iterator
from typing import Any
from app.providers.base import LLMProvider, StreamDelta, ToolCall, ToolSpec


class MyProvider(LLMProvider):
    def __init__(self, config: dict[str, Any]):
        self.model = config["model"]
        self.supports_tools = config.get("supports_tools", True)
        # build your SDK client from config (endpoint, api_key, region, …)

    def stream(self, messages, tools: list[ToolSpec], params) -> Iterator[StreamDelta]:
        # 1. translate `messages` (canonical format, see base.py) to your wire format
        # 2. translate `tools` to your tool schema (if supports_tools)
        # 3. call your streaming API; for each chunk yield:
        #      StreamDelta(type="text", text=...)          # answer tokens
        #      StreamDelta(type="reasoning", text=...)      # optional thinking
        # 4. accumulate streamed tool-call fragments, then at the end yield EXACTLY one:
        #      StreamDelta(type="tool_calls", tool_calls=[ToolCall(id, name, arguments)])
        #    OR (no tools requested):
        #      StreamDelta(type="done", stop_reason="stop")
        ...

    # optional — only if your backend can embed (used for RAG)
    def embed(self, texts, model=None):
        ...
```

**The contract that matters:** `stream` yields zero or more `text`/`reasoning` deltas,
then **exactly one terminal delta** — `tool_calls` if the model wants tools this round,
otherwise `done`. Tool-call arguments must be parsed into a `dict`. The harness handles
the loop, permissions, execution, and persistence; your job is only translation.

Study `openai_provider.py` (tool-call delta accumulation by index) and
`bedrock_provider.py` (`toolUse` block accumulation) — they are the reference
implementations.

## 2. Wire it into the registry

In `backend/app/providers/registry.py`, extend `build_provider`:

```python
ptype = cfg.get("type", "openai")
if ptype == "bedrock":
    return BedrockProvider(cfg)
if ptype == "openai":
    return OpenAIProvider(cfg)
if ptype == "myprovider":          # add this
    from app.providers.my_provider import MyProvider
    return MyProvider(cfg)
```

## 3. Add a profile in `config.yml`

```yaml
profiles:
  my-thing:
    type: myprovider
    label: "My Provider"
    model: some-model
    models: [some-model, another-model]
    supports_tools: true
    # ...any keys your __init__ reads
```

Because this is a **new provider _type_** (custom code in `build_provider`), it ships with
the backend — restart after deploying the code. The profile then shows up in **Settings →
Model**, "Test connection" works, and conversations can use it. No frontend changes required.

> For the built-in `openai`/`bedrock` types, no restart or `config.yml` edit is needed at
> all — add the profile from **Settings → (Admin) Configuration** and it applies live. The
> file-edit-and-restart flow above is only for new provider *types* you implement here.
