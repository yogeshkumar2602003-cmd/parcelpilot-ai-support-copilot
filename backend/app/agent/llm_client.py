"""LLM client abstraction.

`AnthropicLLMClient` wraps the real Anthropic SDK tool-use loop.
`OllamaLLMClient` talks to a local Ollama server instead, for zero-cost
operation when no Anthropic credit/API key is available -- see
`app.config.Settings.ai_provider`. Both implement the same `LLMClient`
Protocol (`create_message(system, messages, tools) -> LLMResponse`) using
the same Anthropic-shaped message/tool representation, so
`app.agent.orchestrator.run_agent_turn` and `app.agent.tools.TOOL_SCHEMAS`
never need to know which provider is active. Tests and any no-API-key code
path use `FakeLLMClient` / a caller-supplied fake so the test suite never
makes a network call. The model name is read from ANTHROPIC_MODEL /
AI_MODEL so it isn't coupled to any specific model id that may be retired
later.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app import config

logger = logging.getLogger("parcelpilot.agent.llm_client")


class NoAPIKeyError(Exception):
    """Raised when a chat request needs the real LLM but no API key is configured.

    The message intentionally names only the environment variable, never the
    key's value -- this exception's `str()` is surfaced to API responses.
    """


class OllamaUnavailableError(Exception):
    """Raised when the local Ollama server cannot be reached at all (not
    running, wrong port, firewalled). Message never includes secrets."""


class OllamaModelNotFoundError(Exception):
    """Raised when Ollama is reachable but the configured model isn't
    installed locally (`ollama pull <model>` was never run for it)."""


class OllamaTimeoutError(Exception):
    """Raised when the local model didn't respond within the configured
    timeout (e.g. a machine too slow/memory-constrained for the model)."""


class OllamaResponseError(Exception):
    """Raised for any other malformed/unexpected response from Ollama."""


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


class OllamaLLMClient:
    """Server-side-only client for a local Ollama server. Speaks Ollama's
    native `/api/chat` endpoint (not the Anthropic wire protocol -- Ollama
    does not implement that -- and not even the OpenAI-compatible shim,
    since the native endpoint needs no extra dependency beyond `httpx`,
    already used elsewhere in this project).

    Implements the same `LLMClient` Protocol as `AnthropicLLMClient` by
    translating the Anthropic-shaped `messages`/`tools`/response
    representation this codebase already uses (see
    `app.agent.tools.TOOL_SCHEMAS`, `app.agent.orchestrator`) to and from
    Ollama's OpenAI-style chat/tool-call shape. This keeps the orchestrator,
    tool schemas, and system prompt completely provider-agnostic.

    `base_url`/`model` read `config.settings` at CONSTRUCTION time (not
    import time), matching `AnthropicLLMClient`'s pattern so both remain
    correct across `app.config.reload_settings()` calls in tests.
    """

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout_s: float | None = None):
        self.base_url = (base_url if base_url is not None else config.settings.ollama_base_url).rstrip("/")
        self.model = model or config.settings.ollama_model
        self.timeout_s = timeout_s if timeout_s is not None else config.settings.ollama_timeout_s

    @staticmethod
    def _tool_schemas_to_ollama(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ]

    @staticmethod
    def _messages_to_ollama(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            role = m["role"]
            content = m["content"]
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue

            if role == "assistant":
                text_parts = [b["text"] for b in content if b.get("type") == "text" and b.get("text")]
                tool_calls = [
                    {
                        "id": b.get("id") or f"call_{uuid.uuid4().hex[:10]}",
                        "type": "function",
                        "function": {"name": b["name"], "arguments": b.get("input") or {}},
                    }
                    for b in content
                    if b.get("type") == "tool_use"
                ]
                assistant_msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts)}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                out.append(assistant_msg)
            else:
                # `role == "user"`: either a plain user turn or a batch of
                # tool_result blocks fed back after `role == "assistant"`
                # tool_use (see `orchestrator._tool_result_message`).
                for b in content:
                    if b.get("type") == "tool_result":
                        out.append({"role": "tool", "content": b.get("content", "")})
                    else:
                        out.append({"role": "user", "content": b.get("text", "")})
        return out

    def create_message(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": self._messages_to_ollama(system, messages),
            "tools": self._tool_schemas_to_ollama(tools),
            "stream": False,
            "options": {"num_predict": config.settings.ollama_num_predict},
            # Keeps the model resident in RAM between requests (default
            # Ollama behavior unloads after 5 minutes idle) -- on a
            # memory-constrained machine, reloading costs ~15s+ per turn on
            # top of generation time. See config.settings.ollama_keep_alive.
            "keep_alive": config.settings.ollama_keep_alive,
        }
        try:
            resp = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_s)
        except httpx.TimeoutException as e:
            raise OllamaTimeoutError(
                f"The local Ollama model ({self.model}) did not respond within {self.timeout_s:.0f}s."
            ) from e
        except httpx.ConnectError as e:
            raise OllamaUnavailableError(
                f"Could not connect to the local Ollama server at {self.base_url}. Is `ollama serve` running?"
            ) from e
        except httpx.HTTPError as e:
            raise OllamaUnavailableError(f"Error communicating with the local Ollama server at {self.base_url}.") from e

        if resp.status_code == 404:
            raise OllamaModelNotFoundError(
                f"Model '{self.model}' is not installed in Ollama. Run: ollama pull {self.model}"
            )
        if resp.status_code != 200:
            snippet = resp.text[:200]
            raise OllamaResponseError(f"Ollama returned HTTP {resp.status_code} for model '{self.model}': {snippet}")

        try:
            data = resp.json()
        except ValueError as e:
            raise OllamaResponseError("Ollama returned a non-JSON response.") from e

        error_text = data.get("error")
        if error_text:
            if "not found" in error_text.lower():
                raise OllamaModelNotFoundError(
                    f"Model '{self.model}' is not installed in Ollama. Run: ollama pull {self.model}"
                )
            raise OllamaResponseError(f"Ollama reported an error: {error_text}")

        message = data.get("message") or {}
        blocks: list[LLMContentBlock] = []
        text = message.get("content")
        if text:
            blocks.append(LLMContentBlock(type="text", text=text))
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            arguments = fn.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments else {}
                except ValueError:
                    arguments = {}
            blocks.append(
                LLMContentBlock(
                    type="tool_use",
                    id=call.get("id") or f"call_{uuid.uuid4().hex[:10]}",
                    name=fn.get("name", ""),
                    input=arguments or {},
                )
            )

        stop_reason = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return LLMResponse(content=blocks, stop_reason=stop_reason)


def check_ollama_reachable(base_url: str, model: str | None = None, timeout_s: float = 1.5) -> bool:
    """Lightweight connectivity check for `/health` -- lists locally
    installed models rather than running inference. Never raises."""
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout_s)
        if resp.status_code != 200:
            return False
        if model:
            names = {m.get("name") for m in (resp.json().get("models") or [])}
            # Ollama tags are typically "name:tag"; also accept a bare match
            # if the caller passed a tag-less model id.
            return any(n == model or (n and n.split(":")[0] == model.split(":")[0]) for n in names if n)
        return True
    except httpx.HTTPError:
        return False


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
