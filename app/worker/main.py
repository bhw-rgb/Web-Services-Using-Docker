import json
import redis
from llama_cpp import Llama

# Redis 연결
redis_client = redis.from_url("redis://redis:6379", decode_responses=True)

# LLM 모델 로드
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
        _, task = redis_client.brpop("queue")

        channel = None
        try:
            task_data = json.loads(task)

            channel = task_data.get("channel")
            messages = task_data.get("messages", [])

            if not channel:
                print("Invalid task data: missing channel")
                continue

            full_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            full_messages.extend(messages)

            response_generator = llm.create_chat_completion(
                messages=full_messages,
                max_tokens=256,
                temperature=0.7,
                stream=True,
            )

            for chunk in response_generator:
                token = (
                    chunk.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content")
                )
                if token:
                    redis_client.publish(channel, token)

        except Exception as e:
            print(f"Error during generation: {e}")

        finally:
            if channel:
                redis_client.publish(channel, "[DONE]")


if __name__ == "__main__":
    run()