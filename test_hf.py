from huggingface_hub import InferenceClient
import os

client = InferenceClient(token=os.environ.get("HF_TOKEN"))
try:
    response = client.chat_completion(
        model="Qwen/Qwen2.5-7B-Instruct",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=10
    )
    print(response)
except Exception as e:
    print("Error:", e)
