# SPDX-License-Identifier: Apache-2.0
"""Background scoring: new postings get scored, stale keyword scores get refreshed."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from waypoint.ai.facts import load_facts
from waypoint.ai.providers import AiError
from waypoint.ai.scoring import ai_unavailable, postings_to_score, score_posting
from waypoint.ai.service import AiClient, client_for, get_settings, is_configured
from waypoint.discovery.models import JobFilters, QueuedJob
from waypoint.discovery.service import evaluate
from waypoint.models import User
from waypoint.worker import queue
from waypoint.worker.runner import WorkerContext, handler

log = logging.getLogger("waypoint.ai")

AI_SCORES_PER_RUN = 50
RESCORE_DELAY = timedelta(seconds=20)


async def queue_rescore(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Debounced: vault edits in quick succession lead to one scoring run. Caller commits."""
    await queue.enqueue(
        db,
        "score_postings",
        {},
        user_id=user_id,
        dedupe_key=f"score:{user_id}",
        run_after=queue.utcnow() + RESCORE_DELAY,
    )


async def allow_private_for(db: AsyncSession, user_id: uuid.UUID, instance_allows: bool) -> bool:
    """Private AI endpoints (a LAN Ollama) are allowed for the instance owner, or when the
    instance allows private outbound targets for everyone."""
    if instance_allows:
        return True
    user = await db.get(User, user_id)
    return bool(user and user.is_owner)


@handler("score_postings")
async def score_postings(ctx: WorkerContext, job: QueuedJob) -> None:
    user_id = job.user_id
    if user_id is None:
        return
    async with ctx.sessionmaker() as db:
        factset = await load_facts(db, user_id)
        todo, never_scored = await postings_to_score(db, user_id, factset.digest())
        filters = await db.get(JobFilters, user_id)
        settings = await get_settings(db, user_id)
        ai: AiClient | None = None
        if (
            is_configured(settings)
            and settings is not None
            and settings.auto_score
            and factset.facts
        ):
            try:
                ai = await client_for(
                    db,
                    user_id,
                    ctx.cipher,
                    allow_private=await allow_private_for(
                        db, user_id, ctx.settings.outbound_allow_private
                    ),
                )
            except AiError as exc:
                log.info("AI scoring unavailable for %s: %s", user_id, exc.code)
        ai_used = 0
        for posting in todo:
            # AI only for brand-new postings that pass the hard filters, and only so many per run.
            use_ai = (
                ai
                if ai
                and posting.id in never_scored
                and ai_used < AI_SCORES_PER_RUN
                and evaluate(posting, filters).passed
                else None
            )
            try:
                await score_posting(db, user_id, posting, ai=use_ai, factset=factset)
                ai_used += 1 if use_ai else 0
            except AiError as exc:
                if ai_unavailable(exc):
                    ai = None
                await score_posting(db, user_id, posting, ai=None, factset=factset)
            await db.commit()
    log.info("scored %d postings for %s (%d with AI)", len(todo), user_id, ai_used)
