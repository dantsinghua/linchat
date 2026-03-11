#!/usr/bin/env python3
"""
LLM 模型对比基准测试脚本

测试 4 个模型的连通性、性能指标和输出质量：
- glm-5 (DashScope)
- qwen3.5-plus (DashScope)
- MiniMax-2.5 (DashScope)
- kimi-k2.5 (DashScope)

性能指标：首 token 耗时、总响应时长、token 输出数、token 生成速率
输出质量：内容可读性、事实准确性、逻辑性、友好程度与情绪价值
"""

import asyncio
import json
import time
from dataclasses import dataclass, field

import httpx

API_KEY = "sk-sp-5e73f653eb204851b528f3e01672c3da"
BASE_URL = "https://coding.dashscope.aliyuncs.com/v1"

MODELS = [
    "glm-5",
    "qwen3.5-plus",
    "MiniMax-M2.5",
    "kimi-k2.5",
]

# 测试 prompt — 综合考查事实准确性、逻辑性、可读性、友好程度
TEST_PROMPT = (
    "你好！我是一个对编程感兴趣的大学生。"
    "请帮我介绍一下 Python 和 Rust 这两种语言各自的优缺点，"
    "然后给我一个选择建议。回答要简洁友好，300字以内。"
)


@dataclass
class BenchmarkResult:
    model: str
    success: bool = False
    error: str = ""
    first_token_ms: float = 0.0
    total_time_ms: float = 0.0
    output_tokens: int = 0
    tokens_per_second: float = 0.0
    output_text: str = ""
    thinking_text: str = ""


async def benchmark_model(client: httpx.AsyncClient, model: str) -> BenchmarkResult:
    """流式请求单个模型，测量各项性能指标"""
    result = BenchmarkResult(model=model)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个友善、专业的AI助手。"},
            {"role": "user", "content": TEST_PROMPT},
        ],
        "stream": True,
        "max_tokens": 1024,
    }

    # qwen3.5 需要关闭 thinking
    if "qwen3" in model.lower():
        payload["extra_body"] = {"enable_thinking": False}

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    start = time.perf_counter()
    first_token_time = None
    chunks: list[str] = []
    thinking_chunks: list[str] = []

    try:
        async with client.stream(
            "POST",
            f"{BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60.0,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                result.error = f"HTTP {resp.status_code}: {body.decode()[:500]}"
                return result

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = data.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {})

                # 处理 reasoning_content (thinking)
                reasoning = delta.get("reasoning_content", "")
                if reasoning:
                    thinking_chunks.append(reasoning)

                content = delta.get("content", "")
                if content:
                    if first_token_time is None:
                        first_token_time = time.perf_counter()
                    chunks.append(content)

                # 收集 usage 信息
                usage = data.get("usage")
                if usage and usage.get("completion_tokens"):
                    result.output_tokens = usage["completion_tokens"]

    except httpx.TimeoutException:
        result.error = "请求超时 (60s)"
        return result
    except Exception as e:
        result.error = f"{type(e).__name__}: {str(e)[:300]}"
        return result

    end = time.perf_counter()

    result.output_text = "".join(chunks)
    result.thinking_text = "".join(thinking_chunks)
    result.success = bool(result.output_text)

    if not result.success:
        result.error = "未收到任何输出内容"
        return result

    result.total_time_ms = (end - start) * 1000
    if first_token_time is not None:
        result.first_token_ms = (first_token_time - start) * 1000

    # 如果 API 没返回 token 数，用字符估算（中文 ~1.5 token/字）
    if result.output_tokens == 0:
        result.output_tokens = max(1, int(len(result.output_text) * 1.5))

    if result.total_time_ms > 0:
        result.tokens_per_second = result.output_tokens / (result.total_time_ms / 1000)

    return result


async def run_benchmark():
    print("=" * 80)
    print("LLM 模型对比基准测试")
    print(f"API: {BASE_URL}")
    print(f"测试模型: {', '.join(MODELS)}")
    print("=" * 80)
    print()

    results: list[BenchmarkResult] = []

    async with httpx.AsyncClient() as client:
        for model in MODELS:
            print(f"▶ 测试 {model} ...")
            r = await benchmark_model(client, model)
            results.append(r)

            if r.success:
                print(f"  ✅ 成功 | 首token: {r.first_token_ms:.0f}ms | "
                      f"总耗时: {r.total_time_ms:.0f}ms | "
                      f"输出tokens: {r.output_tokens} | "
                      f"速率: {r.tokens_per_second:.1f} tok/s")
            else:
                print(f"  ❌ 失败: {r.error}")
            print()

    # ========== 性能对比表 ==========
    print("\n" + "=" * 80)
    print("📊 性能对比")
    print("=" * 80)
    print(f"{'模型':<20} {'状态':<6} {'首token(ms)':<14} {'总耗时(ms)':<14} {'输出tokens':<12} {'速率(tok/s)':<12}")
    print("-" * 80)
    for r in results:
        if r.success:
            print(f"{r.model:<20} {'✅':<6} {r.first_token_ms:<14.0f} {r.total_time_ms:<14.0f} {r.output_tokens:<12} {r.tokens_per_second:<12.1f}")
        else:
            print(f"{r.model:<20} {'❌':<6} {'N/A':<14} {'N/A':<14} {'N/A':<12} {'N/A':<12}")

    # ========== 输出内容 ==========
    print("\n" + "=" * 80)
    print("📝 各模型输出内容")
    print("=" * 80)
    for r in results:
        print(f"\n--- {r.model} ---")
        if r.success:
            if r.thinking_text:
                print(f"[思考过程] {r.thinking_text[:200]}...")
            print(r.output_text)
        else:
            print(f"[失败] {r.error}")

    # ========== 找出最佳 ==========
    successful = [r for r in results if r.success]
    if successful:
        print("\n" + "=" * 80)
        print("🏆 性能排名")
        print("=" * 80)

        # 首 token 排名
        by_first_token = sorted(successful, key=lambda x: x.first_token_ms)
        print("\n首 Token 耗时 (越低越好):")
        for i, r in enumerate(by_first_token, 1):
            print(f"  {i}. {r.model}: {r.first_token_ms:.0f}ms")

        # 总耗时排名
        by_total = sorted(successful, key=lambda x: x.total_time_ms)
        print("\n总响应时长 (越低越好):")
        for i, r in enumerate(by_total, 1):
            print(f"  {i}. {r.model}: {r.total_time_ms:.0f}ms")

        # 速率排名
        by_speed = sorted(successful, key=lambda x: x.tokens_per_second, reverse=True)
        print("\nToken 生成速率 (越高越好):")
        for i, r in enumerate(by_speed, 1):
            print(f"  {i}. {r.model}: {r.tokens_per_second:.1f} tok/s")

    return results


if __name__ == "__main__":
    asyncio.run(run_benchmark())
