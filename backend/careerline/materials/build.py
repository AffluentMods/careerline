# SPDX-License-Identifier: Apache-2.0
"""Building resumes and cover letters from verified facts, and fact-checking them.

Structure (titles, employers, dates, schools, certifications) always comes straight from
the vault. Only bullet and paragraph wording may come from a model, and every one of
those is checked:

* it must cite at least one usable fact (unknown or unverified ids are rejected);
* every number in it must appear in the cited facts' text;
* uncited paragraphs in a cover letter may not make claims about the job seeker.

Anything that fails is ``flagged`` and blocks export until the user approves or edits it.
"""

from __future__ import annotations

import difflib
import math
import re
import secrets
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from careerline.ai.facts import Fact, FactSet, date_range, format_partial
from careerline.ai.scoring import tokens

PROFILE_FACT = "profile"
_NUMBER = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s?%?[kKmM]?")
_CLAIM = re.compile(
    r"\b(I|I've|I'm|my)\b.{0,40}\b(led|built|managed|created|reduced|increased|improved|delivered|"
    r"developed|designed|ran|handled|supported|wrote|implemented|launched|years?|experience|certified)\b",
    re.I,
)

LINKED, FLAGGED, APPROVED = "linked", "flagged", "approved"


@dataclass
class Target:
    title: str
    company: str
    description: str

    def tokens(self) -> set[str]:
        return tokens(f"{self.title} {self.description}")


def new_id() -> str:
    return secrets.token_hex(4)


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[\s,$]", "", n).lower() for n in _NUMBER.findall(text)}


def check(
    text: str, fact_ids: list[str], known: dict[str, Fact], *, allow_uncited: bool = False
) -> tuple[str, str]:
    """Fact-check one bullet or paragraph. Returns (status, reason)."""
    unknown = [i for i in fact_ids if i not in known]
    if unknown:
        return FLAGGED, "Cites something that isn't one of your verified facts."
    cited = [known[i] for i in fact_ids]
    if not cited:
        if allow_uncited and not _CLAIM.search(text):
            return LINKED, ""
        return FLAGGED, "Not linked to any verified fact."
    source_numbers = set().union(*(_numbers(f.text) for f in cited))
    missing = [n for n in _numbers(text) if n not in source_numbers]
    if missing:
        return FLAGGED, f"Mentions {missing[0]}, which isn't in the facts it cites."
    return LINKED, ""


def item(
    text: str, fact_ids: list[str], known: dict[str, Fact], *, allow_uncited: bool = False
) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", text).strip()
    status, reason = check(text, fact_ids, known, allow_uncited=allow_uncited)
    return {"id": new_id(), "text": text, "fact_ids": fact_ids, "status": status, "reason": reason}


def known_facts(factset: FactSet, profile_summary: str) -> dict[str, Fact]:
    known = dict(factset.by_id)
    if profile_summary.strip():
        known[PROFILE_FACT] = Fact(
            id=PROFILE_FACT, kind="profile", text=profile_summary.strip(), parent_id=None
        )
    return known


# --- resumes ---------------------------------------------------------------------------------


def _relevance(fact: Fact, wanted: set[str]) -> float:
    have = tokens(fact.text)
    return len(have & wanted) / math.sqrt(len(have) + 1) if have else 0.0


def _children(facts: list[Fact], parent_id: str) -> list[Fact]:
    return [
        f for f in facts if f.parent_id == parent_id and f.kind in ("achievement", "responsibility")
    ]


def _line(f: Fact) -> str:
    return f.text


def resume_skeleton(
    factset: FactSet, known: dict[str, Fact], profile_summary: str
) -> dict[str, Any]:
    """Everything except experience/project bullets, straight from the vault."""
    facts = factset.facts
    summary = item(profile_summary, [PROFILE_FACT], known) if PROFILE_FACT in known else None
    experience = [
        {
            "fact_id": f.id,
            "title": f.data.get("title", ""),
            "employer": f.data.get("employer", ""),
            "location": f.data.get("location", ""),
            "dates": date_range(f.data),
            "bullets": [],
        }
        for f in facts
        if f.kind == "role"
    ]
    projects = [
        {
            "fact_id": f.id,
            "name": f.data.get("name", ""),
            "role": f.data.get("your_role", ""),
            "dates": date_range(f.data, current="Ongoing"),
            "url": f.data.get("url", ""),
            "bullets": [],
        }
        for f in facts
        if f.kind == "project"
    ]
    education = [
        {"fact_id": f.id, "text": _education_line(f)} for f in facts if f.kind == "education"
    ]
    certifications = [
        {"fact_id": f.id, "text": _cert_line(f)}
        for f in facts
        if f.kind == "certification" and f.data.get("status") != "expired"
    ]
    return {
        "summary": summary,
        "experience": experience,
        "projects": projects,
        "education": education,
        "certifications": certifications,
        "skills": [],
    }


def _education_line(f: Fact) -> str:
    d = f.data
    credential, field_ = d.get("credential", ""), d.get("field", "")
    what = f"{credential} in {field_}" if credential and field_ else credential or field_
    line = ", ".join(p for p in (what, d.get("institution", "")) if p)
    end = format_partial(d.get("end"))
    if d.get("status") == "in_progress":
        return f"{line} (expected {end})" if end else f"{line} (in progress)"
    return f"{line} ({end})" if end else line


def _cert_line(f: Fact) -> str:
    d = f.data
    issuer = f", {d['issuer']}" if d.get("issuer") else ""
    if d.get("status") == "in_progress":
        target = d.get("target_date")
        return (
            f"{d.get('name', '')}{issuer} (in progress{f', expected {target}' if target else ''})"
        )
    year = f" ({str(d['earned_on'])[:4]})" if d.get("earned_on") else ""
    return f"{d.get('name', '')}{issuer}{year}"


def _skill_groups(skills: list[Fact]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for f in skills:
        cat = f.data.get("category") or "Skills"
        g = groups.setdefault(cat, {"category": cat, "names": [], "fact_ids": []})
        g["names"].append(f.data.get("name", ""))
        g["fact_ids"].append(f.id)
    return list(groups.values())


def build_resume_rules(
    factset: FactSet, profile_summary: str, target: Target | None, max_bullets: int = 5
) -> dict[str, Any]:
    """Master resume (no target): every verified bullet. Tailored: the most relevant ones."""
    known = known_facts(factset, profile_summary)
    content = resume_skeleton(factset, known, profile_summary)
    wanted = target.tokens() if target else set()
    for section in ("experience", "projects"):
        for entry in content[section]:
            kids = _children(factset.facts, entry["fact_id"])
            if target:
                ranked = sorted(
                    kids, key=lambda f: (-_relevance(f, wanted), f.kind != "achievement")
                )
                relevant = [f for f in ranked if _relevance(f, wanted) > 0]
                kids = (relevant or ranked)[: max(2, min(max_bullets, len(relevant) or 2))]
            entry["bullets"] = [item(_line(f), [f.id], known) for f in kids]
    skills = [f for f in factset.facts if f.kind == "skill"]
    if target:
        description = target.description.lower()
        skills.sort(
            key=lambda f: (
                f.data.get("name", "").lower() not in description,
                f.data.get("name", ""),
            )
        )
        content["projects"] = sorted(
            content["projects"], key=lambda p: -_relevance(known[p["fact_id"]], wanted)
        )[:3]
    content["skills"] = _skill_groups(skills)
    return content


class BulletOut(BaseModel):
    text: str = Field(max_length=600)
    fact_ids: list[str]


class RoleOut(BaseModel):
    role_id: str
    bullets: list[BulletOut]


class TailorOut(BaseModel):
    summary: BulletOut
    roles: list[RoleOut]
    skill_ids: list[str]
    project_ids: list[str]


def build_resume_from_ai(factset: FactSet, profile_summary: str, out: TailorOut) -> dict[str, Any]:
    known = known_facts(factset, profile_summary)
    content = resume_skeleton(factset, known, profile_summary)
    if out.summary.text.strip():
        content["summary"] = item(out.summary.text, out.summary.fact_ids, known)
    by_role = {r.role_id: r for r in out.roles}
    for entry in content["experience"]:
        role = by_role.get(entry["fact_id"])
        if role:
            entry["bullets"] = [
                item(b.text, b.fact_ids, known) for b in role.bullets[:8] if b.text.strip()
            ]
    projects = [p for p in content["projects"] if p["fact_id"] in set(out.project_ids)]
    for p in projects:
        p["bullets"] = [
            item(f.text, [f.id], known) for f in _children(factset.facts, p["fact_id"])[:3]
        ]
    content["projects"] = projects
    skill_facts = {f.id: f for f in factset.facts if f.kind == "skill"}
    chosen = [skill_facts[i] for i in dict.fromkeys(out.skill_ids) if i in skill_facts][:20]
    content["skills"] = _skill_groups(chosen or list(skill_facts.values()))
    return content


# --- cover letters ---------------------------------------------------------------------------


def build_letter_rules(factset: FactSet, target: Target) -> dict[str, Any]:
    known = dict(factset.by_id)
    wanted = target.tokens()
    role_name = target.title or "this role"
    company = target.company or "your team"
    roles = [f for f in factset.facts if f.kind == "role"]
    wins = sorted(
        (f for f in factset.facts if f.kind == "achievement"), key=lambda f: -_relevance(f, wanted)
    )[:3]
    skills = [
        f
        for f in factset.facts
        if f.kind == "skill" and f.data.get("name", "").lower() in target.description.lower()
    ][:6]
    paragraphs = [
        item(
            f"I'm writing to apply for the {role_name} position at {company}.",
            [],
            known,
            allow_uncited=True,
        )
    ]
    if roles:
        r = roles[0]
        where = f" at {r.data['employer']}" if r.data.get("employer") else ""
        paragraphs.append(
            item(
                f"In my work as {r.data.get('title', '')}{where}, "
                "I focus on the kind of problems this role describes.",
                [r.id],
                known,
            )
        )
    if wins:
        text = "A few results I'm proud of: " + " ".join(w.text.rstrip(".") + "." for w in wins)
        paragraphs.append(item(text, [w.id for w in wins], known))
    if skills:
        names = ", ".join(s.data.get("name", "") for s in skills)
        paragraphs.append(
            item(f"I work with {names}, which this role calls for.", [s.id for s in skills], known)
        )
    paragraphs.append(
        item(
            "Thank you for considering my application. "
            f"I'd welcome the chance to talk about how I can help {company}.",
            [],
            known,
            allow_uncited=True,
        )
    )
    return {"company": target.company, "role": target.title, "paragraphs": paragraphs}


class ParagraphOut(BaseModel):
    text: str = Field(max_length=2000)
    fact_ids: list[str]


class LetterOut(BaseModel):
    paragraphs: list[ParagraphOut]


def build_letter_from_ai(factset: FactSet, target: Target, out: LetterOut) -> dict[str, Any]:
    known = dict(factset.by_id)
    paragraphs = [
        item(p.text, p.fact_ids, known, allow_uncited=True)
        for p in out.paragraphs[:6]
        if p.text.strip()
    ]
    return {"company": target.company, "role": target.title, "paragraphs": paragraphs}


# --- review helpers --------------------------------------------------------------------------


def all_items(kind: str, content: dict[str, Any]) -> list[dict[str, Any]]:
    if kind == "cover_letter":
        return list(content.get("paragraphs", []))
    items = [content["summary"]] if content.get("summary") else []
    for section in ("experience", "projects"):
        for entry in content.get(section, []):
            items.extend(entry.get("bullets", []))
    return items


def flagged_count(kind: str, content: dict[str, Any]) -> int:
    return sum(1 for i in all_items(kind, content) if i.get("status") == FLAGGED)


def plain_lines(kind: str, content: dict[str, Any]) -> list[str]:
    """A plain-text rendering, used for the diff against the master resume."""
    if kind == "cover_letter":
        return [p["text"] for p in content.get("paragraphs", [])]
    lines: list[str] = []
    if content.get("summary"):
        lines += ["SUMMARY", content["summary"]["text"]]
    if content.get("experience"):
        lines.append("EXPERIENCE")
        for e in content["experience"]:
            head = " - ".join(p for p in (e["title"], e["employer"]) if p)
            lines.append(f"{head} ({e['dates']})" if e.get("dates") else head)
            lines += [f"- {b['text']}" for b in e["bullets"]]
    if content.get("projects"):
        lines.append("PROJECTS")
        for p in content["projects"]:
            lines.append(p["name"])
            lines += [f"- {b['text']}" for b in p["bullets"]]
    for section, label in (("education", "EDUCATION"), ("certifications", "CERTIFICATIONS")):
        if content.get(section):
            lines.append(label)
            lines += [e["text"] for e in content[section]]
    if content.get("skills"):
        lines.append("SKILLS")
        lines += [f"{g['category']}: {', '.join(g['names'])}" for g in content["skills"]]
    return lines


def diff_lines(before: list[str], after: list[str]) -> list[dict[str, str]]:
    ops: list[dict[str, str]] = []
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops += [{"op": "same", "text": t} for t in before[i1:i2]]
        else:
            ops += [{"op": "removed", "text": t} for t in before[i1:i2]]
            ops += [{"op": "added", "text": t} for t in after[j1:j2]]
    return ops
