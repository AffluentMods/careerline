# SPDX-License-Identifier: Apache-2.0
"""AI settings, scoring, guards, and usage, with a scripted fake model."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from conftest import TEST_KEY
from fake_web import FakeWeb
from sqlalchemy import select, update

from careerline import db as dbmod
from careerline.ai import service as ai_service
from careerline.ai.models import AiRun, AiSettings, JobScore
from careerline.ai.providers import AiError, FakeProvider, fake_text
from careerline.config import Settings
from careerline.security.crypto import FieldCipher
from careerline.worker.runner import WorkerContext, drain

A = "/api/v1/ai"
D = "/api/v1/discovery"
V = "/api/v1/vault"
GH = {
    "kind": "greenhouse",
    "name": "Northwind",
    "config": {"board": "northwind", "company": "Northwind Tools"},
}


@pytest.fixture
def fake_ai() -> Iterator[FakeProvider]:
    fake = FakeProvider()
    seen: list[tuple[str, str, bool]] = []

    def build(settings: AiSettings, key: str, allow_private: bool) -> FakeProvider:
        seen.append((settings.provider, key, allow_private))
        return fake

    ai_service.set_provider_builder(build)  # type: ignore[arg-type]
    fake.built = seen  # type: ignore[attr-defined]
    yield fake
    ai_service.set_provider_builder(None)


def worker(settings: Settings) -> WorkerContext:
    return WorkerContext(
        sessionmaker=dbmod.get_sessionmaker(),
        settings=settings,
        cipher=FieldCipher(f"1:{TEST_KEY}"),
        fetcher_factory=FakeWeb().factory(),
        worker_id="test",
    )


async def _enable(c: httpx.AsyncClient, **over: Any) -> httpx.Response:
    body = {"provider": "anthropic", "api_key": "sk-ant-test-key", **over}
    return await c.put(f"{A}/settings", json=body)


async def _vault(c: httpx.AsyncClient) -> dict[str, str]:
    role = (
        await c.post(
            f"{V}/entries",
            json={
                "kind": "role",
                "data": {"title": "Security Engineer", "employer": "Contoso", "start": "2020-01"},
            },
        )
    ).json()
    ach = (
        await c.post(
            f"{V}/entries",
            json={
                "kind": "achievement",
                "parent_id": role["id"],
                "data": {"text": "Built detections in Python"},
            },
        )
    ).json()
    skill = (
        await c.post(f"{V}/entries", json={"kind": "skill", "data": {"name": "Python"}})
    ).json()
    draft = (
        await c.post(
            f"{V}/entries", json={"kind": "skill", "data": {"name": "Rust"}, "verified": False}
        )
    ).json()
    return {"role": role["id"], "ach": ach["id"], "skill": skill["id"], "draft": draft["id"]}


async def _postings(c: httpx.AsyncClient, settings: Settings) -> list[dict[str, Any]]:
    await c.post(f"{D}/sources", json=GH)
    await drain(worker(settings))
    return (await c.get(f"{D}/postings", params={"view": "all"})).json()["items"]


# --- settings ------------------------------------------------------------------------------


async def test_settings_roundtrip_and_key_encryption(user_client: httpx.AsyncClient) -> None:
    c = user_client
    s = (await c.get(f"{A}/settings")).json()
    assert s["provider"] == "none" and s["has_api_key"] is False
    assert s["defaults"]["anthropic"] == {
        "scoring": "claude-haiku-5-5",
        "writing": "claude-opus-5-5",
        "chat": "claude-opus-5-5",
    }
    assert s["private_endpoints_allowed"] is True  # the first user is the instance owner

    assert (
        await c.put(f"{A}/settings", json={"provider": "anthropic"})
    ).status_code == 422  # key required
    r = await _enable(c, monthly_budget_cents=500)
    assert r.status_code == 200 and r.json()["has_api_key"] is True and "api_key" not in r.json()
    async for db in dbmod.get_db():
        row = await db.get(AiSettings, (await db.scalar(select(AiSettings.user_id))))
    assert row is not None and row.api_key_enc and "sk-ant-test-key" not in row.api_key_enc

    # Changing other fields keeps the key; "" removes it.
    r = await c.put(f"{A}/settings", json={"provider": "anthropic", "redact_pii": False})
    assert r.json()["has_api_key"] is True and r.json()["redact_pii"] is False
    r = await c.put(f"{A}/settings", json={"provider": "none", "api_key": ""})
    assert r.json()["has_api_key"] is False

    r = await c.put(
        f"{A}/settings", json={"provider": "openai", "base_url": "https://llm.example.com/v1"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "models_required"
    r = await c.put(
        f"{A}/settings",
        json={
            "provider": "ollama",
            "base_url": "file:///etc",
            "model_scoring": "m",
            "model_writing": "m",
            "model_chat": "m",
        },
    )
    assert r.status_code == 422


async def test_connection_test(user_client: httpx.AsyncClient, fake_ai: FakeProvider) -> None:
    c = user_client
    r = await c.post(f"{A}/settings/test")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ai_not_configured"
    await _enable(c)
    fake_ai.script = [fake_text('{"ok": true}')]
    r = await c.post(f"{A}/settings/test")
    assert (
        r.status_code == 200 and r.json()["ok"] is True and r.json()["model"] == "claude-haiku-5-5"
    )
    assert fake_ai.built[-1] == ("anthropic", "sk-ant-test-key", True)  # type: ignore[attr-defined]
    call = fake_ai.calls[-1]
    assert (
        call["model"] == "claude-haiku-5-5" and call["json_schema"]["additionalProperties"] is False
    )
    fake_ai.script = [AiError("auth_failed", "Anthropic rejected the API key.")]
    r = await c.post(f"{A}/settings/test")
    assert (
        r.status_code == 502 and r.json()["error"]["message"] == "Anthropic rejected the API key."
    )


# --- scoring -----------------------------------------------------------------------------------


async def test_rule_scores_by_default(user_client: httpx.AsyncClient, settings: Settings) -> None:
    c = user_client
    await _vault(c)
    items = await _postings(c, settings)
    assert all(p["score"] is not None and p["score"]["method"] == "rules" for p in items)
    eng = next(p for p in items if p["title"] == "Security Engineer")
    detail = (await c.get(f"{D}/postings/{eng['id']}")).json()["score_detail"]
    assert detail["method"] == "rules" and detail["stale"] is False
    assert any("Python" in m["requirement"] for m in detail["matched"])

    # A vault change makes scores stale, and the debounced rescore refreshes keyword scores.
    await c.post(f"{V}/entries", json={"kind": "skill", "data": {"name": "Splunk"}})
    assert (await c.get(f"{D}/postings/{eng['id']}")).json()["score"]["stale"] is True
    async for db in dbmod.get_db():
        from careerline.discovery.models import QueuedJob

        await db.execute(
            update(QueuedJob)
            .where(QueuedJob.kind == "score_postings")
            .values(run_after=QueuedJob.created_at)
        )
        await db.commit()
    await drain(worker(settings))
    assert (await c.get(f"{D}/postings/{eng['id']}")).json()["score"]["stale"] is False


async def test_ai_scoring_validates_and_keeps_facts_honest(
    user_client: httpx.AsyncClient, settings: Settings, fake_ai: FakeProvider
) -> None:
    c = user_client
    ids = await _vault(c)
    items = await _postings(c, settings)
    eng = next(p for p in items if p["title"] == "Security Engineer")
    await _enable(c)
    reply = {
        "score": 140,  # clamped to 100
        "summary": "You build detections in Python, which is the core of this role.",
        "matched": [
            {"requirement": "Python", "fact_ids": [ids["ach"], ids["draft"], "made-up-id"]}
        ],
        "gaps": ["Splunk"],
        "red_flags": [],
    }
    fake_ai.script = [
        fake_text("not json at all"),
        fake_text("```json\n" + json.dumps(reply) + "\n```"),
    ]
    r = await c.post(f"{A}/score/{eng['id']}", json={"method": "ai"})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert (
        detail["score"] == 100
        and detail["method"] == "ai"
        and detail["model"] == "claude-haiku-5-5"
    )
    # Only the user's usable facts survive as evidence: drafts and invented ids are dropped.
    assert detail["matched"][0]["evidence"] == ["Built detections in Python"]
    # Hard-filter reasons are kept next to the model's flags.
    assert detail["gaps"] == ["Splunk"]

    # What went to the model: posting wrapped as untrusted data, no tools, draft facts excluded.
    first = fake_ai.calls[0]
    user_text = first["turns"][0].text
    assert '<untrusted_document kind="job_posting">' in user_text and "Rust" not in user_text
    assert first.get("tools") is None and "untrusted_document" in first["system"]
    assert first["effort"] == "low"
    retry = fake_ai.calls[1]["turns"]
    assert len(retry) == 3 and "didn't match the required JSON schema" in retry[2].text

    async for db in dbmod.get_db():
        runs = (await db.scalars(select(AiRun).order_by(AiRun.created_at))).all()
    assert [r.status for r in runs] == ["invalid", "ok"]
    assert runs[1].prompt_name == "fit_score" and runs[1].prompt_version == "1"
    assert runs[1].cost_micros is None  # "fake-model" has no known price
    usage = (await c.get(f"{A}/usage")).json()
    assert usage["month_calls"] == 2 and usage["by_feature"][0]["feature"] == "score"


async def test_pii_is_redacted_for_hosted_providers(
    user_client: httpx.AsyncClient, settings: Settings, fake_ai: FakeProvider
) -> None:
    c = user_client
    await _vault(c)
    await _enable(c)
    r = await c.post(
        f"{D}/import",
        json={
            "title": "Analyst",
            "company": "Contoso",
            "text": "Requirements:\n- Python\nApply: recruiter@contoso.example or (555) 010-0123.",
        },
    )
    posting = r.json()
    fake_ai.script = [
        fake_text(
            json.dumps({"score": 50, "summary": "ok", "matched": [], "gaps": [], "red_flags": []})
        )
    ]
    await c.post(f"{A}/score/{posting['id']}", json={"method": "ai"})
    sent = fake_ai.calls[-1]["turns"][0].text
    assert (
        "recruiter@contoso.example" not in sent
        and "[email removed]" in sent
        and "[phone removed]" in sent
    )


async def test_auto_scoring_uses_ai_for_new_matches(
    user_client: httpx.AsyncClient, settings: Settings, fake_ai: FakeProvider
) -> None:
    c = user_client
    await _vault(c)
    await _enable(c)
    await c.put(f"{D}/filters", json={"exclude_keywords": ["Tier 1 SOC"]})
    reply = json.dumps(
        {"score": 77, "summary": "Good fit.", "matched": [], "gaps": [], "red_flags": []}
    )
    fake_ai.script = [fake_text(reply) for _ in range(5)]
    items = await _postings(c, settings)
    methods = {p["title"]: p["score"]["method"] for p in items}
    # The filtered-out posting gets a free keyword score; the match gets the AI.
    assert methods == {"Security Engineer": "ai", "Tier 1 SOC Analyst": "rules"}
    assert len(fake_ai.calls) == 1


async def test_budget_and_failures_fall_back_to_rules(
    user_client: httpx.AsyncClient, settings: Settings, fake_ai: FakeProvider
) -> None:
    c = user_client
    await _vault(c)
    await _enable(c, monthly_budget_cents=1)
    async for db in dbmod.get_db():
        uid = await db.scalar(select(AiSettings.user_id))
        db.add(
            AiRun(
                user_id=uid,
                feature="score",
                provider="anthropic",
                model="claude-opus-5-5",
                status="ok",
                input_tokens=10_000,
                output_tokens=0,
                cost_micros=40_000,
            )
        )
        await db.commit()
    items = await _postings(c, settings)
    assert all(p["score"]["method"] == "rules" for p in items)  # budget used up: no AI calls
    assert fake_ai.calls == []
    r = await c.post(f"{A}/score/{items[0]['id']}", json={"method": "ai"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "ai_budget_reached"
    # The user can still ask for a keyword score.
    assert (
        await c.post(f"{A}/score/{items[0]['id']}", json={"method": "rules"})
    ).status_code == 200


async def test_ai_rate_limit(
    user_client: httpx.AsyncClient, settings: Settings, fake_ai: FakeProvider
) -> None:
    c = user_client
    await _enable(c)
    fake_ai.script = [fake_text('{"ok": true}') for _ in range(70)]
    statuses = [
        (await c.post(f"{A}/settings/test")).status_code
        for _ in range(ai_service.AI_CALLS_PER_WINDOW + 1)
    ]
    assert statuses[:-1] == [200] * ai_service.AI_CALLS_PER_WINDOW and statuses[-1] == 429


async def test_isolation(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
    settings: Settings,
    fake_ai: FakeProvider,
) -> None:
    a, b = two_users
    await _vault(a)
    items = await _postings(a, settings)
    await _enable(a)
    assert (await b.get(f"{A}/settings")).json()["provider"] == "none"
    assert (await b.get(f"{A}/settings")).json()[
        "private_endpoints_allowed"
    ] is False  # not the owner
    assert (
        await b.post(f"{A}/score/{items[0]['id']}", json={"method": "rules"})
    ).status_code == 404
    assert (await b.get(f"{A}/usage")).json()["month_calls"] == 0
    async for db in dbmod.get_db():
        assert len((await db.scalars(select(JobScore))).all()) == len(items)  # all A's
