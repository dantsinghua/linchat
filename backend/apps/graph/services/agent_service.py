"""Agent 执行服务

参考:
- behavior-model.md#2.2 执行LangGraph Agent（B_CHAT_002）
- behavior-model.md#2.5 继续生成（B_CHAT_005）
"""

import asyncio
import logging
import os
import uuid
from typing import AsyncGenerator, Optional

from django.conf import settings
from django.utils import timezone
from langchain_core.messages import HumanMessage
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

from apps.chat.models import LangGraphExecution, Message
from apps.chat.repositories import execution_repo, message_repo
from apps.chat.services.generation import (map_llm_exception,
                                           register_generation,
                                           unregister_generation)
from apps.chat.services.types import StreamChunk, _get_language_model_name
from apps.common.exceptions import (LLMException, LLMInvalidResponseError,
                                    LLMTimeoutError)
from apps.graph.agent import create_chat_agent, get_agent_config
from apps.graph.prompts import PromptBuilder, PromptConfig, PromptModule
from apps.users.repositories import user_repo

logger = logging.getLogger(__name__)


async def _build_prompt_preamble(
    user_id: int, user_message: str = "",
) -> tuple[list, int, int]:
    """构建 Agent 前置 prompt 消息列表 [T044]"""
    from asgiref.sync import sync_to_async

    from apps.common.tokenizer import count_tokens
    from apps.models.services import model_service

    config_data = await sync_to_async(model_service.get_active_model)("language")
    max_context_window = config_data.get("max_context_window", 128000) if config_data else 128000

    prompt_config = PromptConfig(user_id=user_id, max_context_window=max_context_window)
    builder = PromptBuilder(config=prompt_config)

    # 记忆召回 [T044]
    retrieved_memories = None
    if user_message:
        try:
            from apps.graph.prompts import RetrievedMemory
            from apps.memory.services import MemoryService

            results = await MemoryService.search_memory(
                user_id=user_id, query=user_message,
                limit=settings.MEMORY_SEARCH_TOP_K, skip_vector=True,
            )
            if results:
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

    preamble = builder.build_preamble(retrieved_memories=retrieved_memories)

    fixed_tokens = sum(
        count_tokens(m.content if hasattr(m, "content") else str(m))
        for m in preamble
    )

    # 从 DB 拉取对话历史
    from langchain_core.messages import AIMessage, HumanMessage, trim_messages

    history_msgs = await message_repo.find_latest_by_user(user_id, limit=50)
    history_msgs.reverse()
    history = []
    for m in history_msgs:
        if m.role == "user":
            history.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            content = (m.content or "").removesuffix("[已中断]")
            if content:
                history.append(AIMessage(content=content))

    history_budget = max_context_window - fixed_tokens - 4096
    history = list(trim_messages(
        history,
        max_tokens=max(history_budget, 2000),
        token_counter=lambda msgs: sum(count_tokens(m.content or "") for m in msgs),
        strategy="last", start_on="human", allow_partial=False,
    ))

    preamble = list(preamble) + history
    preamble_tokens = sum(
        count_tokens(m.content if hasattr(m, "content") else str(m))
        for m in preamble
    )

    logger.debug(
        "Built preamble for user %d: preamble_tokens=%d, memories=%s",
        user_id, preamble_tokens, len(retrieved_memories) if retrieved_memories else 0,
    )
    return preamble, preamble_tokens, prompt_config.effective_window


def _init_langfuse(request_id: str) -> Optional[LangfuseCallbackHandler]:
    """初始化 Langfuse 追踪"""
    try:
        if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
            return None
        for key, val in [
            ("LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY),
            ("LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY),
            ("LANGFUSE_HOST", settings.LANGFUSE_HOST),
        ]:
            os.environ.setdefault(key, val)
        return LangfuseCallbackHandler()
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


def _finalize_message(msg, content, status, duration_ms, prompt_tokens, completion_tokens):
    msg.content = content
    msg.status = status
    msg.response_time_ms = duration_ms
    msg.prompt_tokens = prompt_tokens
    msg.completion_tokens = completion_tokens


def _finalize_execution(
    execution, status, end_time, duration_ms,
    output_data=None, total_prompt_tokens=0, total_completion_tokens=0,
    langfuse_handler=None, error_type=None, error_message=None,
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


class AgentService:
    """Agent 执行服务"""

    @staticmethod
    async def execute(
        user_id: int, thread_id: str, request_id: str, user_message: str
    ) -> AsyncGenerator[StreamChunk, None]:
        execution_uuid = str(uuid.uuid4())
        start_time = timezone.now()
        stop_event = register_generation(request_id)

        execution = LangGraphExecution(
            execution_uuid=execution_uuid, request_id=request_id,
            user_id=user_id, thread_id=thread_id, graph_name="react_agent",
            status=LangGraphExecution.STATUS_PENDING, start_time=start_time,
            input_data={"message": user_message},
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

        try:
            langfuse_handler = _init_langfuse(request_id)
            max_seq = await message_repo.get_max_sequence(user_id)
            execution.status = LangGraphExecution.STATUS_RUNNING
            await execution_repo.update(execution)

            callbacks = [langfuse_handler] if langfuse_handler else None
            config = get_agent_config(user_id, callbacks)
            input_message = {"messages": [HumanMessage(content=user_message)]}
            preamble, preamble_tokens, effective_window = (
                await _build_prompt_preamble(user_id, user_message)
            )

            # 上下文压缩检测 [T069]
            try:
                from apps.chat.services.context_service import ContextService

                history_msgs = await message_repo.find_latest_by_user(user_id, limit=50)
                context_messages = [{"role": m.role, "content": m.content} for m in history_msgs]
                context_messages.append({"role": "user", "content": user_message})

                if ContextService.check_token_limit(context_messages, effective_window):
                    yield StreamChunk(type="context_compacting", content="")
                    await ContextService.compress_context(
                        user_id=user_id, messages=context_messages,
                        effective_window=effective_window,
                    )
                    yield StreamChunk(type="context_compacted", content="")
            except Exception as e:
                logger.warning("Context compression check failed: %s", e)

            # 流式执行
            async with create_chat_agent(
                prompt=preamble, preamble_tokens=preamble_tokens,
                effective_window=effective_window,
            ) as agent:
                try:
                    async with asyncio.timeout(settings.AGENT_TOTAL_TIMEOUT):
                        async for event in agent.astream_events(
                            input_message, config=config, version="v2"
                        ):
                            if stop_event.is_set():
                                interrupted = True
                                break

                            if event["event"] == "on_chat_model_stream":
                                chunk = event["data"]["chunk"]
                                if hasattr(chunk, "content") and chunk.content:
                                    if not first_token_received:
                                        first_token_received = True
                                        first_token_time = timezone.now()
                                        user_msg = Message(
                                            message_uuid=str(uuid.uuid4()),
                                            user_id=user_id, role=Message.ROLE_USER,
                                            content=user_message, request_id=request_id,
                                            sequence=max_seq + 1, status=Message.STATUS_NORMAL,
                                            created_time=start_time,
                                        )
                                        await message_repo.create(user_msg)
                                        assistant_msg = Message(
                                            message_uuid=str(uuid.uuid4()),
                                            user_id=user_id, role=Message.ROLE_ASSISTANT,
                                            content="", request_id=request_id,
                                            sequence=max_seq + 2, status=Message.STATUS_GENERATING,
                                            model_name=await _get_language_model_name(),
                                            created_time=first_token_time,
                                        )
                                        await message_repo.create(assistant_msg)

                                    full_response += chunk.content
                                    chunk_data = StreamChunk(
                                        type="content", content=chunk.content,
                                        message_id=assistant_msg.message_id if assistant_msg else None,
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

                except asyncio.TimeoutError:
                    raise LLMTimeoutError("AI响应超时，请稍后重试")

                # 处理完成/中断
                end_time = timezone.now()
                duration_ms = int((end_time - start_time).total_seconds() * 1000)

                if not first_token_received:
                    _finalize_execution(
                        execution, LangGraphExecution.STATUS_FAILED, end_time, duration_ms,
                        error_type="NoTokenReceived", error_message="未收到任何响应",
                    )
                    await execution_repo.update(execution)
                    raise LLMInvalidResponseError("AI未返回任何响应，请重试")

                if interrupted:
                    full_response += "[已中断]"
                    _finalize_message(assistant_msg, full_response, Message.STATUS_INTERRUPTED, duration_ms, total_prompt_tokens, total_completion_tokens)
                    await message_repo.update(assistant_msg)
                    _finalize_execution(execution, "interrupted", end_time, duration_ms)
                    await execution_repo.update(execution)
                    yield StreamChunk(type="interrupted", content="[已中断]", message_id=assistant_msg.message_id)
                else:
                    _finalize_message(assistant_msg, full_response, Message.STATUS_NORMAL, duration_ms, total_prompt_tokens, total_completion_tokens)
                    await message_repo.update(assistant_msg)
                    _finalize_execution(
                        execution, LangGraphExecution.STATUS_COMPLETED, end_time, duration_ms,
                        output_data={"response": full_response},
                        total_prompt_tokens=total_prompt_tokens,
                        total_completion_tokens=total_completion_tokens,
                        langfuse_handler=langfuse_handler,
                    )
                    await execution_repo.update(execution)
                    await user_repo.add_message_count(user_id, 2)
                    await user_repo.add_tokens(user_id, total_prompt_tokens + total_completion_tokens)
                    yield StreamChunk(type="done", content="", message_id=assistant_msg.message_id)

        except LLMException:
            raise
        except Exception as e:
            logger.exception(f"Agent execution error: {request_id}")
            _finalize_execution(
                execution, LangGraphExecution.STATUS_FAILED, timezone.now(),
                int((timezone.now() - start_time).total_seconds() * 1000),
                error_type=type(e).__name__, error_message=str(e),
            )
            await execution_repo.update(execution)
            if assistant_msg:
                assistant_msg.status = Message.STATUS_FAILED
                assistant_msg.content = full_response or ""
                await message_repo.update(assistant_msg)
            raise map_llm_exception(e)
        finally:
            unregister_generation(request_id)
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
            preamble, preamble_tokens, effective_window = (
                await _build_prompt_preamble(user_id, "请继续")
            )

            async with create_chat_agent(
                prompt=preamble, preamble_tokens=preamble_tokens,
                effective_window=effective_window,
            ) as agent:
                async with asyncio.timeout(settings.AGENT_TOTAL_TIMEOUT):
                    async for event in agent.astream_events(
                        {"messages": [HumanMessage(content="请继续")]},
                        config=config, version="v2",
                    ):
                        if stop_event.is_set():
                            full_response += "[已中断]"
                            await message_repo.update_content_and_status(
                                message.message_id, user_id, full_response, Message.STATUS_INTERRUPTED,
                            )
                            yield StreamChunk(type="interrupted", content="[已中断]", message_id=message.message_id)
                            return

                        if event["event"] == "on_chat_model_stream":
                            chunk = event["data"]["chunk"]
                            if hasattr(chunk, "content") and chunk.content:
                                full_response += chunk.content
                                yield StreamChunk(type="content", content=chunk.content, message_id=message.message_id)

                await message_repo.update_content_and_status(
                    message.message_id, user_id, full_response, Message.STATUS_NORMAL,
                )
                yield StreamChunk(type="done", content="", message_id=message.message_id)

        except Exception as e:
            logger.exception(f"Resume generation error: {request_id}")
            await message_repo.update_status(message.message_id, user_id, Message.STATUS_FAILED)
            yield StreamChunk(type="error", content="恢复生成失败，请重试")
        finally:
            unregister_generation(request_id)
