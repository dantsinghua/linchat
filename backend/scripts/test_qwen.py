#!/usr/bin/env python3
"""单独测试 qwen3.5-plus，120s 超时"""
import httpx
import json
import time

API_KEY = "sk-sp-5e73f653eb204851b528f3e01672c3da"
BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"

payload = {
    "model": "qwen3.5-plus",
    "messages": [
        {"role": "system", "content": "你是一个友善、专业的AI助手。"},
        {"role": "user", "content": "你好！我是一个对编程感兴趣的大学生。请帮我介绍一下 Python 和 Rust 这两种语言各自的优缺点，然后给我一个选择建议。回答要简洁友好，300字以内。"},
    ],
    "stream": True,
    "max_tokens": 1024,
    "extra_body": {"enable_thinking": False},
}

headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

start = time.perf_counter()
first_token_time = None
chunks = []
thinking_chunks = []
output_tokens = 0

with httpx.Client(timeout=120) as client:
    with client.stream("POST", f"{BASE_URL}/chat/completions", json=payload, headers=headers) as resp:
        print(f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(resp.read().decode()[:500])
        else:
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except Exception:
                    continue
                choices = data.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                reasoning = delta.get("reasoning_content", "")
                if reasoning:
                    thinking_chunks.append(reasoning)
                content = delta.get("content", "")
                if content:
                    if first_token_time is None:
                        first_token_time = time.perf_counter()
                    chunks.append(content)
                usage = data.get("usage")
                if usage and usage.get("completion_tokens"):
                    output_tokens = usage["completion_tokens"]

end = time.perf_counter()
text = "".join(chunks)
thinking = "".join(thinking_chunks)

total_ms = (end - start) * 1000
first_ms = (first_token_time - start) * 1000 if first_token_time else 0
if output_tokens == 0:
    output_tokens = max(1, int(len(text) * 1.5))
tps = output_tokens / (total_ms / 1000) if total_ms > 0 else 0

print(f"首token: {first_ms:.0f}ms | 总耗时: {total_ms:.0f}ms | tokens: {output_tokens} | 速率: {tps:.1f} tok/s")
if thinking:
    print(f"[思考] {thinking[:200]}...")
print(f"[输出] {text}")
