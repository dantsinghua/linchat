"""代码执行 SubAgent — 封装 python_exec 工具的代码执行助手

通过 run_subagent() 创建内部 react agent，
自动注入公共工具（mem_search + web_search）。
"""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.graph.subagents.base import run_subagent
from apps.graph.tools.python_repl import REPL_TOOLS

CODE_PROMPT = """你是代码执行助手。根据任务描述编写并执行 Python 代码。

## 执行策略
- 编写代码前，主动用 mem_search 查询用户记忆，获取可能相关的上下文
  （如用户偏好、之前提到的数据、特定需求等）
- 如果任务涉及实时数据或不确定的信息，主动用 web_search 查询
- 使用 python_exec 工具执行代码
- 使用 print() 输出结果
- 执行失败时分析错误，可通过 web_search 查找解决方案后修正代码重试
- 返回关键代码和执行结果
- 独立完成任务，避免返回不完整的结果
- 不要返回不完整的结果或要求主 agent 补充信息"""


@tool
async def code_subagent(task: str, config: RunnableConfig) -> str:
    """执行 Python 代码进行计算、数据处理或验证。当用户需要数学计算、统计分析、数据转换或明确要求运行代码时使用。"""
    return await run_subagent(
        task, config, list(REPL_TOOLS), CODE_PROMPT, name="code_subagent"
    )
