# Backward-compat re-exports — all logic moved to helpers/
from apps.graph.services.helpers import (  # noqa: F401
    build_prompt_preamble,
    create_first_token_messages,
    extract_content_control,
    extract_gateway_error,
    extract_usage,
    finalize_completion,
    finalize_execution,
    finalize_message,
    handle_execution_failure,
    handle_tool_end_event,
    init_langfuse,
    init_monitor_data,
    publish_monitor,
    push_final_monitor,
)

# Legacy aliases
finalize_success = finalize_completion


async def finalize_interrupted(
    execution, execution_repo, assistant_msg, message_repo,
    full_response, end_time, duration_ms, tpt, tct,
):
    return await finalize_completion(
        execution, execution_repo, assistant_msg, message_repo,
        None, full_response, end_time, duration_ms,
        0, tpt, tct, None, interrupted=True,
    )


async def push_monitor_update(
    monitor_data, breakdown, max_context_window, model_name,
    total_prompt_tokens, total_completion_tokens,
    memory_results, tool_processes, request_id, user_id, last_alert,
):
    return await publish_monitor(
        breakdown, max_context_window, model_name, memory_results,
        tool_processes, request_id, user_id,
        total_prompt_tokens, total_completion_tokens,
    )


async def check_context_compression(user_id, user_message, effective_window):
    from apps.chat.repositories import message_repo
    from apps.graph.services.context_service import ContextService
    from django.conf import settings

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
