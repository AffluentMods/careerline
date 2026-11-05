# SPDX-License-Identifier: Apache-2.0
"""Fit scoring: a free, explainable keyword scorer, and an AI scorer when configured.

Both use only usable (verified) vault facts. AI output is validated: scores are
clamped, cited fact ids that aren't the user's usable facts are dropped, and the
posting text goes in as untrusted data with no tools available.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from careerline.ai.facts import Fact, FactSet, load_facts
from careerline.ai.models import JobScore
from careerline.ai.prompting import untrusted
from careerline.ai.providers import AiError
from careerline.ai.service import AiClient, model_for, structured
from careerline.discovery.models import JobFilters, JobPosting
from careerline.discovery.schemas import ScoreOut
from careerline.discovery.service import evaluate

# --- keyword scorer ------------------------------------------------------------------------

_STOP = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "both",
        "but",
        "by",
        "can",
        "could",
        "do",
        "does",
        "each",
        "etc",
        "for",
        "from",
        "has",
        "have",
        "having",
        "how",
        "if",
        "in",
        "including",
        "into",
        "is",
        "it",
        "its",
        "job",
        "may",
        "more",
        "most",
        "must",
        "not",
        "of",
        "on",
        "or",
        "other",
        "our",
        "out",
        "over",
        "per",
        "plus",
        "role",
        "should",
        "so",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "under",
        "up",
        "us",
        "use",
        "using",
        "via",
        "we",
        "well",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "will",
        "with",
        "within",
        "work",
        "working",
        "would",
        "you",
        "your",
        "yourself",
        "team",
        "teams",
        "ability",
        "able",
        "strong",
        "excellent",
        "good",
        "great",
        "knowledge",
        "understanding",
        "experience",
        "experienced",
        "years",
        "year",
        "skills",
        "skill",
        "required",
        "requirements",
        "preferred",
        "qualifications",
        "qualification",
        "responsibilities",
        "including",
        "familiarity",
        "familiar",
        "proficiency",
        "proficient",
        "demonstrated",
        "proven",
        "solid",
        "plus",
        "bonus",
        "nice",
        "ideal",
        "ideally",
        "candidate",
        "candidates",
        "position",
        "company",
        "opportunity",
        "looking",
        "seeking",
        "new",
        "across",
        "help",
        "support",
        "related",
        "relevant",
        "least",
        "minimum",
        "equivalent",
        "degree",
    ]
)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#./-]*[a-z0-9+#]|[a-z0-9]")
_SECTION = re.compile(
    r"^(requirements|qualifications|minimum qualifications|basic qualifications|"
    r"preferred qualifications|"
    r"what you('ll)? (need|bring)|what we('re)? looking for|you have|you bring|must[- ]haves?|"
    r"skills|required skills|nice to have|bonus points|about you|who you are)\b",
    re.I,
)
_OTHER_SECTION = re.compile(
    r"^(benefits|perks|about us|about the company|what we offer|compensation|pay|salary|"
    r"equal opportunity|eeo)\b",
    re.I,
)
_BULLET = re.compile(r"^\s*(?:[\u2022\u25cf\u25aa*\-\u2013]|\d+[.)])\s+")
_SOFT = re.compile(r"\b(preferred|nice to have|bonus|a plus|desirable|ideally)\b", re.I)
_YEARS = re.compile(r"(\d{1,2})\+?\s*(?:or more\s+)?years?", re.I)
_RED_FLAGS = [
    (
        re.compile(
            r"\b(commission[- ]only|unpaid|pay (a|the)? ?fee|training fee|starter kit)\b", re.I
        ),
        "Mentions unpaid work or fees, a common scam signal.",
    ),
    (
        re.compile(
            r"\b(wire transfer|gift cards?|cashier'?s check|crypto(currency)? payment)\b", re.I
        ),
        "Mentions payment methods often used in scams.",
    ),
    (
        re.compile(
            r"\b(ignore (all |any )?(previous|prior|above) instructions|system prompt|"
            r"you are an ai|as an ai)\b",
            re.I,
        ),
        "Contains text aimed at AI tools.",
    ),
]


def tokens(text: str) -> set[str]:
    return {
        t
        for t in _TOKEN.findall(text.lower())
        if t not in _STOP and (len(t) > 2 or any(c.isdigit() for c in t))
    }


def extract_requirements(description: str, limit: int = 25) -> list[str]:
    lines = [ln.strip() for ln in description.splitlines() if ln.strip()]
    found: list[str] = []
    in_section = False
    for line in lines:
        plain = _BULLET.sub("", line).strip()
        is_heading = (
            len(plain) < 60
            and not _BULLET.match(line)
            and (plain.endswith(":") or len(plain.split()) <= 6)
        )
        if is_heading and _SECTION.match(plain.rstrip(":")):
            in_section = True
            continue
        if is_heading and _OTHER_SECTION.match(plain.rstrip(":")):
            in_section = False
            continue
        if in_section and (_BULLET.match(line) or len(plain) > 25):
            found.append(plain)
    if not found:
        found = [_BULLET.sub("", ln).strip() for ln in lines if _BULLET.match(ln)]
    if not found:
        sentences = re.split(r"(?<=[.;])\s+", description)
        found = [
            s.strip()
            for s in sentences
            if re.search(
                r"\b(experience|knowledge|proficien|familiar|degree|certif|ability to)\b", s, re.I
            )
        ]
    return [r[:300] for r in dict.fromkeys(found) if len(r) > 3][:limit]


def _year_month(value: str, default_month: int) -> tuple[int, int]:
    year, _, month = value.partition("-")
    return int(year), int(month) if month else default_month


def _years_of_experience(facts: list[Fact], today: date) -> float:
    """Total months across verified roles (overlaps counted twice; this is a rough check)."""
    months = 0
    for f in facts:
        start, end = f.data.get("start"), f.data.get("end")
        if f.kind != "role" or not isinstance(start, str):
            continue
        sy, sm = _year_month(start, 1)
        ey, em = _year_month(end, 12) if isinstance(end, str) else (today.year, today.month)
        months += max(0, (ey - sy) * 12 + em - sm + 1)
    return months / 12


@dataclass
class ScoreResult:
    score: int
    method: str
    model: str
    summary: str
    matched: list[dict[str, Any]]
    gaps: list[str]
    red_flags: list[str]


def score_with_rules(
    posting: JobPosting, factset: FactSet, filter_reasons: list[str]
) -> ScoreResult:
    facts = factset.facts
    fact_tokens = [(f, tokens(f.text)) for f in facts]
    vault = set().union(*(t for _, t in fact_tokens)) if fact_tokens else set()
    skills = {f.data.get("name", "").lower() for f in facts if f.kind == "skill"}
    requirements = extract_requirements(posting.description)
    matched: list[dict[str, Any]] = []
    gaps: list[str] = []
    earned = possible = 0.0
    for req in requirements:
        req_tokens = tokens(req)
        key = {
            t for t in req_tokens if len(t) >= 4 or any(c.isdigit() or c in "+#" for c in t)
        } or req_tokens
        skill_hit = any(
            s and re.search(rf"(?<![a-z0-9]){re.escape(s)}(?![a-z0-9])", req.lower())
            for s in skills
        )
        hits = key & vault
        weight = 0.5 if _SOFT.search(req) else 1.0
        possible += weight
        if skill_hit or (key and len(hits) >= max(1, math.ceil(len(key) * 0.4))):
            earned += weight
            evidence = sorted(fact_tokens, key=lambda ft: -len(ft[1] & (hits or key)))[:2]
            matched.append(
                {
                    "requirement": req,
                    "fact_ids": [f.id for f, t in evidence if t & (hits or key)],
                }
            )
        else:
            gaps.append(req)

    if possible:
        score = round(100 * earned / possible)
    else:
        # No requirement list found: compare the posting's vocabulary with the user's skills.
        posting_tokens = tokens(f"{posting.title} {posting.description}")
        mentioned = {s for s in skills if s and s in posting.description.lower()}
        overlap = len(posting_tokens & vault)
        score = min(100, 30 + 10 * len(mentioned) + overlap) if (mentioned or overlap) else 20

    titles = [tokens(f.data.get("title", "")) for f in facts if f.kind == "role"]
    wanted = tokens(posting.title)
    if wanted and any(len(wanted & t) / len(wanted) >= 0.5 for t in titles):
        score = min(100, score + 5)

    m = _YEARS.search(posting.description)
    if m and facts:
        asked = int(m.group(1))
        have = _years_of_experience(facts, datetime.now(UTC).date())
        if 0 < asked <= 30 and have + 0.5 < asked:
            gaps.insert(
                0, f"Asks for {asked}+ years; your verified roles add up to about {round(have)}."
            )
            score = max(0, score - 10)

    red_flags = list(filter_reasons)
    blob = f"{posting.title}\n{posting.description}"
    red_flags.extend(msg for pattern, msg in _RED_FLAGS if pattern.search(blob))
    if not facts:
        summary = "Verify some vault facts to get a meaningful score."
        score = min(score, 20)
    elif requirements:
        summary = (
            f"Your verified facts cover {len(matched)} of the {len(requirements)} "
            "requirements found in the posting."
        )
    else:
        summary = (
            "No clear requirement list in the posting; scored on overlapping skills and keywords."
        )
    return ScoreResult(
        score=max(0, min(100, score)),
        method="rules",
        model="",
        summary=summary,
        matched=matched,
        gaps=gaps[:12],
        red_flags=red_flags[:8],
    )


# --- AI scorer -------------------------------------------------------------------------------


class MatchedOut(BaseModel):
    requirement: str = Field(max_length=400)
    fact_ids: list[str]


class FitScoreOut(BaseModel):
    score: int
    summary: str = Field(max_length=600)
    matched: list[MatchedOut]
    gaps: list[str]
    red_flags: list[str]


def posting_block(p: JobPosting) -> str:
    pay = ""
    if p.pay_min_cents is not None or p.pay_max_cents is not None:
        lo, hi = p.pay_min_cents or p.pay_max_cents or 0, p.pay_max_cents or p.pay_min_cents or 0
        pay = f"{p.currency} {lo / 100:,.2f}-{hi / 100:,.2f} per {p.pay_period or 'year'}"
    header = "\n".join(
        f"{k}: {v}"
        for k, v in (
            ("Title", p.title),
            ("Company", p.company),
            ("Location", p.location),
            ("Work mode", p.work_mode or ""),
            ("Pay", pay),
        )
        if v
    )
    return untrusted("job_posting", f"{header}\n\n{p.description}")


async def score_with_ai(ai: AiClient, posting: JobPosting, factset: FactSet) -> ScoreResult:
    out = await structured(
        ai,
        feature="score",
        prompt_name="fit_score",
        values={"facts": factset.prompt_json(), "posting": posting_block(posting)},
        schema_model=FitScoreOut,
        max_tokens=3000,
        effort="low",
        subject={"posting_id": str(posting.id)},
    )
    known = factset.by_id
    matched = [
        {
            "requirement": m.requirement.strip()[:400],
            "fact_ids": [i for i in m.fact_ids if i in known],
        }
        for m in out.matched[:25]
        if m.requirement.strip()
    ]
    return ScoreResult(
        score=max(0, min(100, out.score)),
        method="ai",
        model=ai.settings.model_scoring or "",
        summary=out.summary.strip()[:500],
        matched=matched,
        gaps=[g.strip()[:300] for g in out.gaps[:12] if g.strip()],
        red_flags=[r.strip()[:300] for r in out.red_flags[:8] if r.strip()],
    )


# --- storage -------------------------------------------------------------------------------


async def save_score(
    db: AsyncSession,
    user_id: uuid.UUID,
    posting_id: uuid.UUID,
    result: ScoreResult,
    vault_hash: str,
    model: str = "",
) -> None:
    values = {
        "score": result.score,
        "method": result.method,
        "model": model or result.model,
        "summary": result.summary[:500],
        "detail": {"matched": result.matched, "gaps": result.gaps, "red_flags": result.red_flags},
        "vault_hash": vault_hash,
        "created_at": datetime.now(UTC),
    }
    stmt = insert(JobScore).values(
        id=uuid.uuid4(), user_id=user_id, posting_id=posting_id, **values
    )
    await db.execute(stmt.on_conflict_do_update(constraint="uq_job_scores_posting", set_=values))


async def scores_for(
    db: AsyncSession, user_id: uuid.UUID, posting_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ScoreOut]:
    if not posting_ids:
        return {}
    rows = list(
        await db.scalars(
            select(JobScore).where(
                JobScore.user_id == user_id, JobScore.posting_id.in_(posting_ids)
            )
        )
    )
    if not rows:
        return {}
    current = (await load_facts(db, user_id)).digest()
    return {
        r.posting_id: ScoreOut(
            score=r.score, method=r.method, summary=r.summary, stale=r.vault_hash != current
        )
        for r in rows
    }


async def score_detail(
    db: AsyncSession, user_id: uuid.UUID, posting_id: uuid.UUID
) -> dict[str, Any] | None:
    row = await db.scalar(
        select(JobScore).where(JobScore.user_id == user_id, JobScore.posting_id == posting_id)
    )
    if row is None:
        return None
    factset = await load_facts(db, user_id)
    known = factset.by_id
    detail = row.detail or {}
    return {
        "score": row.score,
        "method": row.method,
        "model": row.model,
        "summary": row.summary,
        "matched": [
            {
                "requirement": m.get("requirement", ""),
                "evidence": [known[i].text for i in m.get("fact_ids", []) if i in known],
            }
            for m in detail.get("matched", [])
        ],
        "gaps": detail.get("gaps", []),
        "red_flags": detail.get("red_flags", []),
        "stale": row.vault_hash != factset.digest(),
        "scored_at": row.created_at.isoformat(),
    }


async def score_posting(
    db: AsyncSession,
    user_id: uuid.UUID,
    posting: JobPosting,
    *,
    ai: AiClient | None,
    factset: FactSet | None = None,
) -> ScoreResult:
    """Score one posting (AI when ``ai`` is given) and store it. Caller commits."""
    factset = factset or await load_facts(db, user_id)
    filters = await db.get(JobFilters, user_id)
    if ai is not None:
        result = await score_with_ai(ai, posting, factset)
        # Hard filters are facts about the posting; keep them visible next to the AI's flags.
        result.red_flags = [*evaluate(posting, filters).reasons, *result.red_flags][:8]
    else:
        result = score_with_rules(posting, factset, evaluate(posting, filters).reasons)
    await save_score(
        db,
        user_id,
        posting.id,
        result,
        factset.digest(),
        model=model_for(ai.settings, "score") if ai else "",
    )
    return result


async def postings_to_score(
    db: AsyncSession, user_id: uuid.UUID, digest: str, limit: int = 200
) -> tuple[list[JobPosting], set[uuid.UUID]]:
    """Unscored postings and stale keyword scores (stale AI scores wait for the user)."""
    postings = list(
        await db.scalars(
            select(JobPosting)
            .where(JobPosting.user_id == user_id, JobPosting.status != "dismissed")
            .order_by(JobPosting.first_seen_at.desc())
            .limit(2000)
        )
    )
    existing = {
        r.posting_id: r
        for r in await db.scalars(select(JobScore).where(JobScore.user_id == user_id))
    }
    out = [
        p
        for p in postings
        if p.id not in existing
        or (existing[p.id].method == "rules" and existing[p.id].vault_hash != digest)
    ]
    never_scored = {p.id for p in out if p.id not in existing}
    return out[:limit], never_scored


def ai_unavailable(exc: AiError) -> bool:
    """Errors that mean "stop using AI for this batch" rather than "skip this posting"."""
    return exc.code in {
        "not_configured",
        "no_key",
        "no_model",
        "auth_failed",
        "budget_reached",
        "rate_limited",
        "forbidden",
        "model_not_found",
        "unreachable",
        "no_base_url",
    }
