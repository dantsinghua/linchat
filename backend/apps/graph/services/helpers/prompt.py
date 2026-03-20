import logging

from django.conf import settings

from apps.graph.prompts import PromptBuilder, PromptConfig, RetrievedMemory

logger = logging.getLogger(__name__)


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
        preamble, breakdown.total - breakdown.user_input,
        prompt_config.effective_window, breakdown,
        memory_results, model_name, max_context_window,
    )
