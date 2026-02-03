"""LangGraph 记忆工具集 — Agent 可调用的 4 个记忆操作工具

双模式支持：Django 环境调用真实服务，独立模式（langgraph dev）返回 Mock。
"""

import logging
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _is_django_mode() -> bool:
    """检测是否在 Django 环境中运行"""
    try:
        import django

        return django.apps.apps.ready
    except Exception:
        return False


@tool
async def mem_search(user_id: int, query: str, limit: int = 5) -> str:
    """搜索用户记忆中与查询相关的内容。"""
    if not _is_django_mode():
        return "[独立模式] 未找到相关记忆。"
    from apps.memory.services import MemoryService

    results = await MemoryService.search_memory(user_id=user_id, query=query, limit=limit)
    if not results:
        return "未找到相关记忆。"
    return "\n".join(f"{i}. [{r['score']:.2f}] {r['memory'].content}" for i, r in enumerate(results, 1))


@tool
async def mem_cache(user_id: int, content: str, name: Optional[str] = None) -> str:
    """保存新的用户记忆。"""
    if not _is_django_mode():
        return f"[独立模式] 记忆已保存: {content[:50]}..."
    from apps.memory.services import MemoryService

    memory = await MemoryService.create_memory(user_id=user_id, content=content, name=name)
    return f"记忆已保存 (id={memory.id})"


@tool
async def mem_update(user_id: int, memory_id: int, content: str) -> str:
    """更新指定的用户记忆内容。"""
    if not _is_django_mode():
        return f"[独立模式] 记忆 (id={memory_id}) 已更新"
    from apps.memory.services import MemoryService

    try:
        await MemoryService.update_memory(memory_id=memory_id, user_id=user_id, content=content)
        return f"记忆 (id={memory_id}) 已更新"
    except Exception as e:
        return f"更新失败: {e}"


@tool
async def mem_delete(user_id: int, memory_id: int) -> str:
    """删除指定的用户记忆。"""
    if not _is_django_mode():
        return f"[独立模式] 记忆 (id={memory_id}) 已删除"
    from apps.memory.services import MemoryService

    try:
        await MemoryService.delete_memory(memory_id=memory_id, user_id=user_id)
        return f"记忆 (id={memory_id}) 已删除"
    except Exception as e:
        return f"删除失败: {e}"


MEMORY_TOOLS = [mem_search, mem_cache, mem_update, mem_delete]
