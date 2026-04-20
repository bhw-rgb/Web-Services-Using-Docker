import json
from typing import Optional

from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from redis import asyncio as aredis
from sqlalchemy import select, and_, not_

from connection_async import AsyncSessionFactory, engine
from models import Conversation, Message, Base

redis_client = aredis.from_url("redis://redis:6379", decode_responses=True)
app = FastAPI()

# ----------------------------
# Context rot 방지 설정값
# ----------------------------
MAX_CONTEXT_MESSAGES = 20
MAX_CONTEXT_CHARS = 1200
MAX_MESSAGE_CHARS = 400
SUMMARY_PREFIX = "[CONTEXT_SUMMARY]"


class ChatRequest(BaseModel):
    user_input: str
    conversation_id: Optional[str] = None


async def get_conversation_or_404(session, conversation_id: str) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation Not Found")
    return conversation


async def load_messages(session, conversation_id: str, include_summary: bool = True) -> list[dict]:
    stmt = (
        select(Message.role, Message.content)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.asc())
    )

    if not include_summary:
        stmt = stmt.where(
            not_(
                and_(
                    Message.role == "system",
                    Message.content.like(f"{SUMMARY_PREFIX}%"),
                )
            )
        )

    result = await session.execute(stmt)
    rows = result.all()
    return [{"role": role, "content": content} for role, content in rows]


async def get_latest_summary(session, conversation_id: str) -> Optional[str]:
    stmt = (
        select(Message.content)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "system",
            Message.content.like(f"{SUMMARY_PREFIX}%"),
        )
        .order_by(Message.id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.first()
    if not row:
        return None

    content = row[0]
    if content.startswith(SUMMARY_PREFIX):
        return content[len(SUMMARY_PREFIX):].strip()

    return content.strip()


def format_summary(summary_text: str) -> str:
    return f"{SUMMARY_PREFIX}\n{summary_text.strip()}"


def generate_compact_summary(messages: list[dict], max_items: int = 12) -> str:
    """
    아주 가벼운 요약기.
    외부 LLM 없이도 돌아가게 만든 압축 요약 버전.
    """
    if not messages:
        return ""

    lines = []
    lines.append("이전 대화에서 아래 내용이 있었음:")

    for idx, msg in enumerate(messages[-max_items:], start=1):
        role = msg.get("role", "user")
        content = " ".join(msg.get("content", "").split())
        if not content:
            continue

        if len(content) > 180:
            content = content[:177].rstrip() + "..."

        lines.append(f"{idx}. {role}: {content}")

    return "\n".join(lines)


async def upsert_summary_message(session, conversation_id: str, summary_text: str) -> None:
    """
    기존 summary system 메시지는 삭제하고, 최신 summary 하나만 남긴다.
    """
    if not summary_text.strip():
        return

    stmt = (
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == "system",
            Message.content.like(f"{SUMMARY_PREFIX}%"),
        )
        .order_by(Message.id.asc())
    )
    result = await session.execute(stmt)
    old_summaries = result.scalars().all()

    for old_msg in old_summaries:
        await session.delete(old_msg)

    summary_msg = Message(
        conversation_id=conversation_id,
        role="system",
        content=format_summary(summary_text),
    )
    session.add(summary_msg)


async def build_context_messages(session, conversation_id: str) -> list[dict]:
    """
    1) 기존 summary가 있으면 가져오고
    2) 전체 메시지 중 summary를 제외한 나머지를 기준으로
    3) 오래된 부분을 요약해서 system message로 저장한 뒤
    4) 요약 + 최근 메시지만 컨텍스트로 반환
    """
    existing_summary = await get_latest_summary(session, conversation_id)
    messages = await load_messages(session, conversation_id, include_summary=False)

    if not messages:
        if existing_summary:
            return [{"role": "system", "content": existing_summary}]
        return []

    total_chars = 0
    recent_messages = []
    trimmed_messages = []

    for msg in reversed(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if len(content) > MAX_MESSAGE_CHARS:
            content = content[-MAX_MESSAGE_CHARS:]

        msg_len = len(content)

        if len(recent_messages) >= MAX_CONTEXT_MESSAGES or total_chars + msg_len > MAX_CONTEXT_CHARS:
            trimmed_messages.append({"role": role, "content": content})
            continue

        recent_messages.append({"role": role, "content": content})
        total_chars += msg_len

    recent_messages.reverse()
    trimmed_messages.reverse()

    if trimmed_messages:
        new_summary_piece = generate_compact_summary(trimmed_messages)
        merged_summary = new_summary_piece

        if existing_summary:
            merged_summary = existing_summary.strip() + "\n\n" + new_summary_piece

        await upsert_summary_message(session, conversation_id, merged_summary)
        return [{"role": "system", "content": merged_summary}] + recent_messages

    if existing_summary:
        return [{"role": "system", "content": existing_summary}] + recent_messages

    return recent_messages


async def enqueue_task(channel: str, messages: list[dict]) -> None:
    task = {"channel": channel, "messages": messages}
    await redis_client.lpush("queue", json.dumps(task))


@app.post("/conversations", summary="대화 시작 API")
async def create_conversation_handler():
    async with AsyncSessionFactory() as session:
        conversation = Conversation()
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)

    return {
        "conversation_id": conversation.id,
        "created_at": conversation.created_at,
    }


@app.get("/conversations/{conversation_id}/messages", summary="전체 메세지 조회 API")
async def get_messages_handler(conversation_id: str):
    async with AsyncSessionFactory() as session:
        await get_conversation_or_404(session, conversation_id)
        messages = await load_messages(session, conversation_id, include_summary=True)

    return {
        "conversation_id": conversation_id,
        "count": len(messages),
        "messages": messages,
    }


@app.post("/conversations/{conversation_id}/messages", summary="메세지 생성 API")
async def create_message_handler(
    conversation_id: str,
    user_input: str = Body(..., embed=True),
):
    pubsub = None
    channel = conversation_id

    async with AsyncSessionFactory() as session:
        conversation = await get_conversation_or_404(session, conversation_id)

        user_msg = Message(
            conversation_id=conversation.id,
            role="user",
            content=user_input,
        )
        session.add(user_msg)
        await session.commit()

        context_messages = await build_context_messages(session, conversation.id)

        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)

        await enqueue_task(channel, context_messages)

    async def event_generator():
        assistant_text = ""

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                token = message["data"]

                if token == "[DONE]":
                    break

                assistant_text += token
                yield f"data: {token}\n\n"

            async with AsyncSessionFactory() as session:
                assistant_msg = Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=assistant_text,
                )
                session.add(assistant_msg)
                await session.commit()

            yield "data: [DONE]\n\n"

        finally:
            if pubsub is not None:
                await pubsub.unsubscribe(channel)
                await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )