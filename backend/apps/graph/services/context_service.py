import asyncio
import logging
from typing import Any, Optional

from django.conf import settings
from redis.exceptions import LockError, RedisError

from apps.context import (COMPACTION_PROMPT_TEMPLATE, PromptBuilder,
                          PromptConfig, RetrievedMemory, TrimLevel,
                          count_tokens, trim_messages_to_budget)
from core.redis import get_redis

logger = logging.getLogger(__name__)
MIN_EFFECTIVE_WINDOW = 10000; COMPRESS_LOCK_TIMEOUT = 60


class ContextWindowTooSmallError(Exception): pass


def _total_tokens(messages: list[dict[str, str]]) -> int:
    return sum(count_tokens(msg.get("content", "")) for msg in messages)


def _filter_by_trim_level(messages, level):
    target, remaining = [], []
    for i, msg in enumerate(messages):
        role, name = msg.get("role", ""), msg.get("name", "")
        if level == TrimLevel.FIRST:
            is_last = role == "user" and i == len(messages) - 1
            matched = role in ("user", "assistant") and name not in ("memory", "tools", "compaction") and not is_last
        elif level == TrimLevel.SECOND:
            matched = name == "tools"
        else:
            matched = name in ("memory", "compaction")
        (target if matched else remaining).append(msg)
    return target, remaining


class ContextService:
    @staticmethod
    def get_effective_window(model_config: dict[str, Any]) -> int:
        effective = int(model_config.get("max_context_window", 128000) * 0.9)
        if effective < MIN_EFFECTIVE_WINDOW:
            raise ContextWindowTooSmallError(f"有效窗口 {effective} < {MIN_EFFECTIVE_WINDOW}")
        return effective

    @staticmethod
    def check_token_limit(messages, effective_window: int) -> bool:
        return _total_tokens(messages) > effective_window

    @staticmethod
    async def _llm_compress(content: str, retries: int = 3) -> Optional[str]:
        from apps.graph.agent import get_llm
        prompt = COMPACTION_PROMPT_TEMPLATE.format(conversation_text=content)
        for attempt in range(retries):
            try:
                llm = await get_llm()
                response = await llm.ainvoke(prompt)
                if response and response.content: return str(response.content)
            except Exception as e:
                logger.warning("LLM compress attempt %d/%d failed: %s", attempt + 1, retries, e)
        return None

    @staticmethod
    async def compress_context(user_id: int, messages, effective_window: int,
                               sse_callback=None) -> tuple[list, Optional[str]]:
        r = await get_redis()
        lock = r.lock(f"compress:{user_id}", timeout=COMPRESS_LOCK_TIMEOUT, blocking=False)
        acquired = await lock.acquire()
        if not acquired:
            for _ in range(60):
                if not await r.exists(f"compress:{user_id}"): break
                await asyncio.sleep(1.0)
            if not ContextService.check_token_limit(messages, effective_window):
                return messages, None
            lock = r.lock(f"compress:{user_id}", timeout=COMPRESS_LOCK_TIMEOUT, blocking=False)
            acquired = await lock.acquire()
        compaction_summary = None
        try:
            if sse_callback:
                try: await sse_callback("context_compacting")
                except Exception: pass
            compressed_parts = []
            for level, label, use_llm in [
                (TrimLevel.FIRST, "L1", True), (TrimLevel.SECOND, "L2", False), (TrimLevel.LAST, "L3", False)]:
                if not ContextService.check_token_limit(messages, effective_window): break
                target, remaining = _filter_by_trim_level(messages, level)
                if not target: continue
                if use_llm:
                    text = "\n".join(m.get("content", "") for m in target)
                    compressed = await ContextService._llm_compress(text)
                    if compressed:
                        compressed_parts.append(text)
                        summary_msg = {"role": "system", "content": f"[之前的对话摘要]\n{compressed}", "name": "compaction"}
                        messages = list(remaining)
                        pos = max((i + 1 for i, m in enumerate(messages) if m.get("role") == "system"), default=0)
                        messages.insert(pos, summary_msg)
                    else:
                        messages = remaining
                else:
                    messages = remaining
            if ContextService.check_token_limit(messages, effective_window):
                messages = trim_messages_to_budget(messages, effective_window)
            if compressed_parts:
                summary = await ContextService._llm_compress("\n\n".join(compressed_parts))
                if summary:
                    compaction_summary = summary
                    try:
                        from apps.memory.services import MemoryService
                        await MemoryService.create_memory(user_id=user_id, content=summary, type="compaction")
                    except Exception as e:
                        logger.warning("Failed to create compaction memory: %s", e)
            if sse_callback:
                try: await sse_callback("context_compacted")
                except Exception: pass
        finally:
            if acquired:
                try: await lock.release()
                except (RedisError, LockError): pass
        return messages, compaction_summary

    @staticmethod
    async def build_context(user_id: int, user_message: str, model_config: dict,
                            conversation_history=None, compaction_summary=None,
                            sse_callback=None) -> list:
        effective_window = ContextService.get_effective_window(model_config)
        retrieved_memories = None
        try:
            from apps.memory.services import MemoryService
            results = await MemoryService.search_memory(
                user_id=user_id, query=user_message, limit=settings.MEMORY_SEARCH_TOP_K)
            if results:
                retrieved_memories = [
                    RetrievedMemory(content=r["memory"].content, memory_type=r["memory"].type, relevance_score=r["score"])
                    for r in results]
        except Exception as e:
            logger.warning("Memory recall failed: %s", e)
        config = PromptConfig(
            user_id=user_id, max_context_window=model_config.get("max_context_window", 128000),
            model_name=model_config.get("name", ""))
        messages = PromptBuilder(config=config).build_messages(
            user_input=user_message, conversation_history=conversation_history,
            retrieved_memories=retrieved_memories, compaction_summary=compaction_summary)
        if ContextService.check_token_limit(messages, effective_window):
            messages, _ = await ContextService.compress_context(
                user_id=user_id, messages=messages,
                effective_window=effective_window, sse_callback=sse_callback)
        if _total_tokens(messages) > model_config.get("max_context_window", 128000):
            messages = trim_messages_to_budget(messages, effective_window)
        return messages
