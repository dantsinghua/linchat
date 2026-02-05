"""搜索 SubAgent — 封装 web_search 工具的搜索助手

通过 run_subagent() 创建内部 react agent，自动注入公共工具（mem_search）。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.graph.subagents.base import run_subagent
from apps.graph.tools.search import SEARCH_TOOLS

SEARCH_PROMPT = """你是搜索助手。根据任务描述搜索互联网获取信息。

## 执行策略
- 搜索前先用 mem_search 查询用户记忆，了解用户背景以优化搜索策略
- 使用 web_search 工具进行搜索
- 搜索结果按编号返回，整合时用 [[N]] 标注引用来源
- 回答末尾附上引文列表，格式：
  **参考来源：**
  1. [标题](url)
  2. [标题](url)
- 如果首次搜索无结果，调整关键词重新搜索
- 独立完成任务，返回完整的搜索整合结果
- 不要返回不完整的结果或要求主 agent 补充信息"""


@tool
async def search_subagent(task: str, config: RunnableConfig) -> str:
    """搜索互联网获取最新信息。当用户需要实时资讯（新闻、天气、股价、技术动态等）或需要查找特定网址、文档时使用。"""
    return await run_subagent(
        task, config, list(SEARCH_TOOLS), SEARCH_PROMPT, name="search_subagent"
    )
