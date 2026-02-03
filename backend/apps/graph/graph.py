"""
独立 Graph 定义 — langgraph dev 入口

用法:
    cd backend
    langgraph dev    # 自动读取 langgraph.json

双模式设计：
- Django 系统内运行时，tools/memory.py 和 tools/context.py 自动检测 Django 环境，
  调用真实服务。
- langgraph dev 独立运行时，工具自动降级为 Mock 模式。

LLM 通过环境变量配置（从 .env 读取），Langfuse 由 LangChain 集成自动 trace。
"""

import os

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from apps.graph.tools.context import CONTEXT_TOOLS
from apps.graph.tools.memory import MEMORY_TOOLS


# ======== LLM（环境变量配置）========


def _get_llm() -> ChatOpenAI:
    """从环境变量创建 LLM 实例（独立模式专用）"""
    return ChatOpenAI(
        base_url=os.environ.get("LLM_API_BASE", "https://ark.cn-beijing.volces.com/api/v3"),
        api_key=os.environ.get("LLM_API_KEY", "not-needed"),
        model=os.environ.get("LLM_MODEL_NAME", "deepseek-v3-1-terminus"),
        streaming=True,
        stream_usage=True,
    )


# ======== System Prompt ========

STANDALONE_SYSTEM_PROMPT = "你是 LinChat 智能助手。当前为独立调试模式。"


# ======== 4 个 Graph 定义 ========

_llm = _get_llm()

chat_graph = create_react_agent(
    model=_llm,
    tools=list(MEMORY_TOOLS),
    prompt=STANDALONE_SYSTEM_PROMPT,
)

context_graph = create_react_agent(
    model=_llm,
    tools=list(CONTEXT_TOOLS),
    prompt=STANDALONE_SYSTEM_PROMPT,
)

memory_graph = create_react_agent(
    model=_llm,
    tools=list(MEMORY_TOOLS),
    prompt=STANDALONE_SYSTEM_PROMPT,
)

cronmem_graph = create_react_agent(
    model=_llm,
    tools=[],
    prompt=STANDALONE_SYSTEM_PROMPT,
)
