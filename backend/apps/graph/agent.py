"""LangGraph Agent 定义

四流程工厂：chat / context / memory / cronMem
各流程工具集严格隔离 [R-018]

参考:
- specs/008-multimodal-minicpm/research.md#5 多模态消息格式
"""

import base64
import json as _json
import logging
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_core.messages import HumanMessage, trim_messages
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.redis import AsyncRedisSaver
from langgraph.prebuilt import create_react_agent

from apps.common.tokenizer import count_tokens as _count_tokens
from apps.models.services import model_service

logger = logging.getLogger(__name__)

RESPONSE_RESERVE = 4096


def _token_counter(messages) -> int:
    return sum(
        _count_tokens(
            m.content if hasattr(m, "content") and isinstance(m.content, str) else ""
        )
        for m in messages
    )


def _wrap_prompt(prompt, preamble_tokens=0, effective_window=128000):
    """将 preamble 包装为 callable(state) -> list[BaseMessage]

    优化：tool calling 循环中（state 已有 tool 消息），移除历史文本
    SystemMessage 以减少重复 token 消耗。LLM 在首次调用时已读取历史上下文，
    后续调用只需处理 tool 结果。
    """
    if prompt is None:
        return None
    history_budget = effective_window - preamble_tokens - RESPONSE_RESERVE

    def _prompt_fn(state: dict) -> list:
        state_msgs = state.get("messages", [])
        trimmed = trim_messages(
            state_msgs,
            max_tokens=max(history_budget, 2000),
            token_counter=_token_counter,
            strategy="last",
            start_on="human",
            allow_partial=False,
        )

        # tool calling 循环中移除历史文本，减少重复 token
        in_tool_loop = len(state_msgs) > 1
        if in_tool_loop:
            return [
                m
                for m in prompt
                if not (hasattr(m, "name") and m.name == "conversation_history")
            ] + list(trimmed)

        return list(prompt) + list(trimmed)

    return _prompt_fn


@asynccontextmanager
async def get_checkpointer() -> AsyncIterator[AsyncRedisSaver]:
    async with AsyncRedisSaver.from_conn_string(
        redis_url=settings.REDIS_URL,
        ttl={
            "default_ttl": settings.LANGGRAPH_CHECKPOINT_TTL,
            "refresh_on_read": settings.LANGGRAPH_CHECKPOINT_REFRESH_ON_READ,
        },
    ) as checkpointer:
        yield checkpointer


def get_thread_id(user_id: int) -> str:
    return f"user_{user_id}"


async def get_llm() -> ChatOpenAI:
    """获取 LLM 实例（每次从 DB 读取最新配置）"""
    config = await sync_to_async(model_service.get_active_model)("language")
    if not config:
        raise RuntimeError("未找到激活的语言模型配置，请在模型配置页面设置")

    kwargs: dict = {
        "base_url": config["url"],
        "api_key": config["api_key"] or "not-needed",
        "model": config["name"],
        "streaming": True,
        "stream_usage": True,
        "timeout": settings.LLM_CALL_TIMEOUT,
        "max_retries": settings.LLM_MAX_RETRIES,
    }

    if "qwen3" in config["name"].lower():
        kwargs["extra_body"] = {"enable_thinking": False}

    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        if config.get(key) is not None:
            kwargs[key] = config[key]

    return ChatOpenAI(**kwargs)


@asynccontextmanager
async def _create_agent(
    tools,
    prompt=None,
    preamble_tokens=0,
    effective_window=128000,
    use_checkpointer=True,
    name: str = "LangGraph",
) -> AsyncIterator:
    llm = await get_llm()
    kwargs: dict = {"model": llm, "tools": tools, "name": name}
    wrapped = _wrap_prompt(prompt, preamble_tokens, effective_window)
    if wrapped:
        kwargs["prompt"] = wrapped

    if use_checkpointer:
        async with get_checkpointer() as checkpointer:
            kwargs["checkpointer"] = checkpointer
            yield create_react_agent(**kwargs)
    else:
        yield create_react_agent(**kwargs)


# ============ 四流程工厂 ============


@asynccontextmanager
async def create_chat_agent(
    prompt=None, extra_tools=None, preamble_tokens=0, effective_window=128000
):
    """聊天 Agent [T053]：不使用 checkpointer 避免 ToolMessage 累积"""
    from apps.graph.subagents import get_subagent_tools

    subagent_tools = get_subagent_tools()
    async with _create_agent(
        subagent_tools + (extra_tools or []),
        prompt,
        preamble_tokens,
        effective_window,
        use_checkpointer=False,
        name="chat",
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
    config: dict = {
        "configurable": {
            "thread_id": get_thread_id(user_id),
            "user_id": user_id,
        }
    }
    if callbacks:
        config["callbacks"] = callbacks
    return config


# ============ 多模态支持 ============


def _preprocess_video(video_bytes: bytes) -> bytes:
    """预处理视频：降分辨率 + 降帧率，避免 vLLM GPU 显存溢出

    MiniCPM-o 视频限制:
    - 帧率最高 10fps（模型自动抽帧）
    - 高分辨率 + 多帧视频会导致 vLLM 内部 500 错误
    - 经测试 320x240 / 10fps 下 60 秒视频均可正常推理 (prompt_tokens=2123)

    处理: 缩放到 max_width 像素宽、帧率 10fps、H.264 yuv420p、去除音轨。
    如果 ffmpeg 处理失败，返回原始字节（降级处理）。

    Args:
        video_bytes: 原始视频字节

    Returns:
        预处理后的 MP4 字节，失败时返回原始字节
    """
    max_width = getattr(settings, "VIDEO_PREPROCESS_WIDTH", 320)

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input.mp4")
        output_path = os.path.join(tmpdir, "output.mp4")
        with open(input_path, "wb") as f:
            f.write(video_bytes)

        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", input_path,
                    "-vf", f"scale='min({max_width},iw)':-2",
                    "-r", "10",  # 降帧率到 10fps (MiniCPM-o 最高支持)
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-an",  # 去除音频轨（减小体积）
                    output_path,
                ],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and os.path.exists(output_path):
                with open(output_path, "rb") as f:
                    processed = f.read()
                logger.info(
                    f"视频预处理完成: {len(video_bytes)}B -> {len(processed)}B "
                    f"(max_width={max_width}, fps=10)"
                )
                return processed
            else:
                logger.warning(f"ffmpeg 预处理失败: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg 视频预处理超时")
        except Exception as e:
            logger.warning(f"视频预处理异常: {e}")

    logger.warning("视频预处理失败，使用原始视频（可能导致推理错误）")
    return video_bytes


def build_multimodal_messages(
    user_message: str,
    attachments: list[Any],
) -> tuple[HumanMessage, str, list[str]]:
    """构建多模态消息

    将用户消息和附件转换为 OpenAI 兼容的多模态消息格式。

    Args:
        user_message: 用户文本消息
        attachments: MediaAttachment 对象列表

    Returns:
        (HumanMessage, model_name, media_types)
        - HumanMessage: 包含文本和媒体的消息
        - model_name: 推荐使用的模型名称
        - media_types: 包含的媒体类型列表

    参考: specs/008-multimodal-minicpm/research.md#5 多模态消息格式
    """
    from apps.chat.services.minio_service import minio_service

    if not attachments:
        # 无附件，返回纯文本消息
        return HumanMessage(content=user_message), "", []

    content: list[dict[str, Any]] = []
    media_types: list[str] = []
    has_audio = any(a.media_type == "audio" for a in attachments)

    # 占位文本处理 (T053/US5-AC3):
    # 仅当携带 audio 附件时，content 为"[语音消息]"替换为空字符串仅传音频
    # 有用户追加文字则保留文本与音频一同传入
    # 无 audio 附件时即使 content 恰好为"[语音消息]"也保留原文
    effective_message = user_message
    if has_audio and user_message == "[语音消息]":
        effective_message = ""

    # 添加文本内容
    if effective_message:
        content.append({"type": "text", "text": effective_message})

    # 处理附件
    for attachment in attachments:
        media_type = attachment.media_type
        if media_type not in media_types:
            media_types.append(media_type)

        try:
            # 从 MinIO 获取文件内容
            file_bytes = minio_service.download_file(
                bucket=settings.MINIO_BUCKET_MEDIA,
                object_name=attachment.storage_path,
            )
            base64_data = base64.b64encode(file_bytes).decode("utf-8")

            if media_type == "image":
                # 图片消息
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{attachment.mime_type};base64,{base64_data}",
                    },
                })
            elif media_type == "video":
                # 视频消息: 预处理后以 video_url base64 格式发送
                # MiniCPM-o 限制: 高分辨率+多帧视频会导致 vLLM 500 错误
                # 预处理: 降分辨率到 320px + 降帧率到 10fps
                processed_bytes = _preprocess_video(file_bytes)
                video_b64 = base64.b64encode(processed_bytes).decode("utf-8")
                content.append({
                    "type": "video_url",
                    "video_url": {
                        "url": f"data:video/mp4;base64,{video_b64}",
                    },
                })
            elif media_type == "audio":
                # 音频消息（MiniCPM-o 支持）
                content.append({
                    "type": "audio_url",
                    "audio_url": {
                        "url": f"data:{attachment.mime_type};base64,{base64_data}",
                    },
                })

        except Exception as e:
            logger.warning(f"处理附件失败: uuid={attachment.attachment_uuid}, error={e}")
            # 附件处理失败，添加错误提示
            content.append({
                "type": "text",
                "text": f"[附件加载失败: {attachment.file_name}]",
            })

    # 选择模型
    if has_audio:
        model_name = getattr(settings, "MULTIMODAL_MODEL_AUDIO", "minicpm-o")
    else:
        model_name = getattr(settings, "MULTIMODAL_MODEL_VISION", "minicpm-o")

    return HumanMessage(content=content), model_name, media_types


async def get_multimodal_llm(model_name: str) -> ChatOpenAI:
    """获取多模态 LLM 实例

    Args:
        model_name: 模型名称 (minicpm-v / minicpm-o)

    Returns:
        ChatOpenAI 实例
    """
    gateway_url = getattr(settings, "LLM_GATEWAY_URL", "")
    gateway_timeout = getattr(settings, "LLM_GATEWAY_INFERENCE_TIMEOUT", 180)

    if not gateway_url:
        raise RuntimeError("未配置 LLM_GATEWAY_URL，无法使用多模态功能")

    return ChatOpenAI(
        base_url=f"{gateway_url}/v1",
        api_key=getattr(settings, "LLM_GATEWAY_API_KEY", "") or "not-needed",
        model=model_name,
        streaming=True,
        stream_usage=True,
        timeout=gateway_timeout,
        max_retries=settings.LLM_MAX_RETRIES,
    )


@asynccontextmanager
async def create_multimodal_agent(
    model_name: str,
    prompt=None,
    preamble_tokens=0,
    effective_window=128000,
):
    """创建多模态 Agent（已弃用，仅保留向后兼容）

    注意: LangChain ChatOpenAI 不支持 video_url / audio_url 等 MiniCPM 扩展类型，
    会导致序列化错误。请改用 create_multimodal_direct()。

    Args:
        model_name: 模型名称 (minicpm-v / minicpm-o)
        prompt: 前置 prompt
        preamble_tokens: 前置 token 数
        effective_window: 有效上下文窗口

    参考: specs/008-multimodal-minicpm/plan.md
    """
    llm = await get_multimodal_llm(model_name)
    wrapped = _wrap_prompt(prompt, preamble_tokens, effective_window)

    kwargs: dict = {
        "model": llm,
        "tools": [],  # 多模态 Agent 暂不使用工具
        "name": f"multimodal_{model_name}",
    }
    if wrapped:
        kwargs["prompt"] = wrapped

    yield create_react_agent(**kwargs)


# ============ 多模态直接推理（绕过 LangChain 序列化） ============


async def stream_multimodal_httpx(
    content: list[dict[str, Any]],
    model_name: str,
    system_prompt: str = "",
) -> AsyncIterator[tuple[str, Optional[dict[str, int]]]]:
    """直接通过 httpx 调用 LLM Gateway 流式推理，绕过 LangChain 序列化问题

    LangChain ChatOpenAI / OpenAI Python SDK 不识别 video_url / audio_url 等
    MiniCPM 扩展内容类型，会将 content 数组元素序列化为 Python repr 字符串
    （如 "ContentPart(type='video_url', ...)"）而非 JSON 对象。
    此函数直接构建 JSON 请求体，确保非标准内容类型正确序列化。

    Args:
        content: 多模态内容列表（来自 build_multimodal_messages().content）
        model_name: 模型名称 (如 minicpm-o)
        system_prompt: 系统提示词

    Yields:
        (content_delta, usage):
        - content_delta: 生成的文本片段（可能为空字符串）
        - usage: token 用量字典，仅最后一个 chunk 时非 None
    """
    import httpx

    gateway_url = getattr(settings, "LLM_GATEWAY_URL", "")
    gateway_key = getattr(settings, "LLM_GATEWAY_API_KEY", "") or "not-needed"
    gateway_timeout = getattr(settings, "LLM_GATEWAY_INFERENCE_TIMEOUT", 180)

    if not gateway_url:
        raise RuntimeError("未配置 LLM_GATEWAY_URL，无法使用多模态功能")

    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(gateway_timeout, connect=30.0),
    ) as client:
        async with client.stream(
            "POST",
            f"{gateway_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {gateway_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_name,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
                "max_tokens": 4096,
            },
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise RuntimeError(
                    f"Gateway HTTP {response.status_code}: "
                    f"{body.decode('utf-8', errors='replace')[:500]}"
                )

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = _json.loads(data_str)
                    choices = chunk.get("choices", [])
                    delta_content = ""
                    if choices:
                        delta_content = (
                            choices[0].get("delta", {}).get("content", "") or ""
                        )
                    usage = chunk.get("usage")
                    if delta_content or usage:
                        yield delta_content, usage
                except _json.JSONDecodeError:
                    logger.warning("多模态 SSE JSON 解析失败: %s", data_str[:100])


class _DirectContent:
    """httpx 适配器：模拟 LangChain chunk.content"""

    __slots__ = ("content",)

    def __init__(self, text: str):
        self.content = text


class _DirectUsage:
    """httpx 适配器：模拟 LangChain output.usage_metadata"""

    __slots__ = ("usage_metadata", "response_metadata")

    def __init__(self, usage_dict: dict):
        self.usage_metadata = {
            "input_tokens": usage_dict.get("prompt_tokens", 0),
            "output_tokens": usage_dict.get("completion_tokens", 0),
        }
        self.response_metadata = None


@asynccontextmanager
async def create_multimodal_direct(
    content: list[dict[str, Any]],
    model_name: str,
    system_prompt: str = "",
) -> AsyncIterator:
    """创建直接 httpx 多模态推理适配器（兼容 LangGraph astream_events 接口）

    通过 httpx 直接调用 LLM Gateway，绕过 LangChain ChatOpenAI 的序列化，
    同时保持与 LangGraph agent.astream_events() 相同的事件接口。

    用法与 create_multimodal_agent 相同:
        async with create_multimodal_direct(content, model) as agent:
            async for event in agent.astream_events(
                input_msg, config=config, version="v2"
            ):
                ...

    Args:
        content: 多模态内容列表（来自 build_multimodal_messages().content）
        model_name: 模型名称 (如 minicpm-o)
        system_prompt: 系统提示词
    """

    class _Adapter:
        async def astream_events(self, input_message, config=None, version=None):
            async for delta, usage in stream_multimodal_httpx(
                content, model_name, system_prompt
            ):
                if delta:
                    yield {
                        "event": "on_chat_model_stream",
                        "data": {"chunk": _DirectContent(delta)},
                        "parent_ids": [],
                    }
                if usage:
                    yield {
                        "event": "on_chat_model_end",
                        "data": {"output": _DirectUsage(usage)},
                    }

    yield _Adapter()
