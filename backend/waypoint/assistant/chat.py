# SPDX-License-Identifier: Apache-2.0
"""The assistant loop: model turn, run read tools, propose write tools, repeat (bounded)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from waypoint.ai.prompting import load_prompt, redact
from waypoint.ai.providers import ToolCall, Turn
from waypoint.ai.service import AiClient, chat_step
from waypoint.assistant.models import ChatMessage, Conversation, PendingAction
from waypoint.assistant.tools import TOOLS, ToolError, as_json

MAX_STEPS = 6
MAX_HISTORY = 60


async def _next_seq(db: AsyncSession, conv: Conversation) -> int:
    last = await db.scalar(
        select(func.max(ChatMessage.seq)).where(ChatMessage.conversation_id == conv.id)
    )
    return int(last or 0) + 1


def _turn(m: ChatMessage) -> Turn:
    if m.role == "assistant":
        calls = [
            ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments", {}))
            for c in m.tool_calls
        ]
        return Turn(role="assistant", text=m.text, tool_calls=calls, native=m.native)
    if m.role == "tool":
        return Turn(role="tool", text=m.text, tool_call_id=m.tool_call_id)
    return Turn(role="user", text=m.text)


async def history(db: AsyncSession, conv: Conversation) -> list[ChatMessage]:
    rows = list(
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.user_id == conv.user_id, ChatMessage.conversation_id == conv.id)
            .order_by(ChatMessage.seq)
        )
    )
    if len(rows) > MAX_HISTORY:
        # Start at a user message so tool calls and their results stay paired.
        rows = rows[-MAX_HISTORY:]
        while rows and rows[0].role != "user":
            rows.pop(0)
    return rows


async def _decisions_since(db: AsyncSession, conv: Conversation, since: datetime | None) -> str:
    """Tell the model which of its proposals the user confirmed or rejected."""
    stmt = select(PendingAction).where(
        PendingAction.user_id == conv.user_id,
        PendingAction.conversation_id == conv.id,
        PendingAction.status != "pending",
    )
    if since:
        stmt = stmt.where(PendingAction.decided_at > since)
    rows = list(await db.scalars(stmt.order_by(PendingAction.decided_at)))
    if not rows:
        return ""
    lines = [f"- {a.summary} -> {a.status}{f' ({a.result})' if a.result else ''}" for a in rows]
    return (
        "(Since your last reply, the user decided on your proposals:\n" + "\n".join(lines) + ")\n\n"
    )


async def send(db: AsyncSession, ai: AiClient, conv: Conversation, text: str, today: date) -> None:
    """Add the user's message and run the loop. Commits as it goes."""
    previous = await history(db, conv)
    last_assistant = next((m.created_at for m in reversed(previous) if m.role == "assistant"), None)
    note = await _decisions_since(db, conv, last_assistant)
    seq = await _next_seq(db, conv)
    db.add(
        ChatMessage(
            user_id=conv.user_id, conversation_id=conv.id, seq=seq, role="user", text=note + text
        )
    )
    if conv.title == "New conversation":
        conv.title = text.strip().splitlines()[0][:80] or conv.title
    conv.updated_at = datetime.now(UTC)
    await db.commit()

    template = load_prompt("assistant")
    system, _ = template.render(today=today.isoformat(), message="")
    specs = [t.spec() for t in TOOLS.values()]
    for _step in range(MAX_STEPS):
        turns = [_turn(m) for m in await history(db, conv)]
        if ai.hosted and ai.settings.redact_pii:
            turns = [
                Turn(
                    role=t.role,
                    text=redact(t.text),
                    tool_calls=t.tool_calls,
                    tool_call_id=t.tool_call_id,
                    is_error=t.is_error,
                    native=t.native,
                )
                for t in turns
            ]
        completion = await chat_step(
            ai,
            feature="assistant",
            system_prompt=(template.name, template.version, system),
            turns=turns,
            tools=specs,
            max_tokens=4000,
            effort="medium",
            subject={"conversation_id": str(conv.id)},
        )
        seq = await _next_seq(db, conv)
        db.add(
            ChatMessage(
                user_id=conv.user_id,
                conversation_id=conv.id,
                seq=seq,
                role="assistant",
                text=completion.text,
                tool_calls=[
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in completion.tool_calls
                ],
                native=completion.native,
            )
        )
        await db.flush()
        if not completion.tool_calls:
            await db.commit()
            return
        for call in completion.tool_calls:
            seq += 1
            result, name = await _run_tool(db, conv, call, today)
            db.add(
                ChatMessage(
                    user_id=conv.user_id,
                    conversation_id=conv.id,
                    seq=seq,
                    role="tool",
                    text=result,
                    tool_call_id=call.id,
                    tool_name=name,
                )
            )
        await db.commit()
    seq = await _next_seq(db, conv)
    db.add(
        ChatMessage(
            user_id=conv.user_id,
            conversation_id=conv.id,
            seq=seq,
            role="assistant",
            text="I stopped there to keep this short. Ask me to continue if you need more.",
        )
    )
    await db.commit()


async def _run_tool(
    db: AsyncSession, conv: Conversation, call: ToolCall, today: date
) -> tuple[str, str]:
    tool = TOOLS.get(call.name)
    if tool is None:
        return as_json({"error": f"Unknown tool {call.name!r}."}), call.name[:64]
    try:
        args = tool.parse(call.arguments)
        if tool.read is not None:
            return as_json(await tool.read(db, conv.user_id, args, today)), tool.name
        assert tool.describe is not None  # noqa: S101
        summary = await tool.describe(db, conv.user_id, args)
    except ToolError as exc:
        return as_json({"error": str(exc)}), tool.name
    action = PendingAction(
        user_id=conv.user_id,
        conversation_id=conv.id,
        tool_call_id=call.id,
        tool=tool.name,
        arguments=args.model_dump(mode="json"),
        summary=summary[:500],
    )
    db.add(action)
    await db.flush()
    return as_json(
        {
            "status": "waiting_for_user_confirmation",
            "proposal": summary,
            "note": "Nothing has changed yet. The user will confirm or reject this in the app.",
        }
    ), tool.name


async def decide(db: AsyncSession, action: PendingAction, confirm: bool) -> bool:
    """Apply (or reject) a pending action, re-validating everything at apply time.
    Returns False if it was already decided. Caller commits."""
    if action.status != "pending":
        return False
    action.decided_at = datetime.now(UTC)
    if not confirm:
        action.status = "rejected"
        return True
    tool = TOOLS[action.tool]
    assert tool.apply is not None  # noqa: S101
    try:
        action.result = (await tool.apply(db, action.user_id, tool.parse(action.arguments)))[:500]
        action.status = "confirmed"
    except ToolError as exc:
        action.status, action.result = "failed", str(exc)[:500]
    return True


def display(messages: list[ChatMessage], actions: list[PendingAction]) -> list[dict[str, Any]]:
    """What the UI shows: user and assistant text, plus proposal cards in place."""
    by_call = {a.tool_call_id: a for a in actions}
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            text = (
                m.text.split(")\n\n", 1)[1]
                if m.text.startswith("(Since your last reply") and ")\n\n" in m.text
                else m.text
            )
            out.append({"id": str(m.id), "role": "user", "text": text})
        elif m.role == "assistant" and m.text.strip():
            out.append({"id": str(m.id), "role": "assistant", "text": m.text})
        elif m.role == "tool" and m.tool_call_id in by_call:
            a = by_call[m.tool_call_id]
            out.append(
                {
                    "id": str(a.id),
                    "role": "action",
                    "text": a.summary,
                    "status": a.status,
                    "result": a.result,
                    "tool": a.tool,
                }
            )
        elif m.role == "tool":
            out.append({"id": str(m.id), "role": "tool", "text": "", "tool": m.tool_name})
    return out
