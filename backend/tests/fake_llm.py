"""A deterministic, scriptable fake LLM client for agent-loop tests.

Tests never call the real Anthropic API. This fake replays a
pre-programmed sequence of tool calls / final text so we can exercise the
full orchestrator loop (multi-tool reasoning, action confirmation, error
handling) deterministically.
"""
from __future__ import annotations

from typing import Any

from app.agent.llm_client import LLMContentBlock, LLMResponse


class ScriptedLLMClient:
    """Each call to create_message pops the next scripted response.

    A "turn" in the script is either:
      * {"tool_calls": [{"name": ..., "input": {...}}, ...]}  -> emits tool_use blocks
      * {"text": "final answer"}                                -> emits a text block (ends the loop)
    """

    def __init__(self, script: list[dict[str, Any]]):
        self.script = list(script)
        self.calls = 0

    def create_message(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls += 1
        if not self.script:
            return LLMResponse(content=[LLMContentBlock(type="text", text="(fake LLM script exhausted)")])
        step = self.script.pop(0)
        if "tool_calls" in step:
            blocks = [
                LLMContentBlock(type="tool_use", id=f"toolu_{i}", name=tc["name"], input=tc["input"])
                for i, tc in enumerate(step["tool_calls"])
            ]
            return LLMResponse(content=blocks, stop_reason="tool_use")
        return LLMResponse(content=[LLMContentBlock(type="text", text=step["text"])], stop_reason="end_turn")


class LoopingLLMClient:
    """Always returns a tool call -- used to test the max-depth guard."""

    def __init__(self, tool_name: str, tool_input: dict[str, Any]):
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.calls = 0

    def create_message(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            content=[LLMContentBlock(type="tool_use", id=f"toolu_{self.calls}", name=self.tool_name, input=self.tool_input)],
            stop_reason="tool_use",
        )
