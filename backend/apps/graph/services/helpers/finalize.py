import logging
import uuid

from django.utils import timezone

logger = logging.getLogger(__name__)


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
        # langfuse_handler.last_trace_id 是 Langfuse 内部生成的 hex，
        # 与 HTTP X-Request-ID（batch-04 的 trace_id）是两个独立 id；
        # 此字段仅供从 Langfuse UI 反查 LinChat 执行记录使用。
        ex.langfuse_trace_id = langfuse_handler.last_trace_id
    if error_type:
        ex.error_type = error_type
    if error_message:
        ex.error_message = error_message


async def handle_execution_failure(
    execution, execution_repo, start_time, error_type, error_message,
    assistant_msg=None, message_repo=None, content=None,
):
    end_time = timezone.now()
    duration_ms = int((end_time - start_time).total_seconds() * 1000)
    logger.warning("execution failed", extra={
        "request_id": execution.request_id, "error_type": error_type,
        "error_message": str(error_message)[:200], "duration_ms": duration_ms,
    })
    finalize_execution(
        execution, "failed", end_time, duration_ms,
        error_type=error_type, error_message=error_message,
    )
    await execution_repo.update(execution)
    if assistant_msg and message_repo:
        assistant_msg.status = 0
        assistant_msg.content = content or ""
        await message_repo.update(assistant_msg)


async def finalize_completion(
    execution, execution_repo, assistant_msg, message_repo,
    user_repo, full_response, end_time, duration_ms,
    user_id, tpt, tct, langfuse_handler, interrupted=False,
):
    from apps.chat.models import LangGraphExecution, Message

    if interrupted:
        full_response += "[已中断]"
        finalize_message(assistant_msg, full_response, Message.STATUS_INTERRUPTED, duration_ms, tpt, tct)
        await message_repo.update(assistant_msg)
        finalize_execution(execution, "interrupted", end_time, duration_ms)
        await execution_repo.update(execution)
        return full_response
    else:
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
        return full_response


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
