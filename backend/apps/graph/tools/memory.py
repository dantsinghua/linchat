"""LangGraph 记忆工具集 — Agent 可调用的 4 个记忆操作工具

双模式支持：Django 环境调用真实服务，独立模式（langgraph dev）返回 Mock。

user_id 通过 RunnableConfig 隐式注入，LLM 不可见也不可篡改 [R-004]。
"""

import logging
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _is_django_mode() -> bool:
    """检测是否在 Django 环境中运行"""
    try:
        import django

        return django.apps.apps.ready
    except Exception:
        return False


def _get_user_id(config: RunnableConfig) -> int:
    """从 RunnableConfig 中提取 user_id，缺失时抛出异常"""
    user_id = config.get("configurable", {}).get("user_id")
    if user_id is None:
        raise ValueError("user_id not found in RunnableConfig")
    return int(user_id)


@tool
async def mem_search(query: str, config: RunnableConfig, limit: int = 5) -> str:
    """搜索用户记忆中与查询相关的内容。"""
    user_id = _get_user_id(config)
    if not _is_django_mode():
        return "[独立模式] 未找到相关记忆。"
    from apps.memory.services import MemoryService

    results = await MemoryService.search_memory(user_id=user_id, query=query, limit=limit)
    if not results:
        return "未找到相关记忆。"
    from apps.graph.tools import cap_tool_result
    result = "\n".join(
        f"{i}. [id={r['memory'].id}] {r['memory'].content}"
        for i, r in enumerate(results, 1)
    )
    return cap_tool_result(result, "mem_search")


@tool
async def mem_cache(
    content: str,
    config: RunnableConfig,
    name: Optional[str] = None,
    tag: Optional[str] = None,
) -> str:
    """保存新的用户记忆。必须提供一个语义标签 tag（如"个人喜好"/"职业信息"/"工作任务"/"日常对话"等），用于分类管理记忆。"""
    user_id = _get_user_id(config)
    if not _is_django_mode():
        return f"[独立模式] 记忆已保存: {content[:50]}..."
    from apps.memory.services import MemoryService

    memory = await MemoryService.create_memory(
        user_id=user_id, content=content, name=name, tag=tag,
    )
    return f"记忆已保存 (id={memory.id})"


@tool
async def mem_update(
    memory_id: int,
    content: str,
    config: RunnableConfig,
    tag: Optional[str] = None,
) -> str:
    """更新指定的用户记忆内容。需要先通过 mem_search 获取 memory_id。可提供语义标签 tag 更新分类。"""
    user_id = _get_user_id(config)
    if not _is_django_mode():
        return f"[独立模式] 记忆 (id={memory_id}) 已更新"
    from apps.memory.services import MemoryService

    try:
        await MemoryService.update_memory(
            memory_id=memory_id, user_id=user_id, content=content, tag=tag,
        )
        return f"记忆 (id={memory_id}) 已更新"
    except Exception as e:
        return f"更新失败: {e}"


@tool
async def mem_delete(memory_id: int, config: RunnableConfig) -> str:
    """删除指定的用户记忆。"""
    user_id = _get_user_id(config)
    if not _is_django_mode():
        return f"[独立模式] 记忆 (id={memory_id}) 已删除"
    from apps.memory.services import MemoryService

    try:
        await MemoryService.delete_memory(memory_id=memory_id, user_id=user_id)
        return f"记忆 (id={memory_id}) 已删除"
    except Exception as e:
        return f"删除失败: {e}"


MEMORY_TOOLS = [mem_search, mem_cache, mem_update, mem_delete]
