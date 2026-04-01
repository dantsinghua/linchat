import asyncio
import calendar
import logging
import time
from datetime import datetime, timezone

import httpx
from django.conf import settings
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.graph.tools.user_id import get_user_id as _get_user_id

logger = logging.getLogger(__name__)


def _end_of_month_ts() -> int:
    """返回当前月末最后一秒的 UNIX 时间戳"""
    now = datetime.now(tz=timezone.utc)
    last_day = calendar.monthrange(now.year, now.month)[1]
    end = now.replace(day=last_day, hour=23, minute=59, second=59)
    return int(end.timestamp())


async def _acquire_rate_slot(user_id: int) -> str | None:
    """获取搜索速率槽位，超 QPS 时等待而非拒绝。月度配额耗尽返回错误消息。"""
    import redis.asyncio as aioredis

    qps = settings.BRAVE_SEARCH_QPS
    monthly_quota = settings.BRAVE_SEARCH_MONTHLY_QUOTA
    r: aioredis.Redis = aioredis.from_url(settings.REDIS_URL)
    try:
        # 月度配额检查（全局）
        month_key = "search:quota:monthly"
        total = await r.incr(month_key)
        if total == 1:
            await r.expireat(month_key, _end_of_month_ts())
        if total > monthly_quota:
            return f"搜索工具超出限额（本月{monthly_quota}次配额已用完）"

        # QPS 限流：等待而非拒绝
        max_wait = 5.0  # 最多等待5秒
        waited = 0.0
        sec_key = f"search:rate:{user_id}"
        while waited < max_wait:
            count = await r.incr(sec_key)
            if count == 1:
                await r.expire(sec_key, 1)
            if count <= qps:
                return None  # 获取到槽位
            # 超过 QPS，等待到下一秒
            ttl_ms = await r.pttl(sec_key)
            wait_s = (ttl_ms / 1000.0) + 0.05 if ttl_ms > 0 else 0.2
            logger.info("[web_search] QPS throttle: user_id=%d, count=%d, qps=%d, waiting=%.2fs", user_id, count, qps, wait_s)
            await asyncio.sleep(wait_s)
            waited += wait_s

        return None  # 超过最大等待时间也放行，避免阻塞
    finally:
        await r.aclose()


@tool
async def web_search(
    query: str, config: RunnableConfig, num_results: int = 5
) -> str:
    """搜索互联网获取最新信息。当需要实时信息（新闻、天气、最新技术动态等）时使用。"""
    user_id = _get_user_id(config)
    t0 = time.monotonic()
    logger.info("[web_search] START: user_id=%d, query='%s', num_results=%d", user_id, query[:80], num_results)

    # 限流：超 QPS 等待，月度配额耗尽才拒绝
    t_rate = time.monotonic()
    err = await _acquire_rate_slot(user_id)
    rate_ms = (time.monotonic() - t_rate) * 1000
    logger.info("[web_search] rate_slot: user_id=%d, waited=%.0fms, blocked=%s", user_id, rate_ms, bool(err))
    if err:
        logger.warning("[web_search] QUOTA EXHAUSTED: user_id=%d, query='%s', msg=%s", user_id, query[:80], err)
        return err

    # 调用 Brave Search API
    t_api = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": num_results, "search_lang": "zh-hans"},
                headers={
                    "X-Subscription-Token": settings.BRAVE_SEARCH_API_KEY,
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
        api_ms = (time.monotonic() - t_api) * 1000
        logger.info("[web_search] Brave API: status=%d, cost=%.0fms", resp.status_code, api_ms)
    except httpx.TimeoutException:
        api_ms = (time.monotonic() - t_api) * 1000
        logger.error("[web_search] Brave API TIMEOUT: query='%s', cost=%.0fms", query[:80], api_ms)
        raise
    except httpx.HTTPStatusError as e:
        api_ms = (time.monotonic() - t_api) * 1000
        logger.error("[web_search] Brave API HTTP ERROR: status=%d, query='%s', cost=%.0fms", e.response.status_code, query[:80], api_ms)
        raise

    results = resp.json().get("web", {}).get("results", [])
    if not results:
        total_ms = (time.monotonic() - t0) * 1000
        logger.info("[web_search] END (no results): query='%s', total=%.0fms", query[:80], total_ms)
        return f"未找到与「{query}」相关的搜索结果。"

    # 返回编号格式，引导 LLM 使用 [[N]] 引用
    lines: list[str] = ["搜索结果："]
    for i, r in enumerate(results, 1):
        lines.append(
            f"[{i}] {r['title']} | {r['url']}\n{r.get('description', '')}"
        )
    lines.append(
        "\n---\n引用指令：在回答中使用 [[1]]、[[2]] 等标注引用来源。"
        "回答末尾附上引文列表，格式：\n**参考来源：**\n"
        "1. [标题](url)\n2. [标题](url)"
    )
    from apps.graph.tools import cap_tool_result
    result = cap_tool_result("\n\n".join(lines), "web_search")
    total_ms = (time.monotonic() - t0) * 1000
    logger.info("[web_search] END: query='%s', results=%d, total=%.0fms", query[:80], len(results), total_ms)
    return result


SEARCH_TOOLS = [web_search]
