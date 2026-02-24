"""历史消息搜索工具

供主 Agent 调用，搜索用户的历史对话记录。
"""

import logging

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
async def history_search(
    keyword: str,
    config: RunnableConfig,
    days: int = 30,
    limit: int = 10,
) -> str:
    """搜索用户的历史对话记录。

    当用户想查找之前聊过的内容时使用。
    支持按关键词和时间范围搜索。

    Args:
        keyword: 搜索关键词
        days: 搜索最近多少天的记录，默认 30 天
        limit: 返回最多多少条记录，默认 10 条
    """
    from apps.chat.repositories import message_repo

    user_id = config.get("configurable", {}).get("user_id")
    if user_id is None:
        return "无法获取用户信息"

    messages = await message_repo.search_messages(
        user_id=int(user_id),
        keyword=keyword,
        days=days,
        limit=limit,
    )

    if not messages:
        return f"未找到包含「{keyword}」的历史记录"

    results = []
    for msg in messages:
        role_label = "用户" if msg.role == "user" else "助手"
        time_str = msg.created_time.strftime("%Y-%m-%d %H:%M")
        content_preview = msg.content[:200] if msg.content else ""
        results.append(f"[{time_str}] {role_label}: {content_preview}")

    header = f"找到 {len(messages)} 条包含「{keyword}」的历史记录："
    return header + "\n" + "\n---\n".join(results)
