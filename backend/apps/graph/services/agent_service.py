import asyncio
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Optional

from django.conf import settings
from django.utils import timezone
from langchain_core.messages import HumanMessage

from apps.chat.models import LangGraphExecution, MediaAttachment, Message
from apps.chat.repositories import execution_repo, media_attachment_repo, message_repo
from apps.chat.services.generation import register_generation, unregister_generation
from apps.chat.services.types import StreamChunk
from apps.common.exceptions import LLMException, LLMInvalidResponseError, LLMTimeoutError, map_llm_exception
from apps.graph.agent import create_chat_agent, get_agent_config
from apps.graph.services.agent_helpers import (
    build_prompt_preamble, check_context_compression, compress_context, create_first_token_messages,
    extract_content_control, extract_gateway_error, extract_usage, finalize_execution,
    finalize_interrupted, finalize_success, handle_execution_failure, handle_tool_end_event,
    init_langfuse, init_monitor_data, push_final_monitor, push_monitor_update,
)
from apps.graph.services.cancel_monitor import monitor_cancel_signal, poll_cancel_signal
from apps.graph.services.inference_service import inference_service
from apps.users.repositories import user_repo

logger = logging.getLogger(__name__)


class AgentService:
    @staticmethod
    async def execute(user_id: int, thread_id: str, request_id: str, user_message: str, attachment_uuids: Optional[list[str]] = None) -> AsyncGenerator[StreamChunk, None]:
        start_time = timezone.now()
        stop_event = register_generation(request_id)
        attachments: list[MediaAttachment] = []
        is_multimodal = False
        media_types: list[str] = []
        if attachment_uuids:
            attachments = await media_attachment_repo.get_by_uuids(attachment_uuids, user_id)
            if attachments:
                is_multimodal = True
                expired = next((a for a in attachments if a.is_expired), None)
                if expired:
                    yield StreamChunk(type="error", content=f"附件已过期: {expired.file_name}")
                    return
                media_types = list(set(a.media_type for a in attachments))
                if not await inference_service.register_task(user_id=user_id, request_id=request_id, model="multimodal", media_types=media_types):
                    yield StreamChunk(type="error", content="推理任务冲突，请稍后重试")
                    return
        input_data: dict[str, Any] = {"message": user_message}
        if is_multimodal:
            input_data["multimodal"] = {"model": "multimodal", "media_types": media_types, "attachment_count": len(attachments)}
        execution = LangGraphExecution(
            execution_uuid=str(uuid.uuid4()), request_id=request_id, user_id=user_id,
            thread_id=thread_id, graph_name="react_agent",
            status=LangGraphExecution.STATUS_PENDING, start_time=start_time, input_data=input_data,
        )
        await execution_repo.create(execution)
        full_response = ""
        total_prompt_tokens = total_completion_tokens = 0
        assistant_msg: Optional[Message] = None
        interrupted = first_token_received = memory_modified = False
        cancel_task: Optional[asyncio.Task] = None
        langfuse_handler = None
        try:
            if is_multimodal:
                cancel_task = asyncio.create_task(monitor_cancel_signal(user_id, request_id, stop_event))
            langfuse_handler = init_langfuse(
                request_id,
                {"model": "multimodal", "media_types": media_types, "attachment_count": len(attachments), "gateway_request_id": request_id} if is_multimodal else None,
            )
            max_seq = await message_repo.get_max_sequence(user_id)
            execution.status = LangGraphExecution.STATUS_RUNNING
            await execution_repo.update(execution)
            config = get_agent_config(user_id, [langfuse_handler] if langfuse_handler else None)
            if attachment_uuids:
                config["configurable"]["attachment_uuids"] = attachment_uuids
            config["configurable"].update(stop_event=stop_event, request_id=request_id)
            hm_content = user_message
            if is_multimodal and attachments:
                hm_content = f"{user_message}\n\n[用户上传了 {len(attachments)} 个附件: {'、'.join(f'{a.file_name}({a.media_type})' for a in attachments)}]"
            input_message = {"messages": [HumanMessage(content=hm_content)]}
            preamble, ptokens, eff_win, breakdown, mem_results, model_name, max_ctx = await build_prompt_preamble(user_id, user_message)
            monitor_data, last_alert = await init_monitor_data(breakdown, max_ctx, model_name, mem_results, [], request_id, user_id)
            tool_procs: list[dict[str, Any]] = []
            try:
                needs, ctx_msgs = await check_context_compression(user_id, user_message, eff_win)
                if needs:
                    yield StreamChunk(type="context_compacting", content="")
                    await compress_context(user_id, ctx_msgs, eff_win)
                    yield StreamChunk(type="context_compacted", content="")
            except Exception as e:
                logger.warning("Context compression check failed: %s", e)
            last_push = time.monotonic()
            interval = getattr(settings, "MONITOR_PUSH_INTERVAL", 0.5)
            async with create_chat_agent(prompt=preamble, preamble_tokens=ptokens, effective_window=eff_win) as agent:
                try:
                    tout = getattr(settings, "AGENT_MULTIMODAL_TIMEOUT", 1500) if (is_multimodal and "document" in media_types) else settings.AGENT_TOTAL_TIMEOUT
                    async with asyncio.timeout(tout):
                        async for ev in agent.astream_events(input_message, config=config, version="v2"):
                            if stop_event.is_set():
                                interrupted = True
                                break
                            event_type = ev.get("event", "")
                            if event_type == "on_chat_model_stream":
                                if len(ev.get("parent_ids", [])) > 3:
                                    continue
                                chunk = ev["data"]["chunk"]
                                if hasattr(chunk, "content") and chunk.content:
                                    if not first_token_received:
                                        first_token_received = True
                                        first_token_time = timezone.now()
                                        _, assistant_msg = await create_first_token_messages(
                                            user_id, user_message, request_id, max_seq,
                                            start_time, first_token_time, is_multimodal,
                                            attachment_uuids, attachments,
                                        )
                                    full_response += chunk.content
                                    stream_chunk = StreamChunk(
                                        type="content", content=chunk.content,
                                        message_id=assistant_msg.message_id if assistant_msg else None,
                                    )
                                    if len(full_response) == len(chunk.content):
                                        stream_chunk.request_id = request_id
                                    yield stream_chunk
                            elif event_type == "on_chat_model_end":
                                out = ev.get("data", {}).get("output")
                                if out:
                                    pt, ct = extract_usage(out)
                                    total_prompt_tokens += pt
                                    total_completion_tokens += ct
                            elif event_type == "on_tool_end" and len(ev.get("parent_ids", [])) <= 3:
                                if handle_tool_end_event(ev, breakdown, tool_procs):
                                    memory_modified = True
                            try:
                                now = time.monotonic()
                                if now - last_push >= interval and monitor_data is not None:
                                    monitor_data, last_alert = await push_monitor_update(
                                        monitor_data, breakdown, max_ctx, model_name,
                                        total_prompt_tokens, total_completion_tokens,
                                        mem_results, tool_procs, request_id, user_id, last_alert,
                                    )
                                    last_push = now
                            except Exception:
                                pass
                except asyncio.TimeoutError:
                    raise LLMTimeoutError("AI响应超时，请稍后重试")
                end = timezone.now()
                dur = int((end - start_time).total_seconds() * 1000)
                if not first_token_received:
                    finalize_execution(
                        execution, LangGraphExecution.STATUS_FAILED, end, dur,
                        error_type="NoTokenReceived", error_message="未收到任何响应",
                    )
                    await execution_repo.update(execution)
                    raise LLMInvalidResponseError("AI未返回任何响应，请重试")
                if interrupted:
                    await finalize_interrupted(
                        execution, execution_repo, assistant_msg, message_repo,
                        full_response, end, dur, total_prompt_tokens, total_completion_tokens,
                    )
                    yield StreamChunk(type="interrupted", content="[已中断]", message_id=assistant_msg.message_id)
                else:
                    await finalize_success(
                        execution, execution_repo, assistant_msg, message_repo, user_repo,
                        full_response, end, dur, user_id,
                        total_prompt_tokens, total_completion_tokens, langfuse_handler,
                    )
                    if monitor_data is not None:
                        await push_final_monitor(
                            user_id, user_message, memory_modified, mem_results,
                            breakdown, max_ctx, model_name,
                            total_prompt_tokens, total_completion_tokens,
                            tool_procs, request_id,
                        )
                    yield StreamChunk(type="done", content="", message_id=assistant_msg.message_id)
        except LLMException:
            raise
        except Exception as e:
            logger.exception("Agent execution error: %s", request_id)
            # 安全护栏检测
            content_control = extract_content_control(e)
            if content_control:
                await handle_execution_failure(
                    execution, execution_repo, start_time, "ContentControl", "safety_violation",
                    assistant_msg, message_repo, content_control,
                )
                yield StreamChunk(type="error", content=content_control, data={"content_control": True})
                return
            # Gateway 错误检测
            gateway_err = extract_gateway_error(e)
            if gateway_err:
                code, msg, retry = gateway_err
                await handle_execution_failure(
                    execution, execution_repo, start_time, f"GatewayError_{code}", msg,
                    assistant_msg, message_repo, full_response or "",
                )
                error_data: dict[str, Any] = {"gateway_error": code}
                if retry is not None:
                    error_data["retry_after"] = retry
                yield StreamChunk(type="error", content=msg, data=error_data)
                return
            # 通用异常
            await handle_execution_failure(
                execution, execution_repo, start_time, type(e).__name__, str(e),
                assistant_msg, message_repo, full_response or "",
            )
            raise map_llm_exception(e)
        finally:
            unregister_generation(request_id)
            if cancel_task and not cancel_task.done():
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass
            if is_multimodal:
                await inference_service.complete_task(user_id, request_id)
            if langfuse_handler and langfuse_handler.client:
                try:
                    langfuse_handler.client.flush()
                except Exception:
                    pass

    @staticmethod
    async def resume(user_id: int, thread_id: str, request_id: str, message: Message) -> AsyncGenerator[StreamChunk, None]:
        stop_event = register_generation(request_id)
        full_response = message.content.replace("[已中断]", "")
        try:
            preamble, ptokens, eff_win, *_ = await build_prompt_preamble(user_id, "请继续")
            async with create_chat_agent(prompt=preamble, preamble_tokens=ptokens, effective_window=eff_win) as agent:
                async with asyncio.timeout(settings.AGENT_TOTAL_TIMEOUT):
                    async for ev in agent.astream_events({"messages": [HumanMessage(content="请继续")]}, config=get_agent_config(user_id), version="v2"):
                        if stop_event.is_set():
                            full_response += "[已中断]"
                            await message_repo.update_content_and_status(message.message_id, user_id, full_response, Message.STATUS_INTERRUPTED)
                            yield StreamChunk(type="interrupted", content="[已中断]", message_id=message.message_id)
                            return
                        if ev["event"] == "on_chat_model_stream":
                            if len(ev.get("parent_ids", [])) > 3:
                                continue
                            chunk = ev["data"]["chunk"]
                            if hasattr(chunk, "content") and chunk.content:
                                full_response += chunk.content
                                yield StreamChunk(type="content", content=chunk.content, message_id=message.message_id)
                await message_repo.update_content_and_status(message.message_id, user_id, full_response, Message.STATUS_NORMAL)
                yield StreamChunk(type="done", content="", message_id=message.message_id)
        except Exception:
            logger.exception("Resume generation error: %s", request_id)
            await message_repo.update_status(message.message_id, user_id, Message.STATUS_FAILED)
            yield StreamChunk(type="error", content="恢复生成失败，请重试")
        finally:
            unregister_generation(request_id)
