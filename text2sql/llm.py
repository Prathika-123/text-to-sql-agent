"""Minimal LLM abstraction so the engine isn't tied to one vendor.

Gemini is the default (the reference project uses Gemini via Google ADK);
Anthropic Claude is supported too. SDKs are imported lazily.
"""
from __future__ import annotations

import os
from typing import Protocol

from . import config

Message = dict  # {"role": "user" | "assistant", "content": str}


class LLM(Protocol):
    def complete(self, system: str, messages: list[Message], json_mode: bool = False) -> str: ...


class GeminiLLM:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        from google import genai
        from google.genai import types

        key = api_key or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        key = key.strip().strip("\"'") if key else key
        if not key or key.startswith("your-"):
            raise RuntimeError("Set GOOGLE_API_KEY in your environment or .env file.")
        # timeout is in milliseconds; without it a stalled request spins forever
        self.client = genai.Client(
            api_key=key, http_options=types.HttpOptions(timeout=int(config.LLM_TIMEOUT_S * 1000)))
        self.key_hint = f"...{key[-4:]}"
        self.model = model or config.GEMINI_MODEL

    def complete(self, system, messages, json_mode=False):
        from google.genai import types

        contents = [
            types.Content(role="model" if m["role"] == "assistant" else "user",
                          parts=[types.Part(text=m["content"])])
            for m in messages
        ]
        kwargs = {}
        # Gemini 3.x thinks at "medium" by default, which is slow for text-to-SQL.
        if self.model.startswith("gemini-3") and hasattr(types.ThinkingConfig, "model_fields") \
                and "thinking_level" in types.ThinkingConfig.model_fields:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=config.GEMINI_THINKING_LEVEL.upper())
        cfg = types.GenerateContentConfig(
            system_instruction=system, temperature=0.0,
            response_mime_type="application/json" if json_mode else None, **kwargs,
        )
        try:
            resp = self.client.models.generate_content(model=self.model, contents=contents, config=cfg)
        except Exception as e:
            if "API_KEY_INVALID" in str(e) or "API key not valid" in str(e):
                raise RuntimeError(
                    f"Gemini rejected the API key (the one in use ends with '{self.key_hint}'). Create a new key at "
                    "https://aistudio.google.com/apikey, paste it into .env as GOOGLE_API_KEY=<key> (no quotes, no spaces), "
                    "save, and restart.") from e
            raise
        return resp.text or ""


class AnthropicLLM:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        import anthropic

        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("Set ANTHROPIC_API_KEY in your environment or .env file.")
        self.client = anthropic.Anthropic(api_key=key, timeout=config.LLM_TIMEOUT_S)
        self.model = model or config.ANTHROPIC_MODEL

    def complete(self, system, messages, json_mode=False):
        if json_mode:
            system += "\n\nRespond with a single JSON object and nothing else."
        resp = self.client.messages.create(
            model=self.model, max_tokens=1500, temperature=0, system=system, messages=messages)
        return "".join(b.text for b in resp.content if b.type == "text")


def get_llm(provider: str | None = None, model: str | None = None) -> LLM:
    provider = (provider or config.LLM_PROVIDER).lower()
    if provider == "gemini":
        return GeminiLLM(model)
    if provider in ("anthropic", "claude"):
        return AnthropicLLM(model)
    raise ValueError(f"Unknown LLM provider '{provider}' (use 'gemini' or 'anthropic').")