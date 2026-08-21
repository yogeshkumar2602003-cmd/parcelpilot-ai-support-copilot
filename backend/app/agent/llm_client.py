"""LLM client abstraction.

`AnthropicLLMClient` wraps the real Anthropic SDK tool-use loop. Tests and
any no-API-key code path use `FakeLLMClient` / a caller-supplied fake so
the test suite never makes a network call. The model name is read from
ANTHROPIC_MODEL so it isn't coupled to any specific model id that may be
retired later.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app import config

logger = logging.getLogger("parcelpilot.agent.llm_client")


class NoAPIKeyError(Exception):
    """Raised when a chat request needs the real LLM but no API key is configured.

    The message intentionally names only the environment variable, never the
    key's value -- this exception's `str()` is surfaced to API responses.
    """


@dataclass
class LLMContentBlock:
    type: str  # "text" | "tool_use"
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    content: list[LLMContentBlock]
    stop_reason: str | None = None


class LLMClient(Protocol):
    def create_message(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
    ) -> LLMResponse: ...


class AnthropicLLMClient:
    """Server-side-only Anthropic client. The API key never leaves this
    process: it is read from `config.settings` (never a request/response
    field, never a query param) and handed directly to the SDK.

    `api_key`/`model` read `config.settings` at CONSTRUCTION time (not at
    import time) via the `config` module reference, so this always sees the
    current settings -- including in tests that call
    `app.config.reload_settings()` after monkeypatching the environment.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key if api_key is not None else config.settings.anthropic_api_key
        self.model = model or config.settings.anthropic_model
        self._client = None
        logger.debug("AnthropicLLMClient constructed: ai_configured=%s model=%s", bool(self.api_key), self.model)

    def _get_client(self):
        if not self.api_key:
            raise NoAPIKeyError(
                "ANTHROPIC_API_KEY is not configured. Set it in the environment (or .env at the project "
                "root) to enable the chat agent."
            )
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def create_message(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
    ) -> LLMResponse:
        client = self._get_client()
        response = client.messages.create(
            model=self.model, max_tokens=2048, system=system, messages=messages, tools=tools,
        )
        blocks = []
        for block in response.content:
            if block.type == "text":
                blocks.append(LLMContentBlock(type="text", text=block.text))
            elif block.type == "tool_use":
                blocks.append(LLMContentBlock(type="tool_use", id=block.id, name=block.name, input=block.input))
        return LLMResponse(content=blocks, stop_reason=response.stop_reason)


def anthropic_message_content_to_dicts(blocks: list[LLMContentBlock]) -> list[dict[str, Any]]:
    """Convert our LLMContentBlock list back into the raw dict shape the
    Anthropic messages API expects when replaying assistant turns."""
    out = []
    for b in blocks:
        if b.type == "text":
            out.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return out
