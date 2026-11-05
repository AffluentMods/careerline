# SPDX-License-Identifier: Apache-2.0
"""Assistant and interview prep API."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from waypoint import audit
from waypoint.ai.facts import load_facts
from waypoint.ai.jobs import allow_private_for
from waypoint.ai.providers import AiError
from waypoint.ai.service import AiClient, client_for, to_api_error
from waypoint.assistant import chat, interview
from waypoint.assistant.models import (
    ChatMessage,
    Conversation,
    InterviewPrep,
    MockSession,
    PendingAction,
)
from waypoint.auth.sessions import DB, CipherDep, CurrentUser, SettingsDep, csrf_protect
from waypoint.config import Settings
from waypoint.discovery.models import JobPosting
from waypoint.errors import ApiError
from waypoint.security.crypto import FieldCipher
from waypoint.tracker.models import Application
from waypoint.vault.schemas import Strict

router = APIRouter(prefix="/api/v1", tags=["assistant"], dependencies=[Depends(csrf_protect)])


async def _ai(
    db: AsyncSession, uid: uuid.UUID, cipher: FieldCipher, settings: Settings
) -> AiClient:
    try:
        return await client_for(
            db,
            uid,
            cipher,
            allow_private=await allow_private_for(db, uid, settings.outbound_allow_private),
        )
    except AiError as exc:
        raise to_api_error(exc) from None


async def _owned(db: AsyncSession, model: Any, uid: uuid.UUID, row_id: uuid.UUID) -> Any:
    row = await db.scalar(select(model).where(model.user_id == uid, model.id == row_id))
    if row is None:
        raise ApiError(404, "not_found", "Not found.")
    return row


# --- assistant ---------------------------------------------------------------------------------


class MessageIn(Strict):
    text: str = Field(min_length=1, max_length=4000)
    today: date | None = None


class DecisionIn(Strict):
    confirm: bool


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[dict[str, Any]]
    pending: int


async def _detail(db: AsyncSession, conv: Conversation) -> ConversationDetail:
    all_msgs = list(
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.user_id == conv.user_id, ChatMessage.conversation_id == conv.id)
            .order_by(ChatMessage.seq)
        )
    )
    actions = list(
        await db.scalars(
            select(PendingAction).where(
                PendingAction.user_id == conv.user_id, PendingAction.conversation_id == conv.id
            )
        )
    )
    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=chat.display(all_msgs, actions),
        pending=sum(a.status == "pending" for a in actions),
    )


@router.get("/assistant/conversations")
async def list_conversations(auth: CurrentUser, db: DB) -> list[ConversationOut]:
    rows = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == auth.user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(100)
    )
    return [ConversationOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/assistant/conversations", status_code=201)
async def create_conversation(auth: CurrentUser, db: DB) -> ConversationDetail:
    conv = Conversation(user_id=auth.user.id)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return await _detail(db, conv)


@router.get("/assistant/conversations/{conv_id}")
async def get_conversation(conv_id: uuid.UUID, auth: CurrentUser, db: DB) -> ConversationDetail:
    return await _detail(db, await _owned(db, Conversation, auth.user.id, conv_id))


@router.delete("/assistant/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    conv = await _owned(db, Conversation, auth.user.id, conv_id)
    await db.execute(
        delete(Conversation).where(Conversation.user_id == auth.user.id, Conversation.id == conv.id)
    )
    await db.commit()
    return Response(status_code=204)


@router.post("/assistant/conversations/{conv_id}/messages")
async def send_message(
    conv_id: uuid.UUID,
    body: MessageIn,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    settings: SettingsDep,
) -> ConversationDetail:
    uid = auth.user.id
    conv = await _owned(db, Conversation, uid, conv_id)
    pending = await db.scalar(
        select(PendingAction.id)
        .where(
            PendingAction.user_id == uid,
            PendingAction.conversation_id == conv.id,
            PendingAction.status == "pending",
        )
        .limit(1)
    )
    if pending:
        raise ApiError(409, "pending_actions", "Confirm or reject the proposed changes first.")
    ai = await _ai(db, uid, cipher, settings)
    try:
        await chat.send(db, ai, conv, body.text.strip(), body.today or datetime.now(UTC).date())
    except AiError as exc:
        await db.rollback()
        raise to_api_error(exc) from None
    await db.refresh(conv)
    return await _detail(db, conv)


@router.post("/assistant/actions/{action_id}")
async def decide_action(
    action_id: uuid.UUID, body: DecisionIn, request: Request, auth: CurrentUser, db: DB
) -> ConversationDetail:
    uid = auth.user.id
    # Lock the row so two concurrent decisions (two tabs, a retry) can't both see
    # "pending" and apply the same action twice; the second waits, then sees it decided.
    action = await db.scalar(
        select(PendingAction)
        .where(PendingAction.user_id == uid, PendingAction.id == action_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if action is None:
        raise ApiError(404, "not_found", "Not found.")
    changed = await chat.decide(db, action, body.confirm)
    if changed and action.status in ("confirmed", "failed"):
        audit.record(
            db,
            request,
            "assistant.action_confirmed",
            uid,
            action_id=str(action.id),
            tool=action.tool,
            status=action.status,
        )
    await db.commit()
    conv = await _owned(db, Conversation, uid, action.conversation_id)
    return await _detail(db, conv)


# --- interview prep -----------------------------------------------------------------------------


class PrepIn(Strict):
    method: Literal["rules", "ai"] = "rules"
    posting_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one(self) -> PrepIn:
        if bool(self.posting_id) == bool(self.application_id):
            raise ValueError("choose one job: a posting or an application")
        return self


class QuestionPatch(Strict):
    notes: str | None = Field(default=None, max_length=5000)
    story_ids: list[uuid.UUID] | None = Field(default=None, max_length=10)


class PrepOut(BaseModel):
    id: uuid.UUID
    posting_id: uuid.UUID | None
    application_id: uuid.UUID | None
    target_title: str
    target_company: str
    method: str
    questions: list[dict[str, Any]]
    stories: dict[str, str]
    created_at: datetime
    updated_at: datetime


class MockIn(Strict):
    prep_id: uuid.UUID
    max_questions: int = Field(default=5, ge=1, le=12)


class AnswerIn(Strict):
    text: str = Field(min_length=1, max_length=6000)


class MockOut(BaseModel):
    id: uuid.UUID
    prep_id: uuid.UUID | None
    target_title: str
    target_company: str
    max_questions: int
    status: str
    turns: list[dict[str, Any]]
    created_at: datetime


async def _prep_out(db: AsyncSession, prep: InterviewPrep) -> PrepOut:
    facts = await load_facts(db, prep.user_id)
    return PrepOut(
        id=prep.id,
        posting_id=prep.posting_id,
        application_id=prep.application_id,
        target_title=prep.target_title,
        target_company=prep.target_company,
        method=prep.method,
        questions=prep.questions,
        stories={s.id: s.title for s in facts.stories},
        created_at=prep.created_at,
        updated_at=prep.updated_at,
    )


def _mock_out(s: MockSession) -> MockOut:
    turns = [{k: v for k, v in t.items() if k != "raw"} for t in s.turns]
    return MockOut(
        id=s.id,
        prep_id=s.prep_id,
        target_title=s.target_title,
        target_company=s.target_company,
        max_questions=s.max_questions,
        status=s.status,
        turns=turns,
        created_at=s.created_at,
    )


@router.get("/interview/preps")
async def list_preps(
    auth: CurrentUser,
    db: DB,
    posting_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
) -> list[PrepOut]:
    stmt = (
        select(InterviewPrep)
        .where(InterviewPrep.user_id == auth.user.id)
        .order_by(InterviewPrep.created_at.desc())
    )
    if posting_id:
        stmt = stmt.where(InterviewPrep.posting_id == posting_id)
    if application_id:
        stmt = stmt.where(InterviewPrep.application_id == application_id)
    return [await _prep_out(db, p) for p in await db.scalars(stmt.limit(100))]


@router.post("/interview/preps", status_code=201)
async def create_prep(
    body: PrepIn, auth: CurrentUser, db: DB, cipher: CipherDep, settings: SettingsDep
) -> PrepOut:
    uid = auth.user.id
    if body.posting_id:
        p = await _owned(db, JobPosting, uid, body.posting_id)
        title, company, desc, posting_id, app_id = (
            p.title,
            p.company,
            p.description,
            p.id,
            p.application_id,
        )
    else:
        a = await _owned(db, Application, uid, body.application_id)  # type: ignore[arg-type]
        title, company, desc, posting_id, app_id = a.title, a.company, a.description, None, a.id
    facts = await load_facts(db, uid)
    if body.method == "ai":
        ai = await _ai(db, uid, cipher, settings)
        try:
            questions = await interview.questions_from_ai(ai, facts, title, company, desc)
        except AiError as exc:
            raise to_api_error(exc) from None
    else:
        questions = interview.questions_from_rules(facts, title, company, desc)
    prep = InterviewPrep(
        user_id=uid,
        posting_id=posting_id,
        application_id=app_id,
        target_title=title,
        target_company=company,
        target_description=desc,
        method=body.method,
        questions=questions,
    )
    db.add(prep)
    await db.commit()
    await db.refresh(prep)
    return await _prep_out(db, prep)


@router.get("/interview/preps/{prep_id}")
async def get_prep(prep_id: uuid.UUID, auth: CurrentUser, db: DB) -> PrepOut:
    return await _prep_out(db, await _owned(db, InterviewPrep, auth.user.id, prep_id))


@router.patch("/interview/preps/{prep_id}/questions/{question_id}")
async def patch_question(
    prep_id: uuid.UUID, question_id: str, body: QuestionPatch, auth: CurrentUser, db: DB
) -> PrepOut:
    uid = auth.user.id
    prep = await _owned(db, InterviewPrep, uid, prep_id)
    questions = [dict(q) for q in prep.questions]
    q = next((q for q in questions if q["id"] == question_id), None)
    if q is None:
        raise ApiError(404, "not_found", "That question wasn't found.")
    if body.notes is not None:
        q["notes"] = body.notes
    if body.story_ids is not None:
        known = {s.id for s in (await load_facts(db, uid)).stories}
        q["story_ids"] = [str(i) for i in body.story_ids if str(i) in known]
    prep.questions = questions
    flag_modified(prep, "questions")
    await db.commit()
    await db.refresh(prep)
    return await _prep_out(db, prep)


@router.delete("/interview/preps/{prep_id}", status_code=204)
async def delete_prep(prep_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    prep = await _owned(db, InterviewPrep, auth.user.id, prep_id)
    await db.delete(prep)
    await db.commit()
    return Response(status_code=204)


@router.get("/interview/mocks")
async def list_mocks(auth: CurrentUser, db: DB, prep_id: uuid.UUID | None = None) -> list[MockOut]:
    stmt = (
        select(MockSession)
        .where(MockSession.user_id == auth.user.id)
        .order_by(MockSession.created_at.desc())
    )
    if prep_id:
        stmt = stmt.where(MockSession.prep_id == prep_id)
    return [_mock_out(s) for s in await db.scalars(stmt.limit(50))]


@router.post("/interview/mocks", status_code=201)
async def start_mock(
    body: MockIn, auth: CurrentUser, db: DB, cipher: CipherDep, settings: SettingsDep
) -> MockOut:
    uid = auth.user.id
    prep = await _owned(db, InterviewPrep, uid, body.prep_id)
    ai = await _ai(db, uid, cipher, settings)
    facts = await load_facts(db, uid)
    try:
        first = await interview.mock_step(
            ai,
            facts,
            [],
            title=prep.target_title,
            company=prep.target_company,
            description=prep.target_description,
            max_questions=body.max_questions,
        )
    except AiError as exc:
        raise to_api_error(exc) from None
    first["feedback"], first["strengths"], first["improvements"], first["rating"] = "", [], [], 0
    session = MockSession(
        user_id=uid,
        prep_id=prep.id,
        target_title=prep.target_title,
        target_company=prep.target_company,
        target_description=prep.target_description,
        max_questions=body.max_questions,
        turns=[first],
        status="finished" if first["finished"] else "active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _mock_out(session)


@router.get("/interview/mocks/{mock_id}")
async def get_mock(mock_id: uuid.UUID, auth: CurrentUser, db: DB) -> MockOut:
    return _mock_out(await _owned(db, MockSession, auth.user.id, mock_id))


@router.post("/interview/mocks/{mock_id}/answer")
async def answer_mock(
    mock_id: uuid.UUID,
    body: AnswerIn,
    auth: CurrentUser,
    db: DB,
    cipher: CipherDep,
    settings: SettingsDep,
) -> MockOut:
    uid = auth.user.id
    session = await _owned(db, MockSession, uid, mock_id)
    if session.status != "active":
        raise ApiError(409, "finished", "This practice interview is over. Start a new one.")
    ai = await _ai(db, uid, cipher, settings)
    turns = [*session.turns, {"role": "candidate", "text": body.text.strip()}]
    try:
        reply = await interview.mock_step(
            ai,
            await load_facts(db, uid),
            turns,
            title=session.target_title,
            company=session.target_company,
            description=session.target_description,
            max_questions=session.max_questions,
        )
    except AiError as exc:
        raise to_api_error(exc) from None
    session.turns = [*turns, reply]
    session.status = "finished" if reply["finished"] else "active"
    flag_modified(session, "turns")
    await db.commit()
    await db.refresh(session)
    return _mock_out(session)


@router.delete("/interview/mocks/{mock_id}", status_code=204)
async def delete_mock(mock_id: uuid.UUID, auth: CurrentUser, db: DB) -> Response:
    session = await _owned(db, MockSession, auth.user.id, mock_id)
    await db.delete(session)
    await db.commit()
    return Response(status_code=204)
