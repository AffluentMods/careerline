# SPDX-License-Identifier: Apache-2.0
"""Running model calls with every guard in one place.

Each call: loads the user's provider settings, checks the AI rate limit and the
monthly budget, renders a versioned prompt, redacts PII for hosted providers,
calls the model, validates the output against a schema (one retry), and logs an
``AiRun`` row (tokens, estimated cost, status; never the prompt or the output).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careerline import db as dbmod
from careerline.ai.models import AiRun, AiSettings
from careerline.ai.pricing import cost_micros
from careerline.ai.prompting import load_prompt, redact, strict_schema
from careerline.ai.providers import (
    LLM_TIMEOUT,
    AiError,
    AnthropicProvider,
    Completion,
    Effort,
    OllamaProvider,
    OpenAICompatibleProvider,
    Provider,
    ToolSpec,
    Turn,
)
from careerline.errors import ApiError
from careerline.net import SafeFetcher
from careerline.security import contexts
from careerline.security.crypto import FieldCipher
from careerline.security.ratelimit import check_and_hit

AI_CALLS_PER_WINDOW = 60
AI_WINDOW = timedelta(minutes=10)

DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "anthropic": {
        "scoring": "claude-haiku-5-5",
        "writing": "claude-opus-5-5",
        "chat": "claude-opus-5-5",
    },
    "openai": {"scoring": "", "writing": "", "chat": ""},
    "ollama": {"scoring": "", "writing": "", "chat": ""},
}
DEFAULT_BASE_URL = {"ollama": "http://localhost:11434", "openai": "https://api.openai.com/v1"}

# Which model setting each feature uses (cheap for scoring/extraction, strong for writing).
FEATURE_MODEL = {
    "score": "scoring",
    "questions": "scoring",
    "test": "scoring",
    "resume": "writing",
    "cover_letter": "writing",
    "assistant": "chat",
    "mock_interview": "chat",
}

# Tests can replace how providers are built.
ProviderBuilder = Callable[[AiSettings, str, bool], Provider]


def _key_context(user_id: uuid.UUID) -> str:
    return contexts.ai_key(user_id)


def model_for(settings: AiSettings, feature: str) -> str:
    slot = FEATURE_MODEL.get(feature, "writing")
    chosen = str(getattr(settings, f"model_{slot}") or "")
    return chosen or DEFAULT_MODELS.get(settings.provider, {}).get(slot, "")


def default_provider_builder(settings: AiSettings, api_key: str, allow_private: bool) -> Provider:
    if settings.provider == "anthropic":
        return AnthropicProvider(api_key)
    fetcher = SafeFetcher(allow_private=allow_private, timeout=LLM_TIMEOUT, max_bytes=10_000_000)
    if settings.provider == "openai":
        return OpenAICompatibleProvider(
            settings.base_url or DEFAULT_BASE_URL["openai"], api_key, fetcher
        )
    if settings.provider == "ollama":
        return OllamaProvider(settings.base_url or DEFAULT_BASE_URL["ollama"], fetcher)
    raise AiError("not_configured", "Turn on an AI provider in Settings → AI.")


_builder: ProviderBuilder = default_provider_builder


def set_provider_builder(builder: ProviderBuilder | None) -> None:
    global _builder
    _builder = builder or default_provider_builder


@dataclass
class AiClient:
    """A configured provider for one user, plus everything needed to guard and log calls."""

    user_id: uuid.UUID
    settings: AiSettings
    provider: Provider

    @property
    def hosted(self) -> bool:
        return self.settings.provider in ("anthropic", "openai")


async def get_settings(db: AsyncSession, user_id: uuid.UUID) -> AiSettings | None:
    return await db.get(AiSettings, user_id)


def is_configured(settings: AiSettings | None) -> bool:
    return settings is not None and settings.provider != "none"


async def client_for(
    db: AsyncSession, user_id: uuid.UUID, cipher: FieldCipher, *, allow_private: bool
) -> AiClient:
    settings = await get_settings(db, user_id)
    if settings is None or settings.provider == "none":
        raise AiError("not_configured", "Turn on an AI provider in Settings → AI.")
    api_key = (
        cipher.decrypt(settings.api_key_enc, _key_context(user_id)) if settings.api_key_enc else ""
    )
    return AiClient(
        user_id=user_id, settings=settings, provider=_builder(settings, api_key, allow_private)
    )


def encrypt_key(cipher: FieldCipher, user_id: uuid.UUID, key: str) -> str | None:
    return cipher.encrypt(key, _key_context(user_id)) if key else None


async def month_spend_micros(db: AsyncSession, user_id: uuid.UUID) -> int:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = await db.scalar(
        select(func.coalesce(func.sum(AiRun.cost_micros), 0)).where(
            AiRun.user_id == user_id, AiRun.created_at >= start
        )
    )
    return int(total or 0)


async def _guard(ai: AiClient) -> None:
    async with dbmod.get_sessionmaker()() as log:
        try:
            await check_and_hit(log, f"ai:{ai.user_id}", AI_CALLS_PER_WINDOW, AI_WINDOW)
        except ApiError:
            raise AiError(
                "rate_limited", "Too many AI requests. Try again in a few minutes.", retryable=True
            ) from None
        await log.commit()
        budget = ai.settings.monthly_budget_cents
        if budget is not None and await month_spend_micros(log, ai.user_id) >= budget * 10_000:
            raise AiError(
                "budget_reached", "This month's AI budget is used up. Raise it in Settings → AI."
            )


async def _log(
    ai: AiClient,
    *,
    feature: str,
    prompt: tuple[str, str],
    model: str,
    status: str,
    started: float,
    completion: Completion | None = None,
    error: str = "",
    subject: dict[str, Any] | None = None,
) -> None:
    tokens_in = completion.input_tokens if completion else 0
    tokens_out = completion.output_tokens if completion else 0
    actual_model = completion.model if completion else model
    async with dbmod.get_sessionmaker()() as log:
        log.add(
            AiRun(
                user_id=ai.user_id,
                feature=feature,
                prompt_name=prompt[0],
                prompt_version=prompt[1],
                provider=ai.settings.provider,
                model=actual_model[:100],
                status=status,
                error=error[:300],
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_micros=cost_micros(ai.settings.provider, actual_model, tokens_in, tokens_out),
                duration_ms=int((time.monotonic() - started) * 1000),
                subject=subject or {},
            )
        )
        await log.commit()


def _parse[M: BaseModel](text: str, schema_model: type[M]) -> M:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[cleaned.find("{") :] if "{" in cleaned else cleaned
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in the reply")
    return schema_model.model_validate(json.loads(cleaned[start : end + 1]))


async def structured[M: BaseModel](
    ai: AiClient,
    *,
    feature: str,
    prompt_name: str,
    values: dict[str, str],
    schema_model: type[M],
    max_tokens: int = 4000,
    effort: Effort = "medium",
    subject: dict[str, Any] | None = None,
) -> M:
    """Render a prompt, call the model with a strict JSON schema, and validate the reply."""
    template = load_prompt(prompt_name)
    if ai.hosted and ai.settings.redact_pii:
        values = {k: redact(v) for k, v in values.items()}
    system, user = template.render(**values)
    model = model_for(ai.settings, feature)
    if not model:
        raise AiError("no_model", "Choose a model in Settings → AI.")
    schema = strict_schema(schema_model)
    turns = [Turn(role="user", text=user)]
    meta = (template.name, template.version)
    await _guard(ai)
    for attempt in (1, 2):
        started = time.monotonic()
        try:
            completion = await ai.provider.complete(
                model=model,
                system=system,
                turns=turns,
                max_tokens=max_tokens,
                json_schema=schema,
                effort=effort,
            )
        except AiError as exc:
            await _log(
                ai,
                feature=feature,
                prompt=meta,
                model=model,
                status="refused" if exc.code == "refused" else "error",
                started=started,
                error=exc.message,
                subject=subject,
            )
            raise
        try:
            parsed = _parse(completion.text, schema_model)
        except (ValueError, ValidationError) as exc:
            await _log(
                ai,
                feature=feature,
                prompt=meta,
                model=model,
                status="invalid",
                started=started,
                completion=completion,
                error=str(exc)[:300],
                subject=subject,
            )
            if attempt == 2:
                raise AiError(
                    "invalid_output", "The model's answer didn't match the expected format."
                ) from None
            turns = [
                *turns,
                Turn(role="assistant", text=completion.text or "(empty)"),
                Turn(
                    role="user",
                    text=f"That reply didn't match the required JSON schema ({str(exc)[:200]}). "
                    "Reply again with only the JSON object.",
                ),
            ]
            continue
        await _log(
            ai,
            feature=feature,
            prompt=meta,
            model=model,
            status="ok",
            started=started,
            completion=completion,
            subject=subject,
        )
        return parsed
    raise AiError(
        "invalid_output", "The model's answer didn't match the expected format."
    )  # pragma: no cover


async def chat_step(
    ai: AiClient,
    *,
    feature: str,
    system_prompt: tuple[str, str, str],
    turns: list[Turn],
    tools: list[ToolSpec] | None = None,
    json_schema: dict[str, Any] | None = None,
    max_tokens: int = 4000,
    effort: Effort = "medium",
    subject: dict[str, Any] | None = None,
) -> Completion:
    """One model turn in a multi-turn conversation (assistant, mock interview)."""
    name, version, system = system_prompt
    model = model_for(ai.settings, feature)
    if not model:
        raise AiError("no_model", "Choose a model in Settings → AI.")
    await _guard(ai)
    started = time.monotonic()
    try:
        completion = await ai.provider.complete(
            model=model,
            system=system,
            turns=turns,
            tools=tools,
            json_schema=json_schema,
            max_tokens=max_tokens,
            effort=effort,
        )
    except AiError as exc:
        await _log(
            ai,
            feature=feature,
            prompt=(name, version),
            model=model,
            status="refused" if exc.code == "refused" else "error",
            started=started,
            error=exc.message,
            subject=subject,
        )
        raise
    await _log(
        ai,
        feature=feature,
        prompt=(name, version),
        model=model,
        status="ok",
        started=started,
        completion=completion,
        subject=subject,
    )
    return completion


def to_api_error(exc: AiError) -> ApiError:
    status = {
        "not_configured": 409,
        "no_key": 409,
        "no_model": 409,
        "no_base_url": 409,
        "budget_reached": 402,
        "rate_limited": 429,
        "refused": 422,
        "invalid_output": 502,
    }.get(exc.code, 502)
    return ApiError(status, f"ai_{exc.code}", exc.message)
