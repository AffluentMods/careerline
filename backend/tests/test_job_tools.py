# SPDX-License-Identifier: Apache-2.0
"""The reviewer pass, the ATS check, and follow-up drafts."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import update

from careerline import db as dbmod
from careerline.ai.providers import FakeProvider, fake_text
from careerline.materials import ats
from careerline.tracker.models import ApplicationEvent
from tests.test_materials import M, _posting, _vault, fake_ai  # noqa: F401  (fixture)

T = "/api/v1/tracker"


def test_posting_keywords_find_skills_and_tech_terms() -> None:
    words = ats.posting_keywords(
        "Detection Engineer",
        "Requirements:\n- Python and SQL\n- Splunk SIEM experience\n- Splunk dashboards\n"
        "- Strong communication skills",
        ["Python", "Go"],
    )
    assert words[0] == "Python"
    assert "Splunk" in words and "SIEM" in words
    assert "Go" not in words  # the posting doesn't ask for it
    assert not {"Strong", "experience", "skills"} & set(words)


async def test_ai_drafts_get_a_reviewer_pass(
    user_client: httpx.AsyncClient,
    fake_ai: FakeProvider,  # noqa: F811
) -> None:
    c = user_client
    ids = await _vault(c)
    posting = await _posting(c)
    await c.put("/api/v1/ai/settings", json={"provider": "anthropic", "api_key": "sk-ant-x"})
    tailor = {
        "summary": {"text": "Analyst who defends against phishing.", "fact_ids": [ids["ach"]]},
        "roles": [
            {
                "role_id": ids["role"],
                "bullets": [{"text": "Worked on phishing stuff", "fact_ids": [ids["ach"]]}],
            }
        ],
        "skill_ids": [ids["skill"]],
        "project_ids": [],
    }
    fake_ai.script = [fake_text(json.dumps(tailor)), fake_text("{}")]  # placeholder, replaced
    first = await c.post(
        f"{M}/documents",
        json={"kind": "resume", "method": "ai", "posting_id": posting["id"], "review": False},
    )
    doc = first.json()
    bullet = doc["content"]["experience"][0]["bullets"][0]
    summary = doc["content"]["summary"]
    assert "review" not in doc["content"]

    review = {
        "summary": "Clear, but the bullet is vague.",
        "revisions": [
            {
                "item_id": bullet["id"],
                "text": "Cut phishing clicks by 40% with targeted training",
                "fact_ids": [ids["ach"]],
                "why": "Leads with the result.",
            },
            {
                "item_id": summary["id"],
                "text": "Analyst who cut phishing by 75%",
                "fact_ids": [ids["ach"]],
                "why": "Stronger number.",
            },
            {"item_id": "nope", "text": "x", "fact_ids": [], "why": ""},
        ],
        "missing": [
            {"requirement": "Python", "supported": True},
            {"requirement": "Kubernetes", "supported": False},
        ],
    }
    fake_ai.script = [fake_text(json.dumps(review))]
    r = await c.post(f"{M}/documents/{doc['id']}/review")
    assert r.status_code == 200, r.text
    got = r.json()
    rec = got["content"]["review"]
    assert [ch["after"] for ch in rec["changes"]] == [
        "Cut phishing clicks by 40% with targeted training"
    ]
    # An invented number never makes it in.
    assert rec["discarded"][0]["text"] == "Analyst who cut phishing by 75%"
    assert got["content"]["summary"]["text"] == "Analyst who defends against phishing."
    assert got["content"]["experience"][0]["bullets"][0]["status"] == "linked"
    assert {m["requirement"] for m in rec["missing"]} == {"Python", "Kubernetes"}
    assert got["flagged"] == 0
    review_call = fake_ai.calls[-1]
    assert '<untrusted_document kind="job_posting">' in review_call["turns"][0].text

    # Generating with AI runs the review automatically; a failed review keeps the draft.
    fake_ai.script = [fake_text(json.dumps(tailor))]
    auto = (
        await c.post(
            f"{M}/documents", json={"kind": "resume", "method": "ai", "posting_id": posting["id"]}
        )
    ).json()
    assert "no more answers" in auto["content"]["review"]["error"]

    # A master resume has no job to review against.
    master = (await c.post(f"{M}/documents", json={"kind": "resume"})).json()
    r = await c.post(f"{M}/documents/{master['id']}/review")
    assert r.status_code == 422


async def test_ats_check_reads_the_exported_file(user_client: httpx.AsyncClient) -> None:
    c = user_client
    await _vault(c)
    posting = await _posting(c)
    doc = (
        await c.post(f"{M}/documents", json={"kind": "resume", "posting_id": posting["id"]})
    ).json()
    for fmt in ("pdf", "docx"):
        r = await c.get(f"{M}/documents/{doc['id']}/ats-check", params={"format": fmt})
        assert r.status_code == 200, r.text
        result = r.json()
        checks = {ch["id"]: ch for ch in result["checks"]}
        assert checks["text"]["ok"] and checks["contact"]["ok"], checks
        assert checks["garbled"]["ok"] and checks["order"]["ok"]
        assert "jordan@example.com" in result["text"]
        kw = result["keywords"]
        assert "Python" in kw["covered"] and kw["score"] is not None

    master = (await c.post(f"{M}/documents", json={"kind": "resume"})).json()
    result = (await c.get(f"{M}/documents/{master['id']}/ats-check")).json()
    assert result["keywords"] is None and result["pages"] == 1


async def test_follow_up_drafts(user_client: httpx.AsyncClient) -> None:
    c = user_client
    await c.put("/api/v1/vault/profile", json={"full_name": "Jordan Rivera"})
    applied = date.today() - timedelta(days=14)
    quiet = (
        await c.post(
            f"{T}/applications",
            json={
                "title": "Data Analyst",
                "company": "Contoso",
                "stage": "applied",
                "applied_on": applied.isoformat(),
            },
        )
    ).json()
    await c.post(
        f"{T}/applications",
        json={
            "title": "Fresh",
            "company": "X",
            "stage": "applied",
            "applied_on": date.today().isoformat(),
        },
    )
    await c.post(f"{T}/applications", json={"title": "Saved only", "stage": "saved"})
    contact = (
        await c.post(f"{T}/contacts", json={"name": "Sam Ortiz", "email": "sam@contoso.example"})
    ).json()
    await c.put(
        f"{T}/applications/{quiet['id']}/contacts/{contact['id']}", json={"role": "recruiter"}
    )

    listed = (await c.get(f"{T}/follow-ups")).json()
    assert [i["title"] for i in listed] == ["Data Analyst"]
    assert listed[0]["days_quiet"] >= 13 and listed[0]["contact_name"] == "Sam Ortiz"

    draft = (await c.post(f"{T}/applications/{quiet['id']}/follow-up/draft", json={})).json()
    assert draft["number"] == 1 and draft["to"] == "sam@contoso.example"
    assert draft["body"].startswith("Hi Sam,") and "Data Analyst" in draft["body"]
    assert draft["body"].rstrip().endswith("Jordan Rivera")

    # Logging it resets the quiet clock; two is the most it suggests.
    r = await c.post(
        f"{T}/applications/{quiet['id']}/follow-up/sent", json={"subject": draft["subject"]}
    )
    assert r.status_code == 201 and r.json()["follow_ups_sent"] == 1
    assert (await c.get(f"{T}/follow-ups")).json() == []
    async for db in dbmod.get_db():
        await db.execute(
            update(ApplicationEvent).values(occurred_at=datetime.now(UTC) - timedelta(days=12))
        )
        await db.commit()
    second = (await c.post(f"{T}/applications/{quiet['id']}/follow-up/draft", json={})).json()
    assert second["number"] == 2 and "once more" in second["body"]
    await c.post(f"{T}/applications/{quiet['id']}/follow-up/sent", json={})
    r = await c.post(f"{T}/applications/{quiet['id']}/follow-up/draft", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "follow_up_limit"


async def test_ai_follow_up_uses_only_what_was_sent(
    user_client: httpx.AsyncClient,
    fake_ai: FakeProvider,  # noqa: F811
) -> None:
    c = user_client
    await _vault(c)
    posting = await _posting(c)
    app_id = (await c.post(f"/api/v1/discovery/postings/{posting['id']}/track")).json()[
        "application_id"
    ]
    doc = (
        await c.post(f"{M}/documents", json={"kind": "cover_letter", "posting_id": posting["id"]})
    ).json()
    await c.post(f"{M}/documents/{doc['id']}/attach", json={"application_id": app_id})
    await c.put("/api/v1/ai/settings", json={"provider": "anthropic", "api_key": "sk-ant-x"})
    fake_ai.script = [
        fake_text(
            json.dumps(
                {
                    "subject": "Detection Engineer application",
                    "body": "Hi Litware team,\n\nI cut phishing clicks by 40% and 3x faster.",
                }
            )
        )
    ]
    r = await c.post(f"{T}/applications/{app_id}/follow-up/draft", json={"method": "ai"})
    assert r.status_code == 200, r.text
    out = r.json()
    # 40% is in the letter they received; "3x" isn't.
    assert out["method"] == "ai" and out["warnings"][0].startswith("Mentions 3,")
    prompt = fake_ai.calls[-1]["turns"][0].text
    assert "Cut phishing clicks by 40%" in prompt and "<submitted_materials>" in prompt
