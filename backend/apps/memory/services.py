"""记忆服务层 — CRUD、混合搜索、总结、自动召回"""

import json
import logging
from typing import Any, Optional

from asgiref.sync import sync_to_async
from django.conf import settings

from apps.common.exceptions import BusinessException
from apps.memory.models import UserMemory
from apps.memory.repositories import embedding_repo, memory_repo

logger = logging.getLogger(__name__)


# 自定义异常
class EmbeddingConfigNotFoundError(BusinessException):
    default_message = "未配置 Embedding 模型，请先在模型配置中添加 type='embedding' 的配置"
    error_code = "EMBEDDING_CONFIG_NOT_FOUND"


class MemoryNotFoundError(BusinessException):
    default_message = "记忆不存在"
    error_code = "MEMORY_NOT_FOUND"


class MemoryPermissionError(BusinessException):
    default_message = "无权访问此记忆"
    error_code = "MEMORY_PERMISSION_DENIED"


class EmbeddingClient:
    """Embedding 向量生成客户端"""

    @staticmethod
    def _get_embedding_config() -> dict[str, Any]:
        from apps.models.services import model_service
        config = model_service.get_active_model("embedding")
        if not config:
            raise EmbeddingConfigNotFoundError()
        return config

    @staticmethod
    async def generate_embedding(text: str) -> list[float]:
        """生成 2048 维 embedding 向量，超限时 tiktoken 截断"""
        from apps.common.tokenizer import count_tokens

        config = await sync_to_async(EmbeddingClient._get_embedding_config)()
        max_input = config.get("max_input_tokens") or 8192

        if count_tokens(text) > max_input:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            text = enc.decode(enc.encode(text)[:max_input])
            logger.warning("Embedding text truncated to %d tokens", max_input)

        import httpx
        import openai
        client = openai.AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["url"],
            timeout=httpx.Timeout(40.0, connect=40.0),
            max_retries=3,
        )
        response = await client.embeddings.create(
            model=config["name"], input=text,
            dimensions=settings.MEMORY_EMBEDDING_DIMENSION,
        )

        embedding = response.data[0].embedding
        expected = settings.MEMORY_EMBEDDING_DIMENSION
        if len(embedding) != expected:
            raise ValueError(f"Embedding 维度不匹配: 期望 {expected}, 实际 {len(embedding)}")
        return embedding


class MemoryService:
    """记忆业务服务 — 所有方法强制 user_id 参数 [R-004]"""

    @staticmethod
    async def _dispatch_embedding(memory: UserMemory) -> None:
        """投递 Celery embedding 生成任务，失败时标记 failed 不阻塞"""
        try:
            from apps.memory.tasks import generate_embedding
            generate_embedding.delay(memory.id)
        except Exception as e:
            logger.warning("Failed to dispatch embedding task: memory_id=%d: %s", memory.id, e)
            memory.embedding_status = UserMemory.EmbeddingStatus.FAILED
            await memory_repo.update(memory)

    @staticmethod
    async def _get_or_404(memory_id: int, user_id: int) -> UserMemory:
        memory = await memory_repo.get_by_id(memory_id, user_id)
        if not memory:
            raise MemoryNotFoundError()
        return memory

    @staticmethod
    async def create_memory(
        user_id: int, content: str,
        name: Optional[str] = None, type: str = "memory",
        tag: Optional[str] = None,
    ) -> UserMemory:
        memory = UserMemory(
            user_id=user_id, content=content, name=name, type=type,
            embedding_status=UserMemory.EmbeddingStatus.PENDING, retry_count=0,
            tags=[tag] if tag else None,
        )
        memory = await memory_repo.create(memory)
        await MemoryService._dispatch_embedding(memory)
        return memory

    @staticmethod
    async def update_memory(
        memory_id: int, user_id: int, content: str,
        tag: Optional[str] = None,
    ) -> UserMemory:
        memory = await MemoryService._get_or_404(memory_id, user_id)
        memory.content = content
        memory.embedding_status = UserMemory.EmbeddingStatus.PENDING
        memory.retry_count = 0
        if tag is not None:
            memory.tags = [tag]
        memory = await memory_repo.update(memory)
        await MemoryService._dispatch_embedding(memory)
        return memory

    @staticmethod
    async def delete_memory(memory_id: int, user_id: int) -> bool:
        await MemoryService._get_or_404(memory_id, user_id)
        return await memory_repo.delete(memory_id, user_id)

    @staticmethod
    async def get_memory(memory_id: int, user_id: int) -> UserMemory:
        return await MemoryService._get_or_404(memory_id, user_id)

    @staticmethod
    async def list_memories(
        user_id: int, type_filter: Optional[str] = None,
        page: int = 1, page_size: int = 20,
    ) -> tuple[list[UserMemory], int]:
        return await memory_repo.list_by_user(
            user_id=user_id, type_filter=type_filter, page=page, page_size=page_size,
        )

    @staticmethod
    async def search_memory(
        user_id: int, query: str, limit: int = 5,
        skip_vector: bool = False,
    ) -> list[dict[str, Any]]:
        """混合搜索: final_score = vector × 0.7 + keyword × 0.3，降级为纯关键词

        Args:
            skip_vector: 为 True 时跳过向量搜索，仅使用关键词搜索。
                聊天路径传 True 可避免触发 embedding API 导致单 GPU 模型热切换。
        """
        vw, kw = settings.MEMORY_VECTOR_WEIGHT, settings.MEMORY_KEYWORD_WEIGHT
        candidate_limit = limit * 2

        # 向量搜索
        vector_results: dict[int, float] = {}
        if not skip_vector:
            try:
                qe = await EmbeddingClient.generate_embedding(query)
                vector_results = dict(await embedding_repo.vector_search(user_id, qe, candidate_limit))
            except Exception as e:
                logger.warning("Vector search fallback to keyword-only: %s", e)

        # 关键词搜索
        keyword_results: dict[int, float] = {}
        try:
            keyword_results = dict(await embedding_repo.keyword_search(user_id, query, candidate_limit))
        except Exception as e:
            logger.warning("Keyword search failed: %s", e)

        # 合并评分
        all_ids = set(vector_results) | set(keyword_results)
        scored = []
        for mid in all_ids:
            vs, ks = vector_results.get(mid, 0.0), keyword_results.get(mid, 0.0)
            match = "hybrid" if mid in vector_results and mid in keyword_results else (
                "vector" if mid in vector_results else "keyword")
            scored.append((mid, vs * vw + ks * kw, match))

        scored.sort(key=lambda x: x[1], reverse=True)
        scored = scored[:limit]
        if not scored:
            return []

        # 批量获取 memory 对象
        memories_map = await memory_repo.batch_get_by_ids([m for m, _, _ in scored], user_id)
        return [
            {"memory": memories_map[mid], "score": round(s, 4), "match_type": mt}
            for mid, s, mt in scored if mid in memories_map
        ]

    @staticmethod
    async def summarize_and_store(
        user_id: int, content: str, summary_type: str, summary_name: str,
    ) -> Optional[UserMemory]:
        """LLM 事实抽取 + 存储总结，失败重试 3 次后跳过"""
        if not content or not content.strip():
            return None

        from apps.graph.agent import get_llm
        from apps.graph.prompts import CRONMEM_PROMPT_TEMPLATE

        # 获取现有记忆用于去重
        existing = ""
        try:
            memories, _ = await MemoryService.list_memories(user_id, type_filter="memory", page_size=20)
            if memories:
                existing = "\n".join(f"- {m.content}" for m in memories)
        except Exception:
            pass

        prompt = (
            CRONMEM_PROMPT_TEMPLATE
            .replace("{existing_memories}", existing or "无现有记忆")
            .replace("{conversation_text}", content)
        )

        # LLM 调用，重试 3 次
        summary_content = None
        for attempt in range(3):
            try:
                llm = await get_llm()
                response = await llm.ainvoke(prompt)
                if not (response and response.content):
                    logger.warning(
                        "Summarize attempt %d/3 empty response: user=%d, type=%s",
                        attempt + 1, user_id, summary_type,
                    )
                    continue
                raw = str(response.content)
                logger.debug(
                    "Summarize raw response: user=%d, type=%s, raw=%s",
                    user_id, summary_type, raw[:500],
                )
                # 解析 JSON，提取 facts 列表
                # LLM 可能返回 markdown 代码块包裹的 JSON，先清理
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                try:
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, dict):
                        facts = parsed.get("facts", [])
                        summary_content = "\n".join(facts) if facts else None
                    elif isinstance(parsed, list):
                        # LLM 直接返回了数组而非 {"facts": [...]}
                        summary_content = "\n".join(str(f) for f in parsed) if parsed else None
                    else:
                        # 合法 JSON 但不是 dict/list（如纯字符串），降级为原始文本
                        summary_content = raw
                except (json.JSONDecodeError, ValueError):
                    # JSON 解析失败，降级为原始文本
                    summary_content = raw
                if summary_content:
                    break
            except Exception as e:
                logger.warning(
                    "Summarize attempt %d/3 failed: user=%d, type=%s: %s",
                    attempt + 1, user_id, summary_type, e,
                )

        if not summary_content:
            logger.warning(
                "Summarize failed after retries: user=%d, type=%s, content_len=%d",
                user_id, summary_type, len(content),
            )
            return None

        try:
            return await MemoryService.create_memory(
                user_id=user_id, content=summary_content,
                name=summary_name, type=summary_type,
            )
        except Exception as e:
            logger.warning("Failed to create summary memory: %s", e)
            return None

    @staticmethod
    async def retrieve_relevant_memories(
        user_id: int, query: str, limit: int = 5,
    ) -> Optional[str]:
        """自动召回记忆并格式化为上下文字符串"""
        results = await MemoryService.search_memory(user_id, query, limit)
        if not results:
            return None
        lines = ["[用户记忆]"] + [
            f"{i}. {item['memory'].content}" for i, item in enumerate(results, 1)
        ]
        return "\n".join(lines)
