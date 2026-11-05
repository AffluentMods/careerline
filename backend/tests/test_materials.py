# SPDX-License-Identifier: Apache-2.0
"""Materials: fact-checking, building, exporting, and the API. All people are fictional."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import docx
import httpx
import pytest
from pypdf import PdfReader
from sqlalchemy import select

from waypoint import db as dbmod
from waypoint.ai import service as ai_service
from waypoint.ai.facts import Fact, FactSet, fact_text
from waypoint.ai.models import AiSettings
from waypoint.ai.providers import FakeProvider, fake_text
from waypoint.materials import build, export
from waypoint.models import AuditLog

M = "/api/v1/materials"
V = "/api/v1/vault"


def _fact(i: str, kind: str, data: dict[str, Any], parent: str | None = None) -> Fact:
    return Fact(id=i, kind=kind, text=fact_text(kind, data), parent_id=parent, data=data)


FACTS = FactSet(
    facts=[
        _fact(
            "r1",
            "role",
            {
                "title": "Security Analyst",
                "employer": "Northwind Tools",
                "location": "Springfield, ST",
                "start": "2021-03",
                "end": None,
            },
        ),
        _fact(
            "a1",
            "achievement",
            {"text": "Cut phishing clicks by 40% with monthly simulations"},
            "r1",
        ),
        _fact(
            "a2", "achievement", {"text": "Wrote Python automation for Splunk alert triage"}, "r1"
        ),
        _fact("a3", "responsibility", {"text": "Ran the badge access review each quarter"}, "r1"),
        _fact(
            "r2",
            "role",
            {
                "title": "Help Desk Technician",
                "employer": "Contoso Ltd",
                "start": "2018-06",
                "end": "2020-12",
            },
        ),
        _fact("a4", "achievement", {"text": "Supported 300 users across three sites"}, "r2"),
        _fact("s1", "skill", {"name": "Python", "category": "Languages"}),
        _fact("s2", "skill", {"name": "Splunk", "category": "Tools"}),
        _fact("s3", "skill", {"name": "Excel", "category": "Tools"}),
        _fact(
            "e1",
            "education",
            {
                "institution": "Example State University",
                "credential": "BS",
                "field": "Information Systems",
                "status": "completed",
                "end": "2018-05",
            },
        ),
        _fact(
            "c1",
            "certification",
            {
                "name": "Example Security+",
                "issuer": "Example Cert Body",
                "status": "earned",
                "earned_on": "2021-08",
            },
        ),
        _fact(
            "c2",
            "certification",
            {"name": "Example Cloud Cert", "status": "in_progress", "target_date": "2027"},
        ),
    ],
    stories=[],
)
TARGET = build.Target(
    "Detection Engineer",
    "Litware",
    "We need Python and Splunk detection engineering. Phishing experience helps.",
)


# --- fact-check ------------------------------------------------------------------------------


def test_fact_check_rules() -> None:
    known = FACTS.by_id
    assert build.check("Cut phishing clicks 40% via simulations", ["a1"], known) == (
        build.LINKED,
        "",
    )
    assert build.check("Cut phishing clicks 55%", ["a1"], known)[0] == build.FLAGGED
    assert "55%" in build.check("Cut phishing clicks 55%", ["a1"], known)[1]
    assert build.check("Led a team of 12", ["a1"], known)[0] == build.FLAGGED
    assert build.check("Anything", ["nope"], known)[1].startswith("Cites something")
    assert build.check("Anything", [], known)[1] == "Not linked to any verified fact."
    # Uncited letter paragraphs may be pleasantries, but not claims about the job seeker.
    assert (
        build.check("I'm excited about Litware's mission.", [], known, allow_uncited=True)[0]
        == build.LINKED
    )
    assert (
        build.check("I have led many security programs.", [], known, allow_uncited=True)[0]
        == build.FLAGGED
    )
    assert build.check("Supported 300 users", ["a4"], known)[0] == build.LINKED
    assert build.check("Supported 3,000 users", ["a4"], known)[0] == build.FLAGGED


def test_master_and_tailored_resumes() -> None:
    master = build.build_resume_rules(FACTS, "Blue team generalist.", None)
    assert [e["title"] for e in master["experience"]] == [
        "Security Analyst",
        "Help Desk Technician",
    ]
    assert len(master["experience"][0]["bullets"]) == 3
    assert (
        master["summary"]["fact_ids"] == [build.PROFILE_FACT]
        and master["summary"]["status"] == build.LINKED
    )
    assert master["education"] == [
        {"fact_id": "e1", "text": "BS in Information Systems, Example State University (May 2018)"}
    ]
    assert [c["text"] for c in master["certifications"]] == [
        "Example Security+, Example Cert Body (2021)",
        "Example Cloud Cert (in progress, expected 2027)",
    ]
    assert build.flagged_count("resume", master) == 0

    tailored = build.build_resume_rules(FACTS, "", TARGET)
    first = [b["text"] for b in tailored["experience"][0]["bullets"]]
    assert first[0] in (
        "Wrote Python automation for Splunk alert triage",
        "Cut phishing clicks by 40% with monthly simulations",
    )
    assert "Ran the badge access review each quarter" not in first
    skills = tailored["skills"]
    assert skills[0]["names"][0] in ("Python", "Splunk") and tailored["summary"] is None
    ops = build.diff_lines(
        build.plain_lines("resume", master), build.plain_lines("resume", tailored)
    )
    assert any(o["op"] == "removed" and "badge access" in o["text"] for o in ops)


def test_ai_resume_keeps_structure_from_the_vault() -> None:
    out = build.TailorOut(
        summary=build.BulletOut(
            text="Security analyst who automates detection in Python.", fact_ids=["a2", "r1"]
        ),
        roles=[
            build.RoleOut(
                role_id="r1",
                bullets=[
                    build.BulletOut(
                        text="Automated Splunk alert triage with Python", fact_ids=["a2"]
                    ),
                    build.BulletOut(
                        text="Reduced phishing clicks 70%", fact_ids=["a1"]
                    ),  # invented number
                    build.BulletOut(text="Managed a SOC of 15 analysts", fact_ids=[]),  # uncited
                ],
            ),
            build.RoleOut(
                role_id="fake-role",
                bullets=[build.BulletOut(text="CISO at Initech", fact_ids=["a1"])],
            ),
        ],
        skill_ids=["s2", "s1", "unknown"],
        project_ids=[],
    )
    content = build.build_resume_from_ai(FACTS, "", out)
    assert [e["fact_id"] for e in content["experience"]] == [
        "r1",
        "r2",
    ]  # roles come from the vault
    assert content["experience"][0]["title"] == "Security Analyst"
    statuses = [b["status"] for b in content["experience"][0]["bullets"]]
    assert statuses == [build.LINKED, build.FLAGGED, build.FLAGGED]
    assert content["experience"][1]["bullets"] == []
    assert "CISO" not in json.dumps(content)
    assert content["skills"][0]["names"] == ["Splunk"] and content["skills"][1]["names"] == [
        "Python"
    ]
    assert build.flagged_count("resume", content) == 2


def test_rule_letter_cites_facts() -> None:
    letter = build.build_letter_rules(FACTS, TARGET)
    texts = [p["text"] for p in letter["paragraphs"]]
    assert texts[0] == "I'm writing to apply for the Detection Engineer position at Litware."
    assert all(p["status"] == build.LINKED for p in letter["paragraphs"])
    assert any("Python, Splunk" in t for t in texts)


# --- export ------------------------------------------------------------------------------------


HEADER = export.Header(
    name="Jordan Rivera",
    headline="Security Analyst",
    contact=["jordan@example.com", "Springfield, ST"],
)


def test_docx_and_pdf_exports() -> None:
    content = build.build_resume_rules(FACTS, "Blue team generalist.", None)
    content["experience"][0]["bullets"][0]["text"] = 'Wrote <b>bold</b> & "quoted" text <para>'
    d = docx.Document(io.BytesIO(export.resume_docx(content, HEADER)))
    text = "\n".join(p.text for p in d.paragraphs)
    assert (
        "Jordan Rivera" in text
        and "EXPERIENCE" in text
        and "Supported 300 users across three sites" in text
    )
    assert 'Wrote <b>bold</b> & "quoted" text <para>' in text

    pdf = PdfReader(io.BytesIO(export.resume_pdf(content, HEADER)))
    pdf_text = "".join(page.extract_text() for page in pdf.pages)
    assert "Jordan Rivera" in pdf_text and "Security Analyst" in pdf_text
    assert "<b>bold</b>" in pdf_text  # markup is escaped, shown literally
    assert pdf.metadata.title == "Jordan Rivera - Resume"

    letter = build.build_letter_rules(FACTS, TARGET)
    from datetime import date

    ltext = "".join(
        p.extract_text()
        for p in PdfReader(io.BytesIO(export.letter_pdf(letter, HEADER, date(2026, 11, 4)))).pages
    )
    assert "November 4, 2026" in ltext and "Dear Litware team," in ltext and "Sincerely," in ltext
    ldoc = docx.Document(io.BytesIO(export.letter_docx(letter, HEADER, date(2026, 11, 4))))
    assert ldoc.paragraphs[-1].text == "Jordan Rivera"
    assert (
        export.filename(HEADER, "resume", "Litware/Inc", "pdf")
        == "Jordan Rivera - Litware_Inc - Resume.pdf"
    )


# --- API ---------------------------------------------------------------------------------------


@pytest.fixture
def fake_ai() -> Iterator[FakeProvider]:
    fake = FakeProvider()
    ai_service.set_provider_builder(lambda s, k, p: fake)  # type: ignore[arg-type,return-value]
    yield fake
    ai_service.set_provider_builder(None)


async def _vault(c: httpx.AsyncClient) -> dict[str, str]:
    await c.put(
        f"{V}/profile",
        json={
            "full_name": "Jordan Rivera",
            "headline": "Security Analyst",
            "summary": "Blue team generalist.",
            "email": "jordan@example.com",
        },
    )
    role = (
        await c.post(
            f"{V}/entries",
            json={
                "kind": "role",
                "data": {
                    "title": "Security Analyst",
                    "employer": "Northwind Tools",
                    "start": "2021-03",
                },
            },
        )
    ).json()
    ach = (
        await c.post(
            f"{V}/entries",
            json={
                "kind": "achievement",
                "parent_id": role["id"],
                "data": {"text": "Cut phishing clicks by 40%"},
            },
        )
    ).json()
    skill = (
        await c.post(f"{V}/entries", json={"kind": "skill", "data": {"name": "Python"}})
    ).json()
    await c.post(
        f"{V}/entries",
        json={"kind": "link", "data": {"label": "Site", "url": "https://jordan.example.com"}},
    )
    return {"role": role["id"], "ach": ach["id"], "skill": skill["id"]}


async def _posting(c: httpx.AsyncClient) -> Any:
    r = await c.post(
        "/api/v1/discovery/import",
        json={
            "title": "Detection Engineer",
            "company": "Litware",
            "text": "Requirements:\n- Python\n- Phishing defense",
        },
    )
    return r.json()


async def test_generate_review_export_attach(
    user_client: httpx.AsyncClient, fake_ai: FakeProvider
) -> None:
    c = user_client
    ids = await _vault(c)
    posting = await _posting(c)

    master = (await c.post(f"{M}/documents", json={"kind": "resume"})).json()
    assert (
        master["is_master"] is True
        and master["title"] == "Master resume"
        and master["flagged"] == 0
    )
    assert master["sources"][ids["ach"]] == "Cut phishing clicks by 40%"

    await c.put("/api/v1/ai/settings", json={"provider": "anthropic", "api_key": "sk-ant-x"})
    reply = {
        "summary": {"text": "Analyst who defends against phishing.", "fact_ids": [ids["ach"]]},
        "roles": [
            {
                "role_id": ids["role"],
                "bullets": [
                    {"text": "Cut phishing clicks by 40%", "fact_ids": [ids["ach"]]},
                    {"text": "Cut phishing clicks by 90% in a week", "fact_ids": [ids["ach"]]},
                ],
            }
        ],
        "skill_ids": [ids["skill"]],
        "project_ids": [],
    }
    fake_ai.script = [fake_text(json.dumps(reply))]
    r = await c.post(
        f"{M}/documents", json={"kind": "resume", "method": "ai", "posting_id": posting["id"]}
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert (
        doc["title"] == "Resume for Detection Engineer at Litware"
        and doc["method"] == "ai"
        and doc["flagged"] == 1
    )
    assert doc["model"] == "claude-opus-5-5"
    sent = fake_ai.calls[0]
    assert (
        '<untrusted_document kind="job_posting">' in sent["turns"][0].text
        and sent.get("tools") is None
    )

    flagged = next(
        b for b in doc["content"]["experience"][0]["bullets"] if b["status"] == "flagged"
    )
    r = await c.get(f"{M}/documents/{doc['id']}/export", params={"format": "pdf"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "unverified_content"

    # Editing to text the facts support makes it linked again.
    r = await c.patch(
        f"{M}/documents/{doc['id']}/items/{flagged['id']}",
        json={"action": "edit", "text": "Cut phishing clicks by 40% using simulations"},
    )
    edited = next(
        b for b in r.json()["content"]["experience"][0]["bullets"] if b["id"] == flagged["id"]
    )
    assert edited["status"] == "linked" and r.json()["flagged"] == 0

    r = await c.get(f"{M}/documents/{doc['id']}/export", params={"format": "pdf"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert 'filename="Jordan Rivera - Litware - Resume.pdf"' in r.headers["content-disposition"]
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    assert (
        "jordan@example.com" in text
        and "jordan.example.com" in text
        and "Cut phishing clicks by 40% using simulations" in text
    )
    r = await c.get(f"{M}/documents/{doc['id']}/export", params={"format": "docx"})
    assert r.status_code == 200 and r.content[:2] == b"PK"

    diff = (await c.get(f"{M}/documents/{doc['id']}/diff")).json()
    assert diff["added"] >= 1

    # Attach to the tracker: needs a tracked application.
    r = await c.post(f"{M}/documents/{doc['id']}/attach", json={"format": "pdf"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "application_required"
    app_id = (await c.post(f"/api/v1/discovery/postings/{posting['id']}/track")).json()[
        "application_id"
    ]
    r = await c.post(
        f"{M}/documents/{doc['id']}/attach", json={"format": "pdf", "application_id": app_id}
    )
    assert r.status_code == 201
    detail = (await c.get(f"/api/v1/tracker/applications/{app_id}")).json()
    assert [a["filename"] for a in detail["attachments"]] == [
        "Jordan Rivera - Litware - Resume.pdf"
    ]

    listed = (await c.get(f"{M}/documents", params={"posting_id": posting["id"]})).json()
    assert [d["id"] for d in listed] == [doc["id"]]
    async for s in dbmod.get_db():
        events = [
            a.event
            for a in await s.scalars(select(AuditLog).where(AuditLog.event.like("document.%")))
        ]
    assert events.count("document.generated") == 2 and events.count("document.exported") == 3


async def test_approve_remove_and_letters(
    user_client: httpx.AsyncClient, fake_ai: FakeProvider
) -> None:
    c = user_client
    ids = await _vault(c)
    posting = await _posting(c)
    await c.put("/api/v1/ai/settings", json={"provider": "anthropic", "api_key": "sk-ant-x"})
    fake_ai.script = [
        fake_text(
            json.dumps(
                {
                    "paragraphs": [
                        {"text": "I'm excited to apply to Litware.", "fact_ids": []},
                        {"text": "I cut phishing clicks by 40%.", "fact_ids": [ids["ach"]]},
                        {"text": "I have managed large teams for years.", "fact_ids": []},
                    ]
                }
            )
        )
    ]
    doc = (
        await c.post(
            f"{M}/documents",
            json={"kind": "cover_letter", "method": "ai", "posting_id": posting["id"]},
        )
    ).json()
    assert [p["status"] for p in doc["content"]["paragraphs"]] == ["linked", "linked", "flagged"]
    bad = doc["content"]["paragraphs"][2]
    r = await c.patch(f"{M}/documents/{doc['id']}/items/{bad['id']}", json={"action": "approve"})
    assert r.json()["flagged"] == 0 and r.json()["content"]["paragraphs"][2]["status"] == "approved"
    r = await c.patch(f"{M}/documents/{doc['id']}/items/{bad['id']}", json={"action": "remove"})
    assert len(r.json()["content"]["paragraphs"]) == 2
    assert (
        await c.patch(f"{M}/documents/{doc['id']}/items/nope", json={"action": "approve"})
    ).status_code == 404
    assert (await c.get(f"{M}/documents/{doc['id']}/diff")).status_code == 422

    r = await c.post(f"{M}/documents", json={"kind": "cover_letter"})
    assert r.status_code == 422  # a letter needs a target
    rules = (
        await c.post(f"{M}/documents", json={"kind": "cover_letter", "posting_id": posting["id"]})
    ).json()
    assert rules["method"] == "rules" and rules["flagged"] == 0
    r = await c.post(f"{M}/documents", json={"kind": "resume", "method": "ai"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "target_required"


async def test_empty_vault_and_isolation(
    two_users: tuple[httpx.AsyncClient, httpx.AsyncClient],
) -> None:
    a, b = two_users
    assert (await b.post(f"{M}/documents", json={"kind": "resume"})).json()["error"][
        "code"
    ] == "empty_vault"
    await _vault(a)
    posting = await _posting(a)
    doc = (
        await a.post(f"{M}/documents", json={"kind": "resume", "posting_id": posting["id"]})
    ).json()
    await _vault(b)
    for coro in (
        b.get(f"{M}/documents/{doc['id']}"),
        b.patch(f"{M}/documents/{doc['id']}", json={"title": "x"}),
        b.delete(f"{M}/documents/{doc['id']}"),
        b.get(f"{M}/documents/{doc['id']}/export"),
        b.get(f"{M}/documents/{doc['id']}/diff"),
        b.post(f"{M}/documents/{doc['id']}/attach", json={}),
        b.post(f"{M}/documents", json={"kind": "resume", "posting_id": posting["id"]}),
    ):
        assert (await coro).status_code == 404
    assert (await b.get(f"{M}/documents")).json() == []
    async for s in dbmod.get_db():
        assert await s.scalar(select(AiSettings)) is None
