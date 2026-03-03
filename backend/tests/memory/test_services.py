"""
服务层测试 [T026] [T036]

MemoryService CRUD 全路径覆盖，mock Celery 任务投递和 EmbeddingClient
T036: search_memory 混合搜索测试
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asgiref.sync import async_to_sync

from apps.memory.models import UserMemory
from apps.memory.services import MemoryNotFoundError, MemoryService

pytestmark = pytest.mark.django_db

# 辅助：将 async 方法包装为同步调用（asgiref 正确管理线程间 DB 连接）
_create_memory = async_to_sync(MemoryService.create_memory)
_update_memory = async_to_sync(MemoryService.update_memory)
_delete_memory = async_to_sync(MemoryService.delete_memory)
_get_memory = async_to_sync(MemoryService.get_memory)
_list_memories = async_to_sync(MemoryService.list_memories)
_search_memory = async_to_sync(MemoryService.search_memory)
_summarize = async_to_sync(MemoryService.summarize_and_store)


@pytest.fixture(autouse=True)
def _clean_user_memory():
    """每个测试前清理 UserMemory 数据，防止 --reuse-db 数据泄漏"""
    UserMemory.objects.all().delete()
    yield


class TestEmbeddingClientConfig:
    """EmbeddingClient 配置测试"""

    @patch("apps.memory.services.EmbeddingClient._get_embedding_config")
    def test_client_timeout_and_retries(self, mock_config: MagicMock) -> None:
        """验证 openai 客户端配置了足够的超时和重试"""
        mock_config.return_value = {
            "api_key": "test",
            "url": "http://localhost:8100/v1",
            "name": "qwen3-embedding",
            "max_input_tokens": 8192,
        }
        with patch("openai.AsyncOpenAI") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.embeddings.create.return_value = MagicMock(
                data=[MagicMock(embedding=[0.1] * 1024)]
            )
            mock_cls.return_value = mock_instance

            from apps.memory.services import EmbeddingClient

            async_to_sync(EmbeddingClient.generate_embedding)("test")

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["max_retries"] == 3
            assert call_kwargs["timeout"].connect == 40.0


class TestMemoryServiceCreate:
    """MemoryService.create_memory 测试"""

    @patch("apps.memory.services.generate_embedding", create=True)
    def test_create_memory_success(self, mock_task: MagicMock) -> None:
        """创建记忆成功，投递 Celery 任务"""
        with patch("apps.memory.tasks.generate_embedding") as mock_celery:
            mock_celery.delay = MagicMock()
            memory = _create_memory(user_id=1, content="测试记忆")

        assert memory.id is not None
        assert memory.user_id == 1
        assert memory.content == "测试记忆"
        assert memory.type == UserMemory.MemoryType.MEMORY
        assert memory.embedding_status in (
            UserMemory.EmbeddingStatus.PENDING,
            UserMemory.EmbeddingStatus.FAILED,
        )

    def test_create_with_custom_type(self) -> None:
        """系统内部创建可指定 type"""
        with patch("apps.memory.tasks.generate_embedding") as mock_celery:
            mock_celery.delay = MagicMock()
            memory = _create_memory(
                user_id=1,
                content="压缩摘要",
                type=UserMemory.MemoryType.COMPACTION,
            )

        assert memory.type == UserMemory.MemoryType.COMPACTION

    def test_create_celery_unavailable_degrades(self) -> None:
        """Celery 不可用时降级标记 failed 不阻塞"""
        with patch(
            "apps.memory.tasks.generate_embedding"
        ) as mock_celery:
            mock_celery.delay.side_effect = Exception("Celery down")
            memory = _create_memory(user_id=1, content="test")

        assert memory.embedding_status == UserMemory.EmbeddingStatus.FAILED


class TestMemoryServiceUpdate:
    """MemoryService.update_memory 测试"""

    def test_update_memory_success(self) -> None:
        memory = UserMemory.objects.create(
            user_id=1,
            content="old",
            embedding_status=UserMemory.EmbeddingStatus.DONE,
            retry_count=2,
        )

        with patch("apps.memory.tasks.generate_embedding") as mock_celery:
            mock_celery.delay = MagicMock()
            updated = _update_memory(
                memory_id=memory.id, user_id=1, content="new"
            )

        assert updated.content == "new"
        assert updated.embedding_status == UserMemory.EmbeddingStatus.PENDING
        assert updated.retry_count == 0

    def test_update_memory_not_found(self) -> None:
        with pytest.raises(MemoryNotFoundError):
            _update_memory(memory_id=99999, user_id=1, content="test")

    def test_update_wrong_user(self) -> None:
        """用户隔离：不能更新他人记忆"""
        memory = UserMemory.objects.create(user_id=1, content="test")

        with pytest.raises(MemoryNotFoundError):
            _update_memory(memory_id=memory.id, user_id=999, content="hack")


class TestMemoryServiceDelete:
    """MemoryService.delete_memory 测试"""

    def test_delete_success(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="test")

        result = _delete_memory(memory_id=memory.id, user_id=1)
        assert result is True
        assert UserMemory.objects.filter(id=memory.id).count() == 0

    def test_delete_not_found(self) -> None:
        with pytest.raises(MemoryNotFoundError):
            _delete_memory(memory_id=99999, user_id=1)

    def test_delete_wrong_user(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="test")

        with pytest.raises(MemoryNotFoundError):
            _delete_memory(memory_id=memory.id, user_id=999)


class TestMemoryServiceQuery:
    """MemoryService.get_memory / list_memories 测试"""

    def test_get_memory_success(self) -> None:
        memory = UserMemory.objects.create(user_id=1, content="test")

        fetched = _get_memory(memory_id=memory.id, user_id=1)
        assert fetched.id == memory.id

    def test_get_memory_not_found(self) -> None:
        with pytest.raises(MemoryNotFoundError):
            _get_memory(memory_id=99999, user_id=1)

    def test_list_memories(self) -> None:
        for i in range(3):
            UserMemory.objects.create(user_id=1, content=f"item {i}")
        UserMemory.objects.create(user_id=2, content="other")

        memories, total = _list_memories(user_id=1)
        assert total == 3
        assert len(memories) == 3

    def test_list_with_type_filter(self) -> None:
        UserMemory.objects.create(user_id=1, content="a", type="memory")
        UserMemory.objects.create(user_id=1, content="b", type="compaction")

        memories, total = _list_memories(user_id=1, type_filter="memory")
        assert total == 1


class TestMemoryServiceSearchSkipVector:
    """search_memory skip_vector 参数测试"""

    MOCK_EMBEDDING = [0.1] * 1024

    @patch("apps.memory.services.embedding_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_skip_vector_true_no_embedding_call(
        self,
        mock_embed: AsyncMock,
        mock_keyword: AsyncMock,
    ) -> None:
        """skip_vector=True 时不调用 EmbeddingClient.generate_embedding"""
        m1 = UserMemory.objects.create(
            user_id=1, content="test data", embedding_status="done"
        )
        mock_keyword.return_value = [(m1.id, 0.7)]

        results = _search_memory(user_id=1, query="test", skip_vector=True)

        mock_embed.assert_not_called()
        assert len(results) == 1
        assert results[0]["match_type"] == "keyword"

    @patch("apps.memory.services.embedding_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.memory.services.embedding_repo.vector_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_skip_vector_false_calls_embedding(
        self,
        mock_embed: AsyncMock,
        mock_vector: AsyncMock,
        mock_keyword: AsyncMock,
    ) -> None:
        """skip_vector=False（默认）时正常调用 embedding"""
        m1 = UserMemory.objects.create(
            user_id=1, content="test data", embedding_status="done"
        )
        mock_embed.return_value = self.MOCK_EMBEDDING
        mock_vector.return_value = [(m1.id, 0.9)]
        mock_keyword.return_value = [(m1.id, 0.5)]

        results = _search_memory(user_id=1, query="test", skip_vector=False)

        mock_embed.assert_called_once()
        assert len(results) == 1
        assert results[0]["match_type"] == "hybrid"


class TestMemoryServiceSearch:
    """MemoryService.search_memory 混合搜索测试 [T036]"""

    MOCK_EMBEDDING = [0.1] * 1024

    @patch("apps.memory.services.embedding_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.memory.services.embedding_repo.vector_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_hybrid_search(
        self,
        mock_embed: AsyncMock,
        mock_vector: AsyncMock,
        mock_keyword: AsyncMock,
    ) -> None:
        """向量+关键词混合合并，权重 0.7/0.3"""
        m1 = UserMemory.objects.create(
            user_id=1, content="python 编程", embedding_status="done"
        )
        m2 = UserMemory.objects.create(
            user_id=1, content="java 编程", embedding_status="done"
        )

        mock_embed.return_value = self.MOCK_EMBEDDING
        # m1 向量得分高，m2 关键词得分高
        mock_vector.return_value = [(m1.id, 0.9), (m2.id, 0.5)]
        mock_keyword.return_value = [(m2.id, 0.8), (m1.id, 0.3)]

        results = _search_memory(user_id=1, query="编程")

        assert len(results) == 2
        # m1: 0.9*0.7 + 0.3*0.3 = 0.72, m2: 0.5*0.7 + 0.8*0.3 = 0.59
        assert results[0]["memory"].id == m1.id
        assert results[0]["match_type"] == "hybrid"

    @patch("apps.memory.services.embedding_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.memory.services.embedding_repo.vector_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_empty_results(
        self,
        mock_embed: AsyncMock,
        mock_vector: AsyncMock,
        mock_keyword: AsyncMock,
    ) -> None:
        """搜索无结果"""
        mock_embed.return_value = self.MOCK_EMBEDDING
        mock_vector.return_value = []
        mock_keyword.return_value = []

        results = _search_memory(user_id=1, query="不存在")
        assert results == []

    @patch("apps.memory.services.embedding_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.memory.services.embedding_repo.vector_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_user_isolation(
        self,
        mock_embed: AsyncMock,
        mock_vector: AsyncMock,
        mock_keyword: AsyncMock,
    ) -> None:
        """搜索结果用户隔离"""
        m1 = UserMemory.objects.create(
            user_id=1, content="user1 data", embedding_status="done"
        )
        UserMemory.objects.create(
            user_id=2, content="user2 data", embedding_status="done"
        )

        mock_embed.return_value = self.MOCK_EMBEDDING
        mock_vector.return_value = [(m1.id, 0.8)]
        mock_keyword.return_value = []

        results = _search_memory(user_id=1, query="data")
        assert len(results) == 1
        assert results[0]["memory"].user_id == 1

    @patch("apps.memory.services.embedding_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_fallback_keyword_only(
        self,
        mock_embed: AsyncMock,
        mock_keyword: AsyncMock,
    ) -> None:
        """EmbeddingClient 不可用时回退为纯关键词搜索"""
        m1 = UserMemory.objects.create(
            user_id=1, content="keyword test", embedding_status="done"
        )

        mock_embed.side_effect = Exception("Embedding unavailable")
        mock_keyword.return_value = [(m1.id, 0.7)]

        results = _search_memory(user_id=1, query="keyword")

        assert len(results) == 1
        assert results[0]["match_type"] == "keyword"

    @patch("apps.memory.services.embedding_repo.keyword_search", new_callable=AsyncMock)
    @patch("apps.memory.services.embedding_repo.vector_search", new_callable=AsyncMock)
    @patch("apps.memory.services.EmbeddingClient.generate_embedding", new_callable=AsyncMock)
    def test_topk_limit(
        self,
        mock_embed: AsyncMock,
        mock_vector: AsyncMock,
        mock_keyword: AsyncMock,
    ) -> None:
        """结果数量不超过 limit"""
        memories = []
        for i in range(10):
            m = UserMemory.objects.create(
                user_id=1, content=f"item {i}", embedding_status="done"
            )
            memories.append(m)

        mock_embed.return_value = self.MOCK_EMBEDDING
        mock_vector.return_value = [(m.id, 0.9 - i * 0.05) for i, m in enumerate(memories)]
        mock_keyword.return_value = []

        results = _search_memory(user_id=1, query="item", limit=5)
        assert len(results) <= 5


class TestMemoryServiceSummarize:
    """MemoryService.summarize_and_store 测试 [T067]"""

    @patch("apps.memory.tasks.generate_embedding")
    @patch("apps.graph.agent.get_llm", new_callable=AsyncMock)
    def test_summarize_success(self, mock_get_llm, mock_celery) -> None:
        """成功总结并写入记忆"""
        mock_celery.delay = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"facts": ["用户喜欢 Python"]}'
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm_instance

        result = _summarize(
            user_id=1,
            content="我很喜欢用 Python 编程",
            summary_type="daily-summary",
            summary_name="daily-2024-01-01",
        )

        assert result is not None
        assert result.type == "daily-summary"
        assert result.name == "daily-2024-01-01"
        assert "Python" in result.content

    @patch("apps.memory.tasks.generate_embedding")
    @patch("apps.graph.agent.get_llm", new_callable=AsyncMock)
    def test_summarize_empty_content(self, mock_get_llm, mock_celery) -> None:
        """空内容不生成总结 [R-007]"""
        result = _summarize(
            user_id=1,
            content="",
            summary_type="daily-summary",
            summary_name="daily-2024-01-01",
        )

        assert result is None
        mock_get_llm.assert_not_called()

    @patch("apps.memory.tasks.generate_embedding")
    @patch("apps.graph.agent.get_llm", new_callable=AsyncMock)
    def test_summarize_llm_failure(self, mock_get_llm, mock_celery) -> None:
        """LLM 重试 3 次后跳过 [R-022]"""
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke.side_effect = Exception("LLM down")
        mock_get_llm.return_value = mock_llm_instance

        result = _summarize(
            user_id=1,
            content="some content",
            summary_type="daily-summary",
            summary_name="daily-2024-01-01",
        )

        assert result is None
        assert mock_llm_instance.ainvoke.call_count == 3

    @patch("apps.memory.tasks.generate_embedding")
    @patch("apps.graph.agent.get_llm", new_callable=AsyncMock)
    def test_summarize_json_parse_fallback(self, mock_get_llm, mock_celery) -> None:
        """JSON 解析失败时使用原始输出"""
        mock_celery.delay = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "这是一段纯文本摘要"
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm_instance

        result = _summarize(
            user_id=1,
            content="test content",
            summary_type="compaction",
            summary_name="compaction-1",
        )

        assert result is not None
        assert result.content == "这是一段纯文本摘要"

    @patch("apps.memory.tasks.generate_embedding")
    @patch("apps.graph.agent.get_llm", new_callable=AsyncMock)
    def test_summarize_empty_facts(self, mock_get_llm, mock_celery) -> None:
        """LLM 返回空 facts 列表"""
        mock_response = MagicMock()
        mock_response.content = '{"facts": []}'
        mock_llm_instance = AsyncMock()
        mock_llm_instance.ainvoke.return_value = mock_response
        mock_get_llm.return_value = mock_llm_instance

        result = _summarize(
            user_id=1,
            content="some content",
            summary_type="daily-summary",
            summary_name="daily-2024-01-01",
        )

        assert result is None
