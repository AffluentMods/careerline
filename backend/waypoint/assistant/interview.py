# SPDX-License-Identifier: Apache-2.0
"""Interview prep: likely questions mapped to STAR stories, and mock interviews."""

from __future__ import annotations

import json
import re
import secrets
from typing import Any, Literal

from pydantic import BaseModel, Field

from waypoint.ai.facts import FactSet
from waypoint.ai.prompting import load_prompt, redact, strict_schema, untrusted
from waypoint.ai.providers import AiError, Turn
from waypoint.ai.scoring import extract_requirements, tokens
from waypoint.ai.service import AiClient, chat_step, structured

Category = Literal["behavioral", "technical", "situational", "company"]

BEHAVIORAL = [
    "Tell me about yourself and what brings you to this role.",
    "Tell me about a time you solved a difficult problem under pressure.",
    "Describe a time you disagreed with a teammate. How did you resolve it?",
    "Tell me about a mistake you made at work and what you learned from it.",
    "Describe the project or result you're most proud of.",
]
SITUATIONAL = [
    "Several urgent requests land at once. How do you decide what to do first?",
    "What would you focus on in your first 90 days in this role?",
]


def _story_matches(text: str, factset: FactSet, limit: int = 2) -> list[str]:
    wanted = tokens(text)
    scored = []
    for s in factset.stories:
        overlap = len(wanted & tokens(f"{s.title} {s.text}"))
        if overlap:
            scored.append((overlap, s.id))
    return [sid for _, sid in sorted(scored, reverse=True)[:limit]]


def _q(question: str, category: str, why: str, story_ids: list[str]) -> dict[str, Any]:
    return {
        "id": secrets.token_hex(4),
        "question": question,
        "category": category,
        "why": why,
        "story_ids": story_ids,
        "notes": "",
    }


def questions_from_rules(
    factset: FactSet, title: str, company: str, description: str
) -> list[dict[str, Any]]:
    posting_text = f"{title} {description}"
    best_overall = _story_matches(posting_text, factset, limit=1)
    out = [
        _q(
            q,
            "behavioral",
            "A common behavioral question.",
            _story_matches(q + " " + posting_text, factset) or best_overall,
        )
        for q in BEHAVIORAL
    ]
    for req in extract_requirements(description, limit=6):
        short = re.sub(r"\s+", " ", req).strip().rstrip(".")
        short = (
            short[0].lower() + short[1:]
            if short[:1].isupper() and not short[:2].isupper()
            else short
        )
        out.append(
            _q(
                f"Walk me through your experience with {short[:140]}.",
                "technical",
                "The posting lists this as a requirement.",
                _story_matches(req, factset),
            )
        )
    out += [
        _q(q, "situational", "Interviewers use this to see how you prioritize.", [])
        for q in SITUATIONAL
    ]
    if company:
        out.append(
            _q(
                f"Why do you want to work at {company}?",
                "company",
                "Almost every interviewer asks this; prepare something specific.",
                [],
            )
        )
    return out


class QuestionOut(BaseModel):
    question: str = Field(max_length=500)
    category: Category
    why: str = Field(max_length=500)
    story_ids: list[str]


class QuestionsOut(BaseModel):
    questions: list[QuestionOut]


async def questions_from_ai(
    ai: AiClient, factset: FactSet, title: str, company: str, description: str
) -> list[dict[str, Any]]:
    out = await structured(
        ai,
        feature="questions",
        prompt_name="interview_questions",
        values={
            "facts": factset.prompt_json(),
            "stories": factset.stories_json(),
            "posting": untrusted(
                "job_posting", f"Title: {title}\nCompany: {company}\n\n{description}"
            ),
        },
        schema_model=QuestionsOut,
        max_tokens=5000,
        effort="low",
    )
    known = {s.id for s in factset.stories}
    return [
        _q(q.question.strip(), q.category, q.why.strip(), [i for i in q.story_ids if i in known])
        for q in out.questions[:20]
        if q.question.strip()
    ]


# --- mock interviews ----------------------------------------------------------------------------


class MockTurnOut(BaseModel):
    feedback: str = Field(max_length=3000)
    strengths: list[str]
    improvements: list[str]
    rating: int
    next_question: str = Field(max_length=1000)
    finished: bool


def _opening(
    factset: FactSet, title: str, company: str, description: str, max_questions: int
) -> tuple[str, str, str, str]:
    template = load_prompt("mock_interview")
    system, user = template.render(
        facts=factset.prompt_json(),
        posting=untrusted("job_posting", f"Title: {title}\nCompany: {company}\n\n{description}"),
        max_questions=str(max_questions),
    )
    return template.name, template.version, system, user


async def mock_step(
    ai: AiClient,
    factset: FactSet,
    session_turns: list[dict[str, Any]],
    *,
    title: str,
    company: str,
    description: str,
    max_questions: int,
) -> dict[str, Any]:
    """Get the interviewer's next move given the session so far. Returns the new turn."""
    name, version, system, opening = _opening(factset, title, company, description, max_questions)
    turns = [Turn(role="user", text=opening)]
    for t in session_turns:
        if t["role"] == "interviewer":
            turns.append(Turn(role="assistant", text=t["raw"]))
        else:
            turns.append(Turn(role="user", text=t["text"]))
    if ai.hosted and ai.settings.redact_pii:
        turns = [Turn(role=t.role, text=redact(t.text)) for t in turns]
    completion = await chat_step(
        ai,
        feature="mock_interview",
        system_prompt=(name, version, system),
        turns=turns,
        json_schema=strict_schema(MockTurnOut),
        max_tokens=2000,
        effort="medium",
    )
    try:
        text = completion.text.strip()
        out = MockTurnOut.model_validate(json.loads(text[text.find("{") : text.rfind("}") + 1]))
    except ValueError:
        raise AiError(
            "invalid_output", "The interviewer's reply didn't match the expected format. Try again."
        ) from None
    answers = sum(1 for t in session_turns if t["role"] == "candidate")
    finished = out.finished or answers >= max_questions or not out.next_question.strip()
    return {
        "role": "interviewer",
        "text": "" if finished else out.next_question.strip(),
        "feedback": out.feedback.strip(),
        "strengths": [s.strip() for s in out.strengths[:5] if s.strip()],
        "improvements": [s.strip() for s in out.improvements[:5] if s.strip()],
        "rating": max(0, min(5, out.rating)),
        "finished": finished,
        "raw": completion.text,
    }
