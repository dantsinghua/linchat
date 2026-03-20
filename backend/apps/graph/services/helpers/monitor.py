import logging
import os

from django.conf import settings
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

logger = logging.getLogger(__name__)


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


async def publish_monitor(
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
        return await publish_monitor(
            breakdown, max_context_window, model_name,
            memory_results, tool_processes, request_id, user_id,
        )
    except Exception as e:
        logger.warning("Monitor init failed: %s", e)
        return None, None


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
            "name": tool_name, "task": str(data.get("input", ""))[:50],
            "input_tokens": input_tokens, "output_tokens": output_tokens,
        })
        return tool_name == "memory_subagent"
    except Exception:
        return False


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
        await publish_monitor(
            breakdown, max_context_window, model_name, final_memories,
            tool_processes, request_id, user_id,
            total_prompt_tokens, total_completion_tokens,
        )
    except Exception as e:
        logger.warning("Final monitor push failed: %s", e)
