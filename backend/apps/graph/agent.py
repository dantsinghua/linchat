import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_core.messages import trim_messages
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.redis import AsyncRedisSaver
from langgraph.prebuilt import create_react_agent

from apps.common.tokenizer import count_tokens as _count_tokens
from apps.models.services import model_service
from apps.graph.multimodal import build_multimodal_messages, stream_multimodal_httpx  # noqa: F401


def _preprocess_video(video_bytes: bytes) -> bytes:
    from apps.media.services.video import preprocess_video
    max_width = getattr(settings, "VIDEO_PREPROCESS_WIDTH", 320)
    return preprocess_video(video_bytes, max_width=max_width) or video_bytes

logger = logging.getLogger(__name__)
RESPONSE_RESERVE = 4096


def _token_counter(messages) -> int:
    return sum(
        _count_tokens(m.content if hasattr(m, "content") and isinstance(m.content, str) else "")
        for m in messages
    )


def _wrap_prompt(prompt, preamble_tokens=0, effective_window=128000):
    if prompt is None: return None
    history_budget = effective_window - preamble_tokens - RESPONSE_RESERVE

    def _prompt_fn(state: dict) -> list:
        state_msgs = state.get("messages", [])
        trimmed = trim_messages(
            state_msgs, max_tokens=max(history_budget, 2000),
            token_counter=_token_counter, strategy="last",
            start_on="human", allow_partial=False,
        )
        if len(state_msgs) > 1:
            return [
                m for m in prompt
                if not (hasattr(m, "name") and m.name == "conversation_history")
            ] + list(trimmed)
        return list(prompt) + list(trimmed)

    return _prompt_fn


@asynccontextmanager
async def get_checkpointer() -> AsyncIterator[AsyncRedisSaver]:
    async with AsyncRedisSaver.from_conn_string(
        redis_url=settings.REDIS_URL,
        ttl={"default_ttl": settings.LANGGRAPH_CHECKPOINT_TTL,
             "refresh_on_read": settings.LANGGRAPH_CHECKPOINT_REFRESH_ON_READ},
    ) as checkpointer:
        yield checkpointer


def get_thread_id(user_id: int) -> str:
    return f"user_{user_id}"


async def get_llm() -> ChatOpenAI:
    config = await sync_to_async(model_service.get_active_model)("tool")
    if not config: raise RuntimeError("未找到激活的工具模型配置，请在模型配置页面设置")
    kwargs: dict = {
        "base_url": config["url"], "api_key": config["api_key"] or "not-needed",
        "model": config["name"], "streaming": True, "stream_usage": True,
        "timeout": settings.LLM_CALL_TIMEOUT, "max_retries": settings.LLM_MAX_RETRIES,
    }
    if "qwen3" in config["name"].lower():
        kwargs["extra_body"] = {"enable_thinking": False}
    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        if config.get(key) is not None: kwargs[key] = config[key]
    return ChatOpenAI(**kwargs)


_NO_TOOL_CALLING_PREFIXES = ("minicpm",)


@asynccontextmanager
async def _create_agent(
    tools, prompt=None, preamble_tokens=0, effective_window=128000,
    use_checkpointer=True, name: str = "LangGraph",
) -> AsyncIterator:
    llm = await get_llm()
    model_name = (llm.model_name or "").lower()
    if any(model_name.startswith(p) for p in _NO_TOOL_CALLING_PREFIXES):
        if tools:
            logger.info("模型 %s 不支持 tool calling，已跳过 %d 个工具", llm.model_name, len(tools))
        tools = []
    kwargs: dict = {"model": llm, "tools": tools, "name": name}
    wrapped = _wrap_prompt(prompt, preamble_tokens, effective_window)
    if wrapped: kwargs["prompt"] = wrapped
    if use_checkpointer:
        async with get_checkpointer() as checkpointer:
            kwargs["checkpointer"] = checkpointer
            yield create_react_agent(**kwargs)
    else:
        yield create_react_agent(**kwargs)


@asynccontextmanager
async def create_chat_agent(prompt=None, extra_tools=None, preamble_tokens=0, effective_window=128000):
    from apps.graph.subagents import get_subagent_tools
    async with _create_agent(
        get_subagent_tools() + (extra_tools or []), prompt, preamble_tokens,
        effective_window, use_checkpointer=False, name="chat",
    ) as agent:
        yield agent


@asynccontextmanager
async def create_context_agent(prompt=None):
    from apps.graph.tools.context import CONTEXT_TOOLS
    async with _create_agent(list(CONTEXT_TOOLS), prompt, name="context") as agent:
        yield agent


@asynccontextmanager
async def create_memory_agent(prompt=None):
    from apps.graph.tools.memory import MEMORY_TOOLS
    async with _create_agent(list(MEMORY_TOOLS), prompt, name="memory") as agent:
        yield agent


@asynccontextmanager
async def create_cronmem_agent(prompt=None):
    async with _create_agent([], prompt, name="cronmem") as agent:
        yield agent


def get_agent_config(user_id: int, callbacks: Optional[list] = None) -> dict:
    config: dict = {"configurable": {"thread_id": get_thread_id(user_id), "user_id": str(user_id)}}
    if callbacks: config["callbacks"] = callbacks
    return config
