import json as _json
import logging
import os
import uuid
from typing import Any, Optional

from django.conf import settings
from django.utils import timezone
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

from apps.graph.prompts import PromptBuilder, PromptConfig, RetrievedMemory

logger = logging.getLogger(__name__)
_FILTERED_CONTENT = "内容已被安全策略过滤"


async def build_prompt_preamble(user_id: int, user_message: str = ""):
    from asgiref.sync import sync_to_async
    from apps.chat.repositories import message_repo
    from apps.common.tokenizer import count_tokens
    from apps.models.services import model_service

    model_config = await sync_to_async(model_service.get_active_model)("tool")
    max_context_window = model_config.get("max_context_window", 128000) if model_config else 128000
    model_name = model_config.get("name", "unknown") if model_config else "unknown"
    prompt_config = PromptConfig(user_id=user_id, max_context_window=max_context_window)
    builder = PromptBuilder(config=prompt_config)

    retrieved_memories = None
    memory_results: list = []
    if user_message:
        try:
            from apps.memory.services import MemoryService
            res = await MemoryService.search_memory(
                user_id=user_id, query=user_message,
                limit=settings.MEMORY_SEARCH_TOP_K, skip_vector=False,
            )
            if res:
                memory_results = res
                retrieved_memories = [
                    RetrievedMemory(
                        content=r["memory"].content,
                        memory_type=r["memory"].type,
                        relevance_score=r["score"],
                    )
                    for r in res
                ]
        except Exception as e:
            logger.warning("Memory recall failed for user %d: %s", user_id, e)

    preamble = builder.build_preamble(retrieved_memories=retrieved_memories)
    preamble_tokens = sum(
        count_tokens(m.content if hasattr(m, "content") else str(m))
        for m in preamble
    )
    history_messages = await message_repo.find_latest_by_user(
        user_id, limit=getattr(settings, "CONTEXT_HISTORY_ROUNDS", 10) * 2,
    )
    history_messages.reverse()

    history_dicts: list[dict[str, str]] = []
    for m in history_messages:
        if m.role == "user" and m.content:
            history_dicts.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            content = (m.content or "").removesuffix("[已中断]")
            if content:
                history_dicts.append({"role": "assistant", "content": content})

    token_budget = max(max_context_window - preamble_tokens - 4096, 2000)
    trimmed_history: list[dict[str, str]] = []
    used_tokens = 0
    for msg in reversed(history_dicts):
        t = count_tokens(msg["content"])
        if used_tokens + t > token_budget:
            break
        trimmed_history.append(msg)
        used_tokens += t
    trimmed_history.reverse()
    while trimmed_history and trimmed_history[0]["role"] != "user":
        trimmed_history.pop(0)

    preamble, breakdown = builder.build_preamble_with_breakdown(
        user_input=user_message,
        retrieved_memories=retrieved_memories,
        conversation_history=trimmed_history if trimmed_history else None,
    )
    return (
        preamble,
        breakdown.total - breakdown.user_input,
        prompt_config.effective_window,
        breakdown,
        memory_results,
        model_name,
        max_context_window,
    )


def init_langfuse(request_id: str, multimodal_metadata=None):
    try:
        if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
            return None
        for key, value in [
            ("LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY),
            ("LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY),
            ("LANGFUSE_HOST", settings.LANGFUSE_HOST),
        ]:
            os.environ.setdefault(key, value)
        return LangfuseCallbackHandler(trace_context={"trace_id": request_id})
    except Exception as e:
        logger.warning("Langfuse init failed: %s", e)
        return None


def extract_usage(output) -> tuple[int, int]:
    if hasattr(output, "usage_metadata") and output.usage_metadata:
        usage = output.usage_metadata
        return usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    if hasattr(output, "response_metadata") and output.response_metadata:
        usage = output.response_metadata.get("token_usage", {})
        return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    return 0, 0


def finalize_message(msg, content, status, duration_ms, pt, ct):
    msg.content = content
    msg.status = status
    msg.response_time_ms = duration_ms
    msg.prompt_tokens = pt
    msg.completion_tokens = ct


def finalize_execution(
    ex, status, end_time, duration_ms, output_data=None,
    total_prompt_tokens=0, total_completion_tokens=0,
    langfuse_handler=None, error_type=None, error_message=None,
):
    ex.status = status
    ex.end_time = end_time
    ex.duration_ms = duration_ms
    if output_data:
        ex.output_data = output_data
    if total_prompt_tokens:
        ex.total_prompt_tokens = total_prompt_tokens
    if total_completion_tokens:
        ex.total_completion_tokens = total_completion_tokens
    if langfuse_handler and hasattr(langfuse_handler, "last_trace_id"):
        ex.langfuse_trace_id = langfuse_handler.last_trace_id
    if error_type:
        ex.error_type = error_type
    if error_message:
        ex.error_message = error_message


def _match_gateway_error(code, s, info=None):
    if "E3001" in (code or s):
        return "E3001", "请求的模型不存在", None
    if "E3002" in (code or s):
        retry_after = info.get("details", {}).get("retry_after") if info else None
        return "E3002", "多模态服务暂时不可用，请稍后重试", retry_after
    return None


def extract_gateway_error(e) -> Optional[tuple[str, str, Optional[int]]]:
    error_body = None
    try:
        if hasattr(e, "response") and hasattr(e.response, "text"):
            error_body = _json.loads(e.response.text)
        elif hasattr(e, "body") and isinstance(e.body, dict):
            error_body = e.body
    except Exception:
        pass
    if not error_body:
        return _match_gateway_error("", str(e))
    info = error_body.get("error", {})
    return _match_gateway_error(info.get("code", ""), "", info)


def extract_content_control(e) -> Optional[str]:
    s = str(e)
    if "content_control" in s:
        try:
            for part in s.split("data:"):
                part = part.strip()
                if part.startswith("{") and "content_control" in part:
                    return _json.loads(part.split("\n")[0]).get("replacement", _FILTERED_CONTENT)
        except Exception:
            pass
        return _FILTERED_CONTENT
    if hasattr(e, "body") and isinstance(e.body, dict) and e.body.get("type") == "clear_previous":
        return e.body.get("replacement", _FILTERED_CONTENT)
    try:
        if hasattr(e, "response") and hasattr(e.response, "text"):
            text = e.response.text
            if "content_control" in text or "clear_previous" in text:
                return _json.loads(text).get("replacement", _FILTERED_CONTENT)
    except Exception:
        pass
    return None


async def _publish_monitor(
    breakdown, max_context_window, model_name, memory_results,
    tool_processes, request_id, user_id, input_tokens=0, output_tokens=0,
):
    from apps.common.event_service import EventService
    from apps.context.monitoring import ContextMonitor

    data = ContextMonitor.build_monitor_data(
        breakdown=breakdown, max_tokens=max_context_window, model_name=model_name,
        input_tokens=input_tokens, output_tokens=output_tokens,
        memory_results=memory_results, tool_processes=tool_processes,
    )
    data["request_id"] = request_id
    await EventService.publish_event(user_id, "context_status", data)
    return data, data["alert"]


async def init_monitor_data(
    breakdown, max_context_window, model_name,
    memory_results, tool_processes, request_id, user_id,
):
    try:
        return await _publish_monitor(
            breakdown, max_context_window, model_name,
            memory_results, tool_processes, request_id, user_id,
        )
    except Exception as e:
        logger.warning("Monitor init failed: %s", e)
        return None, None


async def check_context_compression(user_id, user_message, effective_window):
    from apps.chat.repositories import message_repo
    from apps.graph.services.context_service import ContextService

    history_messages = await message_repo.find_latest_by_user(
        user_id, limit=getattr(settings, "CONTEXT_HISTORY_ROUNDS", 10) * 2,
    )
    ctx = [{"role": m.role, "content": m.content} for m in history_messages]
    ctx.append({"role": "user", "content": user_message})
    return ContextService.check_token_limit(ctx, effective_window), ctx


async def compress_context(user_id, ctx_messages, effective_window):
    from apps.graph.services.context_service import ContextService
    await ContextService.compress_context(
        user_id=user_id, messages=ctx_messages, effective_window=effective_window,
    )


def handle_tool_end_event(event, breakdown, tool_processes):
    try:
        from apps.common.tokenizer import count_tokens

        tool_name = event.get("name", "unknown")
        data = event.get("data", {})
        input_tokens = count_tokens(str(data.get("input", "")))
        output_tokens = count_tokens(str(data.get("output", "")))
        breakdown.tool_calls += input_tokens
        breakdown.tool_results += output_tokens
        breakdown.tool_call_count += 1
        tool_processes.append({
            "name": tool_name,
            "task": str(data.get("input", ""))[:50],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })
        return tool_name == "memory_subagent"
    except Exception:
        return False


async def push_monitor_update(
    monitor_data, breakdown, max_context_window, model_name,
    total_prompt_tokens, total_completion_tokens,
    memory_results, tool_processes, request_id, user_id, last_alert,
):
    return await _publish_monitor(
        breakdown, max_context_window, model_name, memory_results,
        tool_processes, request_id, user_id,
        total_prompt_tokens, total_completion_tokens,
    )


async def push_final_monitor(
    user_id, user_message, memory_modified, memory_results,
    breakdown, max_context_window, model_name,
    total_prompt_tokens, total_completion_tokens,
    tool_processes, request_id,
):
    try:
        final_memories = memory_results
        if memory_modified:
            from apps.memory.services import MemoryService
            final_memories = await MemoryService.search_memory(
                user_id=user_id, query=user_message,
                limit=settings.MEMORY_SEARCH_TOP_K, skip_vector=False,
            )
        await _publish_monitor(
            breakdown, max_context_window, model_name, final_memories,
            tool_processes, request_id, user_id,
            total_prompt_tokens, total_completion_tokens,
        )
    except Exception as e:
        logger.warning("Final monitor push failed: %s", e)


async def handle_execution_failure(
    execution, execution_repo, start_time, error_type, error_message,
    assistant_msg=None, message_repo=None, content=None,
):
    end_time = timezone.now()
    duration_ms = int((end_time - start_time).total_seconds() * 1000)
    finalize_execution(
        execution, "failed", end_time, duration_ms,
        error_type=error_type, error_message=error_message,
    )
    await execution_repo.update(execution)
    if assistant_msg and message_repo:
        assistant_msg.status = 0  # Message.STATUS_FAILED
        assistant_msg.content = content or ""
        await message_repo.update(assistant_msg)


async def create_first_token_messages(
    user_id, user_message, request_id, max_seq, start_time,
    first_token_time, is_multimodal, attachment_uuids, attachments,
):
    from apps.chat.models import Message
    from apps.chat.repositories import media_attachment_repo, message_repo
    from apps.chat.services.types import _get_tool_model_name

    user_msg = Message(
        message_uuid=str(uuid.uuid4()), user_id=user_id,
        role=Message.ROLE_USER, content=user_message,
        request_id=request_id, sequence=max_seq + 1,
        status=Message.STATUS_NORMAL, created_time=start_time,
    )
    await message_repo.create(user_msg)
    assistant_msg = Message(
        message_uuid=str(uuid.uuid4()), user_id=user_id,
        role=Message.ROLE_ASSISTANT, content="",
        request_id=request_id, sequence=max_seq + 2,
        status=Message.STATUS_GENERATING,
        model_name=await _get_tool_model_name(),
        created_time=first_token_time,
    )
    await message_repo.create(assistant_msg)
    if is_multimodal and attachment_uuids:
        await media_attachment_repo.associate_message(
            attachment_ids=[a.attachment_id for a in attachments],
            message_id=user_msg.message_id, user_id=user_id,
        )
    return user_msg, assistant_msg


async def validate_attachments(attachment_uuids, user_id):
    from apps.chat.repositories import media_attachment_repo
    from apps.graph.services.inference_service import inference_service

    atts = await media_attachment_repo.get_by_uuids(attachment_uuids, user_id)
    if not atts:
        return [], False, [], None
    exp = next((a for a in atts if a.is_expired), None)
    if exp:
        return atts, True, [], f"附件已过期: {exp.file_name}"
    media_types = list(set(a.media_type for a in atts))
    reg = await inference_service.register_task(
        user_id=user_id, request_id=None, model="multimodal", media_types=media_types,
    )
    return atts, False, media_types, None if reg else "推理任务冲突，请稍后重试"


async def finalize_success(
    execution, execution_repo, assistant_msg, message_repo,
    user_repo, full_response, end_time, duration_ms,
    user_id, tpt, tct, langfuse_handler,
):
    from apps.chat.models import LangGraphExecution, Message

    finalize_message(assistant_msg, full_response, Message.STATUS_NORMAL, duration_ms, tpt, tct)
    await message_repo.update(assistant_msg)
    finalize_execution(
        execution, LangGraphExecution.STATUS_COMPLETED, end_time, duration_ms,
        output_data={"response": full_response},
        total_prompt_tokens=tpt, total_completion_tokens=tct,
        langfuse_handler=langfuse_handler,
    )
    await execution_repo.update(execution)
    await user_repo.add_message_count(user_id, 2)
    await user_repo.add_tokens(user_id, tpt + tct)


async def finalize_interrupted(
    execution, execution_repo, assistant_msg, message_repo,
    full_response, end_time, duration_ms, tpt, tct,
):
    from apps.chat.models import Message

    full_response += "[已中断]"
    finalize_message(assistant_msg, full_response, Message.STATUS_INTERRUPTED, duration_ms, tpt, tct)
    await message_repo.update(assistant_msg)
    finalize_execution(execution, "interrupted", end_time, duration_ms)
    await execution_repo.update(execution)
    return full_response
