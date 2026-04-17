import json
import redis
from llama_cpp import Llama

# 1. Redis 연결 설정
redis_client = redis.from_url("redis://redis:6379", decode_responses=True)

# 2. LLM 모델 로드 (전역에서 한 번만 로드)
llm = Llama(
    model_path="./models/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
    n_ctx=4096,
    n_threads=2,
    verbose=False,
    chat_format="llama-3",
)

SYSTEM_PROMPT = (
    "You are a concise assistant. "
    "Always reply in the same language as the user's input. "
    "Do not change the language. "
    "Do not mix languages."
)


def run():
    print("Worker is running... Waiting for tasks in 'queue'.")
    while True:
        # 1) Queue에서 task를 dequeue (blocking pop)
        _, task = redis_client.brpop("queue")
        
        try:
            task_data = json.loads(task)
            user_input = task_data.get("user_input") # 안전하게 데이터 가져오기
            channel = task_data.get("channel") # 오타 수정: chnnel -> channel
            
            if not user_input or not channel:
                print("Invalid task data received.")
                continue

            # 2) 추론 -> 스트리밍 토큰 -> Redis Publish
            response_generator = llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_input},
                ],
                max_tokens=256,
                temperature=0.7,
                stream=True,
            )

            for chunk in response_generator:
                token = chunk["choices"][0]["delta"].get("content")
                if token:
                    redis_client.publish(channel, token)

            # 3) 추론 종료 알림
            redis_client.publish(channel, "[DONE]")
            
        except Exception as e:
            print(f"Error processing task: {e}")

if __name__ == "__main__":
    run()