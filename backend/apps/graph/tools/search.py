import calendar
import logging
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


async def _check_rate_limit(user_id: int) -> str | None:
    """检查限流，通过返回 None，超限返回错误消息"""
    import redis.asyncio as aioredis

    r: aioredis.Redis = aioredis.from_url(settings.REDIS_URL)
    try:
        # 秒级限流：每用户每秒 1 次
        sec_key = f"search:rate:{user_id}"
        count = await r.incr(sec_key)
        if count == 1:
            await r.expire(sec_key, 1)
        if count > 1:
            return "搜索工具超出限额（每秒最多1次请求，请稍后再试）"

        # 月度配额：全局 2000 次/月
        month_key = "search:quota:monthly"
        total = await r.incr(month_key)
        if total == 1:
            await r.expireat(month_key, _end_of_month_ts())
        if total > 2000:
            return "搜索工具超出限额（本月2000次配额已用完）"

        return None
    finally:
        await r.aclose()


@tool
async def web_search(
    query: str, config: RunnableConfig, num_results: int = 5
) -> str:
    """搜索互联网获取最新信息。当需要实时信息（新闻、天气、最新技术动态等）时使用。"""
    user_id = _get_user_id(config)

    # 限流检查
    err = await _check_rate_limit(user_id)
    if err:
        return err

    # 调用 Brave Search API
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

    results = resp.json().get("web", {}).get("results", [])
    if not results:
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
    return cap_tool_result("\n\n".join(lines), "web_search")


SEARCH_TOOLS = [web_search]
