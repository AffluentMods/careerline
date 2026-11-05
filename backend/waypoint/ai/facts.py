# SPDX-License-Identifier: Apache-2.0
"""The verified facts AI features may use: vault entries that are verified, with every
parent verified too ("usable"), plus verified stories. Nothing else about the user's
experience is ever sent to a model."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint.models import Story, StoryEntry, VaultEntry
from waypoint.vault.service import is_usable, verification_index

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def format_partial(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    year, _, month = value.partition("-")
    return f"{_MONTHS[int(month) - 1]} {year}" if month else year


def date_range(data: dict[str, Any], current: str = "Present") -> str:
    start, end = format_partial(data.get("start")), format_partial(data.get("end"))
    if not start and not end:
        return ""
    return f"{start or '?'} - {end or current}"


def fact_text(kind: str, d: dict[str, Any]) -> str:
    """A one-line, human-readable statement of a fact."""
    if kind == "role":
        where = f" at {d['employer']}" if d.get("employer") else ""
        dates = date_range(d)
        return f"{d.get('title', '')}{where}" + (f" ({dates})" if dates else "")
    if kind in ("responsibility", "achievement"):
        text = str(d.get("text", ""))
        metric = d.get("metric")
        if isinstance(metric, dict) and metric.get("value"):
            unit = f" {metric['unit']}" if metric.get("unit") else ""
            text += f" [metric: {metric['value']}{unit}]"
        return text
    if kind == "project":
        return f"Project: {d.get('name', '')}" + (
            f" — {d['description']}" if d.get("description") else ""
        )
    if kind == "certification":
        status = {"earned": "earned", "in_progress": "in progress", "expired": "expired"}.get(
            d.get("status", ""), ""
        )
        issuer = f", {d['issuer']}" if d.get("issuer") else ""
        return f"Certification: {d.get('name', '')}{issuer} ({status})"
    if kind == "education":
        cred = d.get("credential", "")
        field_ = f" in {d['field']}" if d.get("field") else ""
        status = " (in progress)" if d.get("status") == "in_progress" else ""
        return f"{cred}{field_}, {d.get('institution', '')}{status}".strip(", ")
    if kind == "skill":
        return f"Skill: {d.get('name', '')}" + (f" ({d['category']})" if d.get("category") else "")
    return ""


@dataclass
class Fact:
    id: str
    kind: str
    text: str
    parent_id: str | None
    data: dict[str, Any] = field(default_factory=dict)

    def for_prompt(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "text": self.text}
        if self.parent_id:
            out["belongs_to"] = self.parent_id
        return out


@dataclass
class StoryFact:
    id: str
    title: str
    text: str
    fact_ids: list[str]


@dataclass
class FactSet:
    facts: list[Fact]
    stories: list[StoryFact]

    @property
    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for f in self.facts}

    def prompt_json(self) -> str:
        return json.dumps([f.for_prompt() for f in self.facts], ensure_ascii=False, indent=1)

    def stories_json(self) -> str:
        return json.dumps(
            [
                {"id": s.id, "title": s.title, "story": s.text, "fact_ids": s.fact_ids}
                for s in self.stories
            ],
            ensure_ascii=False,
            indent=1,
        )

    def digest(self) -> str:
        """Changes whenever a usable fact is added, removed, or edited."""
        payload = json.dumps(sorted((f.id, f.kind, f.text) for f in self.facts))
        return hashlib.sha256(payload.encode()).hexdigest()


async def load_facts(db: AsyncSession, user_id: uuid.UUID) -> FactSet:
    index = await verification_index(db, user_id)
    entries = await db.scalars(
        select(VaultEntry)
        .where(
            VaultEntry.user_id == user_id, VaultEntry.verified.is_(True), VaultEntry.kind != "link"
        )
        .order_by(VaultEntry.sort_date.desc().nulls_last(), VaultEntry.created_at)
    )
    facts = [
        Fact(
            id=str(e.id),
            kind=e.kind,
            text=fact_text(e.kind, e.data),
            parent_id=str(e.parent_id) if e.parent_id else None,
            data=dict(e.data),
        )
        for e in entries
        if is_usable(index, e.id)
    ]
    usable = {f.id for f in facts}
    stories = list(
        await db.scalars(select(Story).where(Story.user_id == user_id, Story.verified.is_(True)))
    )
    links: dict[uuid.UUID, list[str]] = {s.id: [] for s in stories}
    if stories:
        for link in await db.scalars(
            select(StoryEntry).where(StoryEntry.user_id == user_id, StoryEntry.story_id.in_(links))
        ):
            if str(link.entry_id) in usable:
                links[link.story_id].append(str(link.entry_id))
    story_facts = [
        StoryFact(
            id=str(s.id),
            title=s.title,
            text=" ".join(
                f"{label}: {value}"
                for label, value in (
                    ("Situation", s.situation),
                    ("Task", s.task),
                    ("Action", s.action),
                    ("Result", s.result),
                )
                if value
            ),
            fact_ids=links[s.id],
        )
        for s in stories
    ]
    return FactSet(facts=facts, stories=story_facts)
