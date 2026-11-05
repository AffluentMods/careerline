# SPDX-License-Identifier: Apache-2.0
"""Model providers behind one small interface.

* Anthropic (Claude) through the official ``anthropic`` SDK, always with the
  user's own key passed explicitly (never ambient credentials from the server).
* Any OpenAI-compatible ``/chat/completions`` endpoint.
* Ollama's ``/api/chat`` for local models.

The OpenAI-compatible and Ollama providers go through :class:`SafeFetcher`, so a
configured base URL can't be used to reach private addresses unless the instance
allows it.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import anthropic
import httpx

from careerline.net import FetchError, SafeFetcher

Effort = Literal["low", "medium", "high"]

# Models that accept server-side refusal fallbacks ("default" routing).
FALLBACK_MODELS = frozenset(
    {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Model families that accept output_config.effort.
_EFFORT_PREFIXES = (
    "claude-fable-",
    "claude-mythos-",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-5",
)
LLM_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
ANTHROPIC_API_URL = "https://api.anthropic.com"


class AiError(Exception):
    """A model call failed. ``message`` is safe to show the user."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class AiRefused(AiError):
    def __init__(self) -> None:
        super().__init__("refused", "The model declined this request.")


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Turn:
    """One provider-neutral conversation entry."""

    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    is_error: bool = False
    # Provider-native assistant content to send back unchanged (e.g. Claude's blocks).
    native: Any = None


@dataclass
class Completion:
    text: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str
    native: Any = None


class Provider(ABC):
    name: str = ""

    @abstractmethod
    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: list[Turn],
        max_tokens: int = 4000,
        json_schema: dict[str, Any] | None = None,
        tools: list[ToolSpec] | None = None,
        effort: Effort | None = None,
    ) -> Completion: ...


# --- Anthropic ----------------------------------------------------------------------------


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, http_client: Any = None) -> None:
        if not api_key:
            raise AiError("no_key", "Add your Anthropic API key in Settings → AI.")
        self._api_key = api_key
        self._http_client = http_client

    def _client(self) -> anthropic.AsyncAnthropic:
        # Everything explicit: the user's own key and the official endpoint, so server-side
        # environment variables or credential profiles can never redirect or replace them.
        return anthropic.AsyncAnthropic(
            api_key=self._api_key,
            base_url=ANTHROPIC_API_URL,
            max_retries=2,
            timeout=180.0,
            http_client=self._http_client,
        )

    @staticmethod
    def _messages(turns: list[Turn]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in turns:
            if t.role == "user":
                out.append({"role": "user", "content": t.text})
            elif t.role == "assistant":
                out.append(
                    {
                        "role": "assistant",
                        "content": t.native if t.native else t.text or "(no reply)",
                    }
                )
            else:
                block = {
                    "type": "tool_result",
                    "tool_use_id": t.tool_call_id,
                    "content": t.text,
                    "is_error": t.is_error,
                }
                # All results for one assistant turn go back in a single user message.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: list[Turn],
        max_tokens: int = 4000,
        json_schema: dict[str, Any] | None = None,
        tools: list[ToolSpec] | None = None,
        effort: Effort | None = None,
    ) -> Completion:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": self._messages(turns),
        }
        output_config: dict[str, Any] = {}
        if effort and model.startswith(_EFFORT_PREFIXES):
            output_config["effort"] = effort
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if output_config:
            kwargs["output_config"] = output_config
        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.schema,
                    "strict": True,
                }
                for t in tools
            ]
        client = self._client()
        try:
            if model in FALLBACK_MODELS:
                # On a policy decline the API re-runs the request on the recommended fallback.
                response = await client.beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **kwargs
                )
            else:
                response = await client.messages.create(**kwargs)
        except anthropic.AuthenticationError:
            raise AiError("auth_failed", "Anthropic rejected the API key.") from None
        except anthropic.PermissionDeniedError:
            raise AiError("forbidden", "This API key can't use that model.") from None
        except anthropic.NotFoundError:
            raise AiError(
                "model_not_found", f"Anthropic doesn't recognize the model {model!r}."
            ) from None
        except anthropic.RateLimitError:
            raise AiError(
                "rate_limited",
                "Anthropic is rate-limiting requests. Try again shortly.",
                retryable=True,
            ) from None
        except anthropic.BadRequestError as exc:
            raise AiError(
                "bad_request", f"Anthropic rejected the request: {str(exc.message)[:200]}"
            ) from None
        except anthropic.APIStatusError as exc:
            raise AiError(
                "provider_error",
                f"Anthropic returned an error ({exc.status_code}).",
                retryable=exc.status_code >= 500,
            ) from None
        except anthropic.APIConnectionError:
            raise AiError("unreachable", "Couldn't reach Anthropic.", retryable=True) from None
        finally:
            if self._http_client is None:  # only close what we created
                await client.close()

        if response.stop_reason == "refusal":
            raise AiRefused()
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
                )
        usage = response.usage
        input_tokens = (
            (usage.input_tokens or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        return Completion(
            text="".join(text_parts),
            tool_calls=calls,
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens or 0,
            model=response.model,
            stop_reason=str(response.stop_reason or ""),
            native=[b.to_dict() for b in response.content],
        )


# --- OpenAI-compatible ----------------------------------------------------------------------


def _http_error(name: str, status: int, body: Any) -> AiError:
    detail = ""
    if isinstance(body, dict):
        err = body.get("error")
        detail = str(err.get("message", "") if isinstance(err, dict) else err or "")[:200]
    if status in (401, 403):
        return AiError("auth_failed", f"{name} rejected the API key.")
    if status == 404:
        return AiError("model_not_found", f"{name} couldn't find that model or endpoint.")
    if status == 429:
        return AiError("rate_limited", f"{name} is rate-limiting requests.", retryable=True)
    if status >= 500:
        return AiError("provider_error", f"{name} returned an error ({status}).", retryable=True)
    return AiError(
        "bad_request",
        f"{name} rejected the request: {detail}"
        if detail
        else f"{name} rejected the request ({status}).",
    )


class OpenAICompatibleProvider(Provider):
    name = "openai"

    def __init__(self, base_url: str, api_key: str, fetcher: SafeFetcher) -> None:
        if not base_url:
            raise AiError("no_base_url", "Set the API base URL in Settings → AI.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.fetcher = fetcher

    @staticmethod
    def _messages(system: str, turns: list[Turn]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for t in turns:
            if t.role == "user":
                out.append({"role": "user", "content": t.text})
            elif t.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": t.text or None}
                if t.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in t.tool_calls
                    ]
                out.append(msg)
            else:
                out.append({"role": "tool", "tool_call_id": t.tool_call_id, "content": t.text})
        return out

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: list[Turn],
        max_tokens: int = 4000,
        json_schema: dict[str, Any] | None = None,
        tools: list[ToolSpec] | None = None,
        effort: Effort | None = None,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model,
            "messages": self._messages(system, turns),
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": json_schema, "strict": True},
            }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.schema,
                    },
                }
                for t in tools
            ]
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            result = await self.fetcher.post(
                f"{self.base_url}/chat/completions", json=body, headers=headers, max_redirects=0
            )
        except FetchError as exc:
            raise AiError("unreachable", exc.message, retryable=exc.code == "timeout") from None
        data = result.json() if result.body else {}
        if result.status >= 400:
            raise _http_error("The AI endpoint", result.status, data)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls = []
        for c in message.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            calls.append(
                ToolCall(
                    id=c.get("id") or uuid.uuid4().hex,
                    name=fn.get("name", ""),
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        if choice.get("finish_reason") == "content_filter":
            raise AiRefused()
        usage = data.get("usage") or {}
        return Completion(
            text=message.get("content") or "",
            tool_calls=calls,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model=str(data.get("model") or model),
            stop_reason=str(choice.get("finish_reason") or ""),
        )


# --- Ollama -----------------------------------------------------------------------------------


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, base_url: str, fetcher: SafeFetcher) -> None:
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.fetcher = fetcher

    async def complete(
        self,
        *,
        model: str,
        system: str,
        turns: list[Turn],
        max_tokens: int = 4000,
        json_schema: dict[str, Any] | None = None,
        tools: list[ToolSpec] | None = None,
        effort: Effort | None = None,
    ) -> Completion:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for t in turns:
            if t.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": t.text}
                if t.tool_calls:
                    msg["tool_calls"] = [
                        {"function": {"name": c.name, "arguments": c.arguments}}
                        for c in t.tool_calls
                    ]
                messages.append(msg)
            elif t.role == "tool":
                messages.append({"role": "tool", "content": t.text})
            else:
                messages.append({"role": "user", "content": t.text})
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if json_schema is not None:
            body["format"] = json_schema
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.schema,
                    },
                }
                for t in tools
            ]
        try:
            result = await self.fetcher.post(
                f"{self.base_url}/api/chat", json=body, max_redirects=0
            )
        except FetchError as exc:
            raise AiError(
                "unreachable",
                f"Couldn't reach Ollama: {exc.message}",
                retryable=exc.code == "timeout",
            ) from None
        data = result.json() if result.body else {}
        if result.status >= 400:
            raise _http_error("Ollama", result.status, data)
        message = data.get("message") or {}
        calls = [
            ToolCall(
                id=uuid.uuid4().hex,
                name=(c.get("function") or {}).get("name", ""),
                arguments=(c.get("function") or {}).get("arguments") or {},
            )
            for c in message.get("tool_calls") or []
        ]
        return Completion(
            text=message.get("content") or "",
            tool_calls=calls,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            model=str(data.get("model") or model),
            stop_reason=str(data.get("done_reason") or ""),
        )


# --- test double --------------------------------------------------------------------------


@dataclass
class FakeProvider(Provider):
    """Answers from a script. Each entry is a Completion, an Exception, or a callable."""

    script: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    name: str = "fake"

    async def complete(self, **kwargs: Any) -> Completion:
        self.calls.append(kwargs)
        if not self.script:
            raise AiError("script_empty", "FakeProvider has no more answers.")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            step = step(**kwargs)
        assert isinstance(step, Completion)  # noqa: S101
        return step


def fake_text(
    text: str, *, input_tokens: int = 100, output_tokens: int = 50, model: str = "fake-model"
) -> Completion:
    return Completion(
        text=text,
        tool_calls=[],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
        stop_reason="end_turn",
    )


ProviderFactory = Callable[[], Provider]
