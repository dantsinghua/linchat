"""记忆 SubAgent — 封装 mem_* 工具的记忆管理助手

通过 run_subagent() 创建内部 react agent，
自动注入公共工具（web_search，mem_search 已在 MEMORY_TOOLS 中，去重跳过）。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.graph.subagents.base import run_subagent
from apps.graph.tools.memory import MEMORY_TOOLS

MEMORY_PROMPT = """你是记忆管理助手。根据任务描述管理用户的长期记忆。

## 工具
- mem_search: 搜索记忆，返回 [id=<memory_id>] <内容>
- mem_cache: 保存新记忆。必须提供语义标签 tag（如"个人喜好"/"职业信息"/"工作任务"/"日常对话"等）
- mem_update: 更新记忆（需要 memory_id）
- mem_delete: 删除记忆（需要 memory_id）

## 执行策略
- 保存前必须先 mem_search 搜索去重：
  - 如果找到相似记忆 → 用 mem_update 更新，而不是创建新的
  - 如果没有相似记忆 → 用 mem_cache 创建新记忆
- 保存内容应为精炼的事实性信息，而不是对话原文
- 更新/删除前必须先搜索获取 memory_id
- 更新 vs 删除：
  - 用户要求修改/纠正信息 → 用 mem_update 更新
  - 用户要求忘记/删除/移除 → 用 mem_delete 删除
  - 部分内容不需要 → 用 mem_update 保留剩余
  - 全部不需要 → 用 mem_delete 删除
- 如需验证信息准确性，可使用 web_search 搜索确认
- 独立完成任务，返回操作结果和确认信息
- 不要返回不完整的结果或要求主 agent 补充信息"""


@tool
async def memory_subagent(task: str, config: RunnableConfig) -> str:
    """管理用户的长期记忆。当用户要求记住、回忆、更新或删除个人信息时使用。"""
    return await run_subagent(
        task, config, list(MEMORY_TOOLS), MEMORY_PROMPT, name="memory_subagent"
    )
