"""Agent 执行服务

参考:
- behavior-model.md#2.2 执行LangGraph Agent（B_CHAT_002）
- behavior-model.md#2.5 继续生成（B_CHAT_005）
- specs/008-multimodal-minicpm/plan.md
"""

import asyncio
import logging
import os
import time
import uuid
from typing import Any, AsyncGenerator, Optional

from django.conf import settings
from django.utils import timezone
from langchain_core.messages import HumanMessage
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

from apps.chat.models import LangGraphExecution, MediaAttachment, Message
from apps.chat.repositories import execution_repo, media_attachment_repo, message_repo
from apps.chat.services.generation import (map_llm_exception,
                                           register_generation,
                                           unregister_generation)
from apps.chat.services.inference_service import inference_service
from apps.chat.services.types import StreamChunk, _get_language_model_name
from apps.common.exceptions import (LLMException, LLMInvalidResponseError,
                                    LLMTimeoutError)
from apps.graph.agent import (build_multimodal_messages, create_chat_agent,
                              create_multimodal_direct, get_agent_config)
from apps.graph.prompts import PromptBuilder, PromptConfig, PromptModule
from apps.users.repositories import user_repo

logger = logging.getLogger(__name__)


async def _monitor_cancel_signal(
    user_id: int,
    request_id: str,
    stop_event: asyncio.Event,
) -> None:
    """后台监听推理取消信号 (T035)

    优先使用 Redis Pub/Sub 监听 INFERENCE_CANCEL 事件，
    Pub/Sub 连接异常时降级为轮询 Redis inference_task 键。

    Args:
        user_id: 用户 ID
        request_id: 请求 ID
        stop_event: 停止事件（设置后 SSE 循环中断）
    """
    import json

    from core.redis import get_redis, get_user_events_channel

    try:
        client = await get_redis()
        pubsub = client.pubsub()
        channel = get_user_events_channel(user_id)

        await pubsub.subscribe(channel)
        try:
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=1.0,
                    )
                    if message and message["type"] == "message":
                        data_str = message["data"]
                        if isinstance(data_str, bytes):
                            data_str = data_str.decode("utf-8")
                        # 解析 SSE 格式中的 data 行
                        for line in data_str.split("\n"):
                            if line.startswith("data: "):
                                try:
                                    event_data = json.loads(line[6:])
                                    if event_data.get("type") == "inference_cancel":
                                        cancel_rid = event_data.get("request_id")
                                        if not cancel_rid or cancel_rid == request_id:
                                            logger.info(
                                                "收到推理取消信号 (Pub/Sub): "
                                                "user_id=%d, request_id=%s",
                                                user_id,
                                                request_id,
                                            )
                                            stop_event.set()
                                            return
                                except json.JSONDecodeError:
                                    pass
                except asyncio.TimeoutError:
                    continue
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    except Exception as e:
        # Pub/Sub 失败，降级为轮询 Redis 推理任务键
        logger.warning(
            "Pub/Sub 订阅失败，降级为轮询: user_id=%d, error=%s",
            user_id,
            e,
        )
        await _poll_cancel_signal(user_id, request_id, stop_event)


async def _poll_cancel_signal(
    user_id: int,
    request_id: str,
    stop_event: asyncio.Event,
) -> None:
    """降级轮询：检查 Redis 推理任务键是否被删除 (T035)

    cancel_task() 步骤 1 删除键后，轮询检测到键不存在即视为取消信号。
    轮询间隔 1 秒，与 Pub/Sub 超时对齐。
    """
    from core.redis import get_redis

    key = f"user:{user_id}:inference_task"

    try:
        client = await get_redis()
        while not stop_event.is_set():
            task_data = await client.get(key)
            if task_data is None:
                logger.info(
                    "收到推理取消信号 (轮询): user_id=%d, request_id=%s",
                    user_id,
                    request_id,
                )
                stop_event.set()
                return
            await asyncio.sleep(1.0)
    except Exception as e:
        logger.error(
            "取消信号轮询失败: user_id=%d, error=%s", user_id, e
        )


async def _build_prompt_preamble(
    user_id: int,
    user_message: str = "",
) -> tuple[list, int, int, "TokenBreakdown", list, str, int]:
    """构建 Agent 前置 prompt 消息列表

    Returns:
        (preamble, preamble_tokens, effective_window, breakdown,
         memory_results, model_name, max_context_window)
    """
    from asgiref.sync import sync_to_async

    from apps.common.tokenizer import count_tokens
    from apps.context.types import TokenBreakdown
    from apps.models.services import model_service

    config_data = await sync_to_async(model_service.get_active_model)("language")
    max_context_window = (
        config_data.get("max_context_window", 128000) if config_data else 128000
    )
    model_name = config_data.get("name", "unknown") if config_data else "unknown"

    prompt_config = PromptConfig(user_id=user_id, max_context_window=max_context_window)
    builder = PromptBuilder(config=prompt_config)

    # 记忆召回
    retrieved_memories = None
    memory_results: list = []
    if user_message:
        try:
            from apps.graph.prompts import RetrievedMemory
            from apps.memory.services import MemoryService

            results = await MemoryService.search_memory(
                user_id=user_id,
                query=user_message,
                limit=settings.MEMORY_SEARCH_TOP_K,
                skip_vector=False,
            )
            if results:
                memory_results = results
                retrieved_memories = [
                    RetrievedMemory(
                        content=r["memory"].content,
                        memory_type=r["memory"].type,
                        relevance_score=r["score"],
                    )
                    for r in results
                ]
        except Exception as e:
            logger.warning("Memory recall failed for user %d: %s", user_id, e)

    # 先构建不含历史的 preamble，计算 fixed_tokens
    preamble_no_history = builder.build_preamble(retrieved_memories=retrieved_memories)

    fixed_tokens = sum(
        count_tokens(m.content if hasattr(m, "content") else str(m))
        for m in preamble_no_history
    )

    # 从 DB 拉取对话历史，转为 dict 列表
    history_msgs = await message_repo.find_latest_by_user(user_id, limit=50)
    history_msgs.reverse()
    history_dicts: list[dict[str, str]] = []
    for m in history_msgs:
        if m.role == "user" and m.content:
            history_dicts.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            content = (m.content or "").removesuffix("[已中断]")
            if content:
                history_dicts.append({"role": "assistant", "content": content})

    # 按 token 预算从前向后裁剪（保证从 user 消息开始）
    history_budget = max(max_context_window - fixed_tokens - 4096, 2000)
    trimmed: list[dict[str, str]] = []
    used_tokens = 0
    for msg in reversed(history_dicts):
        msg_tokens = count_tokens(msg["content"])
        if used_tokens + msg_tokens > history_budget:
            break
        trimmed.append(msg)
        used_tokens += msg_tokens
    trimmed.reverse()
    while trimmed and trimmed[0]["role"] != "user":
        trimmed.pop(0)

    # 使用 build_preamble_with_breakdown 获取带 breakdown 的 preamble
    preamble, breakdown = builder.build_preamble_with_breakdown(
        user_input=user_message,
        retrieved_memories=retrieved_memories,
        conversation_history=trimmed if trimmed else None,
    )
    preamble_tokens = breakdown.total - breakdown.user_input

    logger.debug(
        "Built preamble for user %d: preamble_tokens=%d, breakdown=%s",
        user_id,
        preamble_tokens,
        breakdown.to_dict(),
    )
    return (
        preamble,
        preamble_tokens,
        prompt_config.effective_window,
        breakdown,
        memory_results,
        model_name,
        max_context_window,
    )


def _init_langfuse(
    request_id: str,
    multimodal_metadata: Optional[dict] = None,
) -> Optional[LangfuseCallbackHandler]:
    """初始化 Langfuse 追踪

    Args:
        request_id: 请求 ID（用于 trace_id 关联）
        multimodal_metadata: 多模态推理元数据（model、media_types、attachment_count）
    """
    try:
        if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
            return None
        for key, val in [
            ("LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY),
            ("LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY),
            ("LANGFUSE_HOST", settings.LANGFUSE_HOST),
        ]:
            os.environ.setdefault(key, val)
        handler = LangfuseCallbackHandler(
            trace_context={"trace_id": request_id},
        )

        # T068: 多模态推理元数据注入到 Langfuse trace
        if multimodal_metadata and handler.client:
            try:
                handler.client.trace(
                    id=request_id,
                    metadata=multimodal_metadata,
                    tags=["multimodal"] + multimodal_metadata.get("media_types", []),
                )
            except Exception as e:
                logger.debug("Langfuse multimodal metadata injection: %s", e)

        return handler
    except Exception as e:
        logger.warning("Langfuse init failed: %s", e)
        return None


def _extract_usage(output: object) -> tuple[int, int]:
    """从 LLM 输出中提取 token 用量 (prompt_tokens, completion_tokens)"""
    if hasattr(output, "usage_metadata") and output.usage_metadata:
        u = output.usage_metadata
        return u.get("input_tokens", 0), u.get("output_tokens", 0)
    if hasattr(output, "response_metadata") and output.response_metadata:
        u = output.response_metadata.get("token_usage", {})
        return u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    return 0, 0


def _finalize_message(
    msg, content, status, duration_ms, prompt_tokens, completion_tokens
):
    msg.content = content
    msg.status = status
    msg.response_time_ms = duration_ms
    msg.prompt_tokens = prompt_tokens
    msg.completion_tokens = completion_tokens


def _finalize_execution(
    execution,
    status,
    end_time,
    duration_ms,
    output_data=None,
    total_prompt_tokens=0,
    total_completion_tokens=0,
    langfuse_handler=None,
    error_type=None,
    error_message=None,
):
    execution.status = status
    execution.end_time = end_time
    execution.duration_ms = duration_ms
    if output_data:
        execution.output_data = output_data
    if total_prompt_tokens:
        execution.total_prompt_tokens = total_prompt_tokens
    if total_completion_tokens:
        execution.total_completion_tokens = total_completion_tokens
    if langfuse_handler and hasattr(langfuse_handler, "trace_id"):
        execution.langfuse_trace_id = langfuse_handler.trace_id
    if error_type:
        execution.error_type = error_type
    if error_message:
        execution.error_message = error_message


def _extract_gateway_error(
    e: Exception,
) -> Optional[tuple[str, str, Optional[int]]]:
    """从异常中提取 Gateway 模型错误信息 (T079)

    解析 OpenAI SDK / httpx 异常中的 Gateway 错误码。

    Returns:
        (error_code, user_message, retry_after) 或 None
        - E3001 → ("E3001", "请求的模型不存在", None)
        - E3002 → ("E3002", "多模态服务暂时不可用，请稍后重试", retry_after)
    """
    import json as _json

    # 提取错误体：openai.APIStatusError 携带 response.text
    error_body = None
    try:
        if hasattr(e, "response") and hasattr(e.response, "text"):
            error_body = _json.loads(e.response.text)
        elif hasattr(e, "body") and isinstance(e.body, dict):
            error_body = e.body
    except Exception:
        pass

    if not error_body:
        # 从异常消息中尝试提取
        error_str = str(e)
        if "E3001" in error_str:
            return "E3001", "请求的模型不存在", None
        if "E3002" in error_str:
            return "E3002", "多模态服务暂时不可用，请稍后重试", None
        return None

    error_info = error_body.get("error", {})
    error_code = error_info.get("code", "")
    details = error_info.get("details", {})

    if error_code == "E3001":
        return "E3001", "请求的模型不存在", None
    elif error_code == "E3002":
        retry_after = details.get("retry_after")
        return "E3002", "多模态服务暂时不可用，请稍后重试", retry_after

    return None


def _extract_content_control(e: Exception) -> Optional[str]:
    """从异常中提取 Gateway content_control 事件信息 (T035)

    当 Gateway 安全护栏触发时，发送 `event: content_control` SSE 事件，
    OpenAI SDK 解析此非标准事件时可能抛出异常。

    Returns:
        replacement 文本，或 None（非 content_control 异常）
    """
    import json as _json

    error_str = str(e)

    # 检查异常消息中是否包含 content_control 相关信息
    if "content_control" in error_str:
        # 尝试从异常消息中提取 replacement 文本
        try:
            # 可能是 JSON 解析错误，包含原始 data
            for part in error_str.split("data:"):
                part = part.strip()
                if part.startswith("{") and "content_control" in part:
                    data = _json.loads(part.split("\n")[0])
                    return data.get("replacement", "内容已被安全策略过滤")
        except Exception:
            pass
        return "内容已被安全策略过滤"

    # 检查异常体中的 content_control 信息
    if hasattr(e, "body") and isinstance(e.body, dict):
        if e.body.get("type") == "clear_previous":
            return e.body.get("replacement", "内容已被安全策略过滤")

    # 检查异常 response 体
    try:
        if hasattr(e, "response") and hasattr(e.response, "text"):
            text = e.response.text
            if "content_control" in text or "clear_previous" in text:
                data = _json.loads(text)
                return data.get("replacement", "内容已被安全策略过滤")
    except Exception:
        pass

    return None


class AgentService:
    """Agent 执行服务"""

    @staticmethod
    async def execute(
        user_id: int,
        thread_id: str,
        request_id: str,
        user_message: str,
        attachment_uuids: Optional[list[str]] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """执行 Agent（支持多模态）

        Args:
            user_id: 用户 ID
            thread_id: 线程 ID
            request_id: 请求 ID
            user_message: 用户消息
            attachment_uuids: 附件 UUID 列表（多模态支持）

        Yields:
            StreamChunk: 流式响应块
        """
        execution_uuid = str(uuid.uuid4())
        start_time = timezone.now()
        stop_event = register_generation(request_id)

        # 多模态：获取附件并注册推理任务
        attachments: list[MediaAttachment] = []
        is_multimodal = False
        multimodal_model = ""
        media_types: list[str] = []

        if attachment_uuids:
            attachments = await media_attachment_repo.get_by_uuids(
                attachment_uuids, user_id
            )
            if attachments:
                is_multimodal = True
                # 检查附件是否过期
                for att in attachments:
                    if att.is_expired:
                        yield StreamChunk(
                            type="error",
                            content=f"附件已过期: {att.file_name}",
                        )
                        return

                # 构建多模态消息获取模型信息
                _, multimodal_model, media_types = build_multimodal_messages(
                    user_message, attachments
                )

                # 注册推理任务（并发控制）
                # 注意：视图层已做并发检查并返回 HTTP 409，这里是竞态条件保护
                registered = await inference_service.register_task(
                    user_id=user_id,
                    request_id=request_id,
                    model=multimodal_model,
                    media_types=media_types,
                )
                if not registered:
                    # 竞态条件：视图层检查后、注册前有其他请求抢占
                    logger.warning(
                        f"推理任务注册失败（竞态条件）: user_id={user_id}, request_id={request_id}"
                    )
                    yield StreamChunk(
                        type="error",
                        content="推理任务冲突，请稍后重试",
                    )
                    return

        # 构建 execution input_data（含多模态元数据 T068）
        input_data: dict[str, Any] = {"message": user_message}
        if is_multimodal:
            input_data["multimodal"] = {
                "model": multimodal_model,
                "media_types": media_types,
                "attachment_count": len(attachments),
            }

        execution = LangGraphExecution(
            execution_uuid=execution_uuid,
            request_id=request_id,
            user_id=user_id,
            thread_id=thread_id,
            graph_name="react_agent",
            status=LangGraphExecution.STATUS_PENDING,
            start_time=start_time,
            input_data=input_data,
        )
        await execution_repo.create(execution)

        full_response = ""
        total_prompt_tokens = 0
        total_completion_tokens = 0
        user_msg: Optional[Message] = None
        assistant_msg: Optional[Message] = None
        interrupted = False
        first_token_received = False
        first_token_time = None
        max_seq: Optional[int] = None
        cancel_monitor_task: Optional[asyncio.Task] = None

        try:
            # T035: 多模态请求启动后台取消信号监听
            if is_multimodal:
                cancel_monitor_task = asyncio.create_task(
                    _monitor_cancel_signal(user_id, request_id, stop_event)
                )
            # T068: 初始化 Langfuse（多模态推理包含元数据）
            multimodal_meta = (
                {
                    "model": multimodal_model,
                    "media_types": media_types,
                    "attachment_count": len(attachments),
                    "gateway_request_id": request_id,
                }
                if is_multimodal
                else None
            )
            langfuse_handler = _init_langfuse(request_id, multimodal_meta)
            max_seq = await message_repo.get_max_sequence(user_id)
            execution.status = LangGraphExecution.STATUS_RUNNING
            await execution_repo.update(execution)

            callbacks = [langfuse_handler] if langfuse_handler else None
            config = get_agent_config(user_id, callbacks)

            # 构建输入消息（支持多模态）
            if is_multimodal and attachments:
                mm_message, _, _ = build_multimodal_messages(user_message, attachments)
                input_message = {"messages": [mm_message]}
            else:
                input_message = {"messages": [HumanMessage(content=user_message)]}
            (
                preamble,
                preamble_tokens,
                effective_window,
                breakdown,
                memory_results,
                model_name,
                max_context_window,
            ) = await _build_prompt_preamble(user_id, user_message)

            # 监控初始化 [005-context-monitoring]
            monitor_data: Optional[dict[str, Any]] = None
            tool_processes: list[dict[str, Any]] = []
            last_alert = None
            try:
                from apps.common.event_service import EventService
                from apps.context.monitoring import ContextMonitor

                monitor_data = ContextMonitor.build_monitor_data(
                    breakdown=breakdown,
                    max_tokens=max_context_window,
                    model_name=model_name,
                    memory_results=memory_results,
                    tool_processes=tool_processes,
                )
                monitor_data["request_id"] = request_id
                last_alert = monitor_data["alert"]
                await EventService.publish_event(
                    user_id,
                    "context_status",
                    monitor_data,
                )
            except Exception as e:
                logger.warning("Monitor init failed: %s", e)

            # 上下文压缩检测 [T069]
            try:
                from apps.chat.services.context_service import ContextService

                history_msgs = await message_repo.find_latest_by_user(user_id, limit=50)
                context_messages = [
                    {"role": m.role, "content": m.content} for m in history_msgs
                ]
                context_messages.append({"role": "user", "content": user_message})

                if ContextService.check_token_limit(context_messages, effective_window):
                    yield StreamChunk(type="context_compacting", content="")
                    await ContextService.compress_context(
                        user_id=user_id,
                        messages=context_messages,
                        effective_window=effective_window,
                    )
                    yield StreamChunk(type="context_compacted", content="")
            except Exception as e:
                logger.warning("Context compression check failed: %s", e)

            # 流式执行
            last_push_time = time.monotonic()
            push_interval = getattr(settings, "MONITOR_PUSH_INTERVAL", 0.5)
            memory_modified = False

            # 选择 Agent 类型
            if is_multimodal:
                # 直接 httpx 调用 Gateway，绕过 LangChain video_url/audio_url 序列化问题
                # LangChain ChatOpenAI 会将非标准内容类型序列化为 Python repr 字符串
                system_prompt = "\n\n".join(
                    m.content
                    for m in preamble
                    if hasattr(m, "content") and isinstance(m.content, str)
                )
                agent_context = create_multimodal_direct(
                    content=mm_message.content,
                    model_name=multimodal_model,
                    system_prompt=system_prompt,
                )
            else:
                agent_context = create_chat_agent(
                    prompt=preamble,
                    preamble_tokens=preamble_tokens,
                    effective_window=effective_window,
                )

            async with agent_context as agent:
                try:
                    async with asyncio.timeout(settings.AGENT_TOTAL_TIMEOUT):
                        async for event in agent.astream_events(
                            input_message, config=config, version="v2"
                        ):
                            if stop_event.is_set():
                                interrupted = True
                                break

                            if event["event"] == "on_chat_model_stream":
                                # SubAgent 内部 LLM 输出过滤：
                                # 主 agent 事件 parent_ids depth <= 3，
                                # SubAgent 内部事件 depth > 3
                                if len(event.get("parent_ids", [])) > 3:
                                    continue

                                chunk = event["data"]["chunk"]
                                if hasattr(chunk, "content") and chunk.content:
                                    if not first_token_received:
                                        first_token_received = True
                                        first_token_time = timezone.now()
                                        user_msg = Message(
                                            message_uuid=str(uuid.uuid4()),
                                            user_id=user_id,
                                            role=Message.ROLE_USER,
                                            content=user_message,
                                            request_id=request_id,
                                            sequence=max_seq + 1,
                                            status=Message.STATUS_NORMAL,
                                            created_time=start_time,
                                        )
                                        await message_repo.create(user_msg)
                                        # 多模态时使用多模态模型名称
                                        msg_model_name = (
                                            multimodal_model
                                            if is_multimodal
                                            else await _get_language_model_name()
                                        )
                                        assistant_msg = Message(
                                            message_uuid=str(uuid.uuid4()),
                                            user_id=user_id,
                                            role=Message.ROLE_ASSISTANT,
                                            content="",
                                            request_id=request_id,
                                            sequence=max_seq + 2,
                                            status=Message.STATUS_GENERATING,
                                            model_name=msg_model_name,
                                            created_time=first_token_time,
                                        )
                                        await message_repo.create(assistant_msg)

                                        # 多模态：关联附件到用户消息
                                        if is_multimodal and attachment_uuids:
                                            await media_attachment_repo.associate_message(
                                                attachment_ids=[a.attachment_id for a in attachments],
                                                message_id=user_msg.message_id,
                                                user_id=user_id,
                                            )

                                    full_response += chunk.content
                                    chunk_data = StreamChunk(
                                        type="content",
                                        content=chunk.content,
                                        message_id=(
                                            assistant_msg.message_id
                                            if assistant_msg
                                            else None
                                        ),
                                    )
                                    if len(full_response) == len(chunk.content):
                                        chunk_data.request_id = request_id
                                    yield chunk_data

                            elif event.get("event") == "on_chat_model_end":
                                output = event.get("data", {}).get("output")
                                if output:
                                    pt, ct = _extract_usage(output)
                                    total_prompt_tokens += pt
                                    total_completion_tokens += ct

                            elif event.get("event") == "on_tool_end":
                                # 追踪工具调用 [005-context-monitoring]
                                try:
                                    from apps.common.tokenizer import \
                                        count_tokens

                                    tool_name = event.get("name", "unknown")
                                    if tool_name == "memory_subagent":
                                        memory_modified = True
                                    tool_output = str(
                                        event.get("data", {}).get("output", "")
                                    )
                                    tool_input = str(
                                        event.get("data", {}).get("input", "")
                                    )
                                    t_in = count_tokens(tool_input)
                                    t_out = count_tokens(tool_output)
                                    breakdown.tool_calls += t_in
                                    breakdown.tool_results += t_out
                                    breakdown.tool_call_count += 1
                                    tool_processes.append(
                                        {
                                            "name": tool_name,
                                            "task": (
                                                tool_input[:50] if tool_input else ""
                                            ),
                                            "input_tokens": t_in,
                                            "output_tokens": t_out,
                                        }
                                    )
                                except Exception:
                                    pass

                            # 500ms 定时推送 [005-context-monitoring]
                            try:
                                now = time.monotonic()
                                if (
                                    now - last_push_time >= push_interval
                                    and monitor_data is not None
                                ):
                                    from apps.context.monitoring import \
                                        ContextMonitor

                                    monitor_data = ContextMonitor.build_monitor_data(
                                        breakdown=breakdown,
                                        max_tokens=max_context_window,
                                        model_name=model_name,
                                        input_tokens=total_prompt_tokens,
                                        output_tokens=total_completion_tokens,
                                        memory_results=memory_results,
                                        tool_processes=tool_processes,
                                    )
                                    monitor_data["request_id"] = request_id
                                    current_alert = monitor_data["alert"]
                                    await EventService.publish_event(
                                        user_id,
                                        "context_status",
                                        monitor_data,
                                    )
                                    last_push_time = now
                                    # 告警级别变化时立即推送（已在上面推送）
                                    if current_alert != last_alert:
                                        last_alert = current_alert
                            except Exception:
                                pass

                except asyncio.TimeoutError:
                    raise LLMTimeoutError("AI响应超时，请稍后重试")

                # 处理完成/中断
                end_time = timezone.now()
                duration_ms = int((end_time - start_time).total_seconds() * 1000)

                if not first_token_received:
                    _finalize_execution(
                        execution,
                        LangGraphExecution.STATUS_FAILED,
                        end_time,
                        duration_ms,
                        error_type="NoTokenReceived",
                        error_message="未收到任何响应",
                    )
                    await execution_repo.update(execution)
                    raise LLMInvalidResponseError("AI未返回任何响应，请重试")

                if interrupted:
                    full_response += "[已中断]"
                    _finalize_message(
                        assistant_msg,
                        full_response,
                        Message.STATUS_INTERRUPTED,
                        duration_ms,
                        total_prompt_tokens,
                        total_completion_tokens,
                    )
                    await message_repo.update(assistant_msg)
                    _finalize_execution(execution, "interrupted", end_time, duration_ms)
                    await execution_repo.update(execution)
                    yield StreamChunk(
                        type="interrupted",
                        content="[已中断]",
                        message_id=assistant_msg.message_id,
                    )
                else:
                    _finalize_message(
                        assistant_msg,
                        full_response,
                        Message.STATUS_NORMAL,
                        duration_ms,
                        total_prompt_tokens,
                        total_completion_tokens,
                    )
                    await message_repo.update(assistant_msg)
                    _finalize_execution(
                        execution,
                        LangGraphExecution.STATUS_COMPLETED,
                        end_time,
                        duration_ms,
                        output_data={"response": full_response},
                        total_prompt_tokens=total_prompt_tokens,
                        total_completion_tokens=total_completion_tokens,
                        langfuse_handler=langfuse_handler,
                    )
                    await execution_repo.update(execution)
                    await user_repo.add_message_count(user_id, 2)
                    await user_repo.add_tokens(
                        user_id, total_prompt_tokens + total_completion_tokens
                    )

                    # Agent 完成后推送最终监控数据（含正确的 token 用量）
                    if monitor_data is not None:
                        try:
                            final_memory = memory_results
                            if memory_modified:
                                from apps.memory.services import MemoryService

                                final_memory = await MemoryService.search_memory(
                                    user_id=user_id,
                                    query=user_message,
                                    limit=settings.MEMORY_SEARCH_TOP_K,
                                    skip_vector=False,
                                )
                            monitor_data = ContextMonitor.build_monitor_data(
                                breakdown=breakdown,
                                max_tokens=max_context_window,
                                model_name=model_name,
                                input_tokens=total_prompt_tokens,
                                output_tokens=total_completion_tokens,
                                memory_results=final_memory,
                                tool_processes=tool_processes,
                            )
                            monitor_data["request_id"] = request_id
                            await EventService.publish_event(
                                user_id, "context_status", monitor_data
                            )
                        except Exception as e:
                            logger.warning("Final monitor push failed: %s", e)

                    yield StreamChunk(
                        type="done", content="", message_id=assistant_msg.message_id
                    )

        except LLMException:
            raise
        except Exception as e:
            logger.exception(f"Agent execution error: {request_id}")

            # T035: Gateway content_control 事件检测（安全护栏触发）
            # OpenAI SDK 遇到非标准 SSE event: content_control 时可能抛出异常
            content_control_info = _extract_content_control(e)
            if content_control_info:
                replacement_text = content_control_info
                logger.warning(
                    "Gateway content_control triggered: "
                    "user_id=%d, request_id=%s, replacement=%s",
                    user_id,
                    request_id,
                    replacement_text,
                )
                end_time = timezone.now()
                duration_ms = int(
                    (end_time - start_time).total_seconds() * 1000
                )
                _finalize_execution(
                    execution,
                    LangGraphExecution.STATUS_FAILED,
                    end_time,
                    duration_ms,
                    error_type="ContentControl",
                    error_message="safety_violation",
                )
                await execution_repo.update(execution)
                if assistant_msg:
                    assistant_msg.status = Message.STATUS_FAILED
                    assistant_msg.content = replacement_text
                    await message_repo.update(assistant_msg)
                yield StreamChunk(
                    type="error",
                    content=replacement_text,
                    data={"content_control": True},
                )
                return

            # T079: Gateway 模型错误识别 (E3001/E3002)
            gateway_error = _extract_gateway_error(e)
            if gateway_error:
                error_code, error_msg, retry_after = gateway_error
                end_time = timezone.now()
                duration_ms = int((end_time - start_time).total_seconds() * 1000)
                _finalize_execution(
                    execution,
                    LangGraphExecution.STATUS_FAILED,
                    end_time,
                    duration_ms,
                    error_type=f"GatewayError_{error_code}",
                    error_message=error_msg,
                )
                await execution_repo.update(execution)
                if assistant_msg:
                    assistant_msg.status = Message.STATUS_FAILED
                    assistant_msg.content = full_response or ""
                    await message_repo.update(assistant_msg)
                error_data: dict[str, Any] = {
                    "gateway_error": error_code,
                }
                if retry_after is not None:
                    error_data["retry_after"] = retry_after
                yield StreamChunk(type="error", content=error_msg, data=error_data)
                return

            _finalize_execution(
                execution,
                LangGraphExecution.STATUS_FAILED,
                timezone.now(),
                int((timezone.now() - start_time).total_seconds() * 1000),
                error_type=type(e).__name__,
                error_message=str(e),
            )
            await execution_repo.update(execution)
            if assistant_msg:
                assistant_msg.status = Message.STATUS_FAILED
                assistant_msg.content = full_response or ""
                await message_repo.update(assistant_msg)
            raise map_llm_exception(e)
        finally:
            unregister_generation(request_id)
            # T035: 清理取消信号监听任务
            if cancel_monitor_task and not cancel_monitor_task.done():
                cancel_monitor_task.cancel()
                try:
                    await cancel_monitor_task
                except asyncio.CancelledError:
                    pass
            # 多模态：完成推理任务（清理 Redis InferenceTask 键）
            if is_multimodal:
                await inference_service.complete_task(user_id, request_id)
            if langfuse_handler and langfuse_handler.client:
                try:
                    langfuse_handler.client.flush()
                except Exception:
                    pass

    @staticmethod
    async def resume(
        user_id: int, thread_id: str, request_id: str, message: Message
    ) -> AsyncGenerator[StreamChunk, None]:
        stop_event = register_generation(request_id)
        existing_content = message.content.replace("[已中断]", "")
        full_response = existing_content

        try:
            config = get_agent_config(user_id)
            (
                preamble,
                preamble_tokens,
                effective_window,
                _breakdown,
                _memory_results,
                _model_name,
                _max_ctx,
            ) = await _build_prompt_preamble(user_id, "请继续")

            async with create_chat_agent(
                prompt=preamble,
                preamble_tokens=preamble_tokens,
                effective_window=effective_window,
            ) as agent:
                async with asyncio.timeout(settings.AGENT_TOTAL_TIMEOUT):
                    async for event in agent.astream_events(
                        {"messages": [HumanMessage(content="请继续")]},
                        config=config,
                        version="v2",
                    ):
                        if stop_event.is_set():
                            full_response += "[已中断]"
                            await message_repo.update_content_and_status(
                                message.message_id,
                                user_id,
                                full_response,
                                Message.STATUS_INTERRUPTED,
                            )
                            yield StreamChunk(
                                type="interrupted",
                                content="[已中断]",
                                message_id=message.message_id,
                            )
                            return

                        if event["event"] == "on_chat_model_stream":
                            # SubAgent 内部 LLM 输出过滤（与 execute 一致）
                            if len(event.get("parent_ids", [])) > 3:
                                continue

                            chunk = event["data"]["chunk"]
                            if hasattr(chunk, "content") and chunk.content:
                                full_response += chunk.content
                                yield StreamChunk(
                                    type="content",
                                    content=chunk.content,
                                    message_id=message.message_id,
                                )

                await message_repo.update_content_and_status(
                    message.message_id,
                    user_id,
                    full_response,
                    Message.STATUS_NORMAL,
                )
                yield StreamChunk(
                    type="done", content="", message_id=message.message_id
                )

        except Exception as e:
            logger.exception(f"Resume generation error: {request_id}")
            await message_repo.update_status(
                message.message_id, user_id, Message.STATUS_FAILED
            )
            yield StreamChunk(type="error", content="恢复生成失败，请重试")
        finally:
            unregister_generation(request_id)
