import uuid
import json

from fastapi import FastAPI, Body
from fastapi.responses import StreamingResponse # (수정 1) StreamingResponse 임포트 추가
from redis import asyncio as aredis

redis_client = aredis.from_url("redis://redis:6379", decode_responses=True)

app = FastAPI()

# (수정 4) GET 요청 대신 POST 요청으로 변경 (Body 데이터를 받기 위함)
@app.post("/chats")
async def generate_chat_handler(
    user_input: str = Body(..., embed=True),
):
    # (1) 요청 본문: user_input
    # (2) SUBSCRIBE 채널을 먼저 정해 놓는다.
    channel = str(uuid.uuid4()) # (수정 2) uuid.uuid() -> uuid.uuid4()
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel) # (수정 3) subscirbe -> subscribe 오타 수정
    
    # (3) Queue를 통해 Worker에 Task를 전달(enqueue)
    task = {"channel": channel, "user_input": user_input}
    await redis_client.lpush("queue", json.dumps(task))
    
    # (4) 채널 메세지 읽고, 토큰 반환(그때그때마다)
    async def event_generator():
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":            # 메세지만 받기위해
                    continue
                token = message["data"]                     # 토큰 변수에 메세지 받음
                if token == "[DONE]":                       # 토큰이 끝나면 종료하겠다.
                    break
                yield token
        finally:
            # (권장) 에러가 발생하거나 끊겨도 구독을 해제하도록 finally 블록 안에 넣는 것이 안전합니다.
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    # (5) 결과 수신
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )