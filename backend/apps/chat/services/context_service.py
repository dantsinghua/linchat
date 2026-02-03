"""上下文管理服务 [T043] [T058-T061]

负责动态窗口计算、token 限制检查、上下文组装编排、优先级驱动的压缩流程。
"""

import asyncio
import logging
from typing import Any, Optional

import redis.asyncio as aioredis
from django.conf import settings

from apps.context import (COMPACTION_PROMPT_TEMPLATE, PromptBuilder,
                          PromptConfig, RetrievedMemory, TrimLevel,
                          count_tokens, trim_messages_to_budget)

logger = logging.getLogger(__name__)

MIN_EFFECTIVE_WINDOW = 10000
COMPRESS_LOCK_TIMEOUT = 60
COMPRESS_LOCK_WAIT_INTERVAL = 1.0
COMPRESS_LOCK_WAIT_MAX_RETRIES = 60


class ContextWindowTooSmallError(Exception):
    pass


def _total_tokens(messages: list[dict[str, str]]) -> int:
    return sum(count_tokens(msg.get("content", "")) for msg in messages)


def _filter_by_trim_level(
    messages: list[dict[str, str]], level: TrimLevel
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """将消息分为指定级别的和其余的"""
    target, remaining = [], []
    for i, msg in enumerate(messages):
        role, name = msg.get("role", ""), msg.get("name", "")
        matched = False
        if level == TrimLevel.FIRST:
            is_last_user = role == "user" and i == len(messages) - 1
            matched = (role in ("user", "assistant") and name not in ("memory", "tools", "compaction") and not is_last_user)
        elif level == TrimLevel.SECOND:
            matched = name == "tools"
        elif level == TrimLevel.LAST:
            matched = name in ("memory", "compaction")
        (target if matched else remaining).append(msg)
    return target, remaining


class ContextService:
    """上下文管理服务"""

    @staticmethod
    def get_effective_window(model_config: dict[str, Any]) -> int:
        effective = int(model_config.get("max_context_window", 128000) * 0.9)
        if effective < MIN_EFFECTIVE_WINDOW:
            raise ContextWindowTooSmallError(f"有效窗口 {effective} < {MIN_EFFECTIVE_WINDOW}")
        return effective

    @staticmethod
    def check_token_limit(messages: list[dict[str, str]], effective_window: int) -> bool:
        return _total_tokens(messages) > effective_window

    @staticmethod
    async def _acquire_compress_lock(user_id: int) -> Optional[Any]:
        try:
            r = aioredis.from_url(settings.REDIS_URL)
            lock = r.lock(f"compress:{user_id}", timeout=COMPRESS_LOCK_TIMEOUT, blocking=False)
            return lock if await lock.acquire() else None
        except Exception as e:
            logger.warning("Redis lock failed for user %d: %s", user_id, e)
            return None

    @staticmethod
    async def _release_compress_lock(lock: Any) -> None:
        if lock is None:
            return
        try:
            await lock.release()
        except Exception as e:
            logger.warning("Failed to release compress lock: %s", e)

    @staticmethod
    async def _wait_for_compress_lock(user_id: int, effective_window: int) -> bool:
        try:
            r = aioredis.from_url(settings.REDIS_URL)
            for _ in range(COMPRESS_LOCK_WAIT_MAX_RETRIES):
                if not await r.exists(f"compress:{user_id}"):
                    return True
                await asyncio.sleep(COMPRESS_LOCK_WAIT_INTERVAL)
            return True
        except Exception:
            return True

    @staticmethod
    async def _llm_compress(content: str, retries: int = 3) -> Optional[str]:
        from apps.graph.agent import get_llm

        prompt = COMPACTION_PROMPT_TEMPLATE.format(conversation_text=content)
        for attempt in range(retries):
            try:
                llm = await get_llm()
                response = await llm.ainvoke(prompt)
                if response and response.content:
                    return str(response.content)
            except Exception as e:
                logger.warning("LLM compress attempt %d/%d failed: %s", attempt + 1, retries, e)
        return None

    @staticmethod
    async def compress_context(
        user_id: int, messages: list[dict[str, str]],
        effective_window: int, sse_callback: Optional[Any] = None,
    ) -> tuple[list[dict[str, str]], Optional[str]]:
        """优先级驱动的上下文压缩 [T058]"""
        lock = await ContextService._acquire_compress_lock(user_id)
        if lock is None:
            still_needed = await ContextService._wait_for_compress_lock(user_id, effective_window)
            if not still_needed or not ContextService.check_token_limit(messages, effective_window):
                return messages, None
            lock = await ContextService._acquire_compress_lock(user_id)

        compaction_summary = None
        try:
            if sse_callback:
                try:
                    await sse_callback("context_compacting")
                except Exception:
                    pass

            compressed_content_parts = []

            # L1→L2→L3 逐级压缩
            for level, label, use_llm in [
                (TrimLevel.FIRST, "L1 对话历史", True),
                (TrimLevel.SECOND, "L2 工具内容", False),
                (TrimLevel.LAST, "L3 记忆内容", False),
            ]:
                if not ContextService.check_token_limit(messages, effective_window):
                    break
                target, remaining = _filter_by_trim_level(messages, level)
                if not target:
                    continue

                if use_llm:
                    target_text = "\n".join(m.get("content", "") for m in target)
                    compressed = await ContextService._llm_compress(target_text)
                    if compressed:
                        compressed_content_parts.append(target_text)
                        summary_msg = {"role": "system", "content": f"[之前的对话摘要]\n{compressed}", "name": "compaction"}
                        messages = list(remaining)
                        insert_pos = max((i + 1 for i, m in enumerate(messages) if m.get("role") == "system"), default=0)
                        messages.insert(insert_pos, summary_msg)
                    else:
                        messages = remaining
                        logger.info("%s LLM compress failed, truncated", label)
                else:
                    messages = remaining
                    logger.info("%s removed to fit budget", label)

            # 仍超限则直接截断
            if ContextService.check_token_limit(messages, effective_window):
                messages = trim_messages_to_budget(messages, effective_window)

            # 生成 compaction 记忆
            if compressed_content_parts:
                summary = await ContextService._llm_compress("\n\n".join(compressed_content_parts))
                if summary:
                    compaction_summary = summary
                    try:
                        from apps.memory.services import MemoryService
                        await MemoryService.create_memory(user_id=user_id, content=summary, type="compaction")
                    except Exception as e:
                        logger.warning("Failed to create compaction memory: %s", e)

            if sse_callback:
                try:
                    await sse_callback("context_compacted")
                except Exception:
                    pass
        finally:
            await ContextService._release_compress_lock(lock)

        return messages, compaction_summary

    @staticmethod
    async def build_context(
        user_id: int, user_message: str, model_config: dict[str, Any],
        conversation_history: Optional[list[dict[str, str]]] = None,
        compaction_summary: Optional[str] = None,
        sse_callback: Optional[Any] = None,
    ) -> list[dict[str, str]]:
        """构建完整上下文消息列表 [T043]"""
        effective_window = ContextService.get_effective_window(model_config)

        # 召回记忆
        retrieved_memories: Optional[list[RetrievedMemory]] = None
        try:
            from apps.memory.services import MemoryService
            results = await MemoryService.search_memory(user_id=user_id, query=user_message, limit=settings.MEMORY_SEARCH_TOP_K)
            if results:
                retrieved_memories = [
                    RetrievedMemory(content=r["memory"].content, memory_type=r["memory"].type, relevance_score=r["score"])
                    for r in results
                ]
        except Exception as e:
            logger.warning("Memory recall failed: %s", e)

        config = PromptConfig(
            user_id=user_id,
            max_context_window=model_config.get("max_context_window", 128000),
            model_name=model_config.get("name", ""),
        )
        messages = PromptBuilder(config=config).build_messages(
            user_input=user_message, conversation_history=conversation_history,
            retrieved_memories=retrieved_memories, compaction_summary=compaction_summary,
        )

        if ContextService.check_token_limit(messages, effective_window):
            messages, _ = await ContextService.compress_context(
                user_id=user_id, messages=messages,
                effective_window=effective_window, sse_callback=sse_callback,
            )

        max_window = model_config.get("max_context_window", 128000)
        if _total_tokens(messages) > max_window:
            messages = trim_messages_to_budget(messages, effective_window)

        return messages
