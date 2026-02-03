"""
Celery 任务测试 [T027]

generate_embedding 状态流转、重试机制、retry_failed_embeddings 扫描逻辑
活跃用户检查、语言模型预热
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from apps.memory.models import UserMemory, UserMemoryEmbedding
from apps.memory.tasks import generate_embedding, retry_failed_embeddings


MOCK_EMBEDDING = [0.1] * 1024


class TestGenerateEmbeddingActiveUsers(TransactionTestCase):
    """generate_embedding 活跃用户检查和语言模型预热测试"""

    @patch("apps.memory.tasks._has_active_users", return_value=True)
    def test_skips_when_active_users(self, mock_active: MagicMock) -> None:
        """有活跃用户时跳过 embedding 生成，不修改记忆状态"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test",
            embedding_status=UserMemory.EmbeddingStatus.PENDING,
        )

        generate_embedding(memory.id)

        memory.refresh_from_db()
        assert memory.embedding_status == UserMemory.EmbeddingStatus.PENDING

    @patch("apps.memory.tasks._warmup_language_model")
    @patch("apps.memory.tasks._has_active_users", return_value=False)
    def test_runs_when_no_active_users(
        self, mock_active: MagicMock, mock_warmup: MagicMock
    ) -> None:
        """无活跃用户时正常执行 embedding 生成"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test content",
            embedding_status=UserMemory.EmbeddingStatus.PENDING,
        )

        with patch(
            "apps.memory.services.EmbeddingClient.generate_embedding",
            new_callable=AsyncMock,
            return_value=MOCK_EMBEDDING,
        ):
            generate_embedding(memory.id)

        memory.refresh_from_db()
        assert memory.embedding_status == UserMemory.EmbeddingStatus.DONE

    @patch("apps.memory.tasks._warmup_language_model")
    @patch("apps.memory.tasks._has_active_users", return_value=False)
    def test_warmup_called_after_embedding(
        self, mock_active: MagicMock, mock_warmup: MagicMock
    ) -> None:
        """成功生成 embedding 后调用语言模型预热"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test",
            embedding_status=UserMemory.EmbeddingStatus.PENDING,
        )

        with patch(
            "apps.memory.services.EmbeddingClient.generate_embedding",
            new_callable=AsyncMock,
            return_value=MOCK_EMBEDDING,
        ):
            generate_embedding(memory.id)

        mock_warmup.assert_called_once()

    @patch("apps.memory.tasks._warmup_language_model", side_effect=Exception("warmup error"))
    @patch("apps.memory.tasks._has_active_users", return_value=False)
    def test_warmup_failure_does_not_affect_embedding(
        self, mock_active: MagicMock, mock_warmup: MagicMock
    ) -> None:
        """预热失败不影响 embedding 状态"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test",
            embedding_status=UserMemory.EmbeddingStatus.PENDING,
        )

        with patch(
            "apps.memory.services.EmbeddingClient.generate_embedding",
            new_callable=AsyncMock,
            return_value=MOCK_EMBEDDING,
        ):
            generate_embedding(memory.id)

        memory.refresh_from_db()
        assert memory.embedding_status == UserMemory.EmbeddingStatus.DONE


class TestGenerateEmbedding(TransactionTestCase):
    """generate_embedding Celery 任务测试"""

    def setUp(self):
        # 默认 mock _has_active_users 返回 False，避免影响原有测试
        self._active_patcher = patch("apps.memory.tasks._has_active_users", return_value=False)
        self._warmup_patcher = patch("apps.memory.tasks._warmup_language_model")
        self._active_patcher.start()
        self._warmup_patcher.start()

    def tearDown(self):
        self._active_patcher.stop()
        self._warmup_patcher.stop()

    def test_success_flow(self) -> None:
        """成功路径: pending → processing → done + 写入 embedding 记录"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="测试内容",
            embedding_status=UserMemory.EmbeddingStatus.PENDING,
        )

        with patch(
            "apps.memory.services.EmbeddingClient.generate_embedding",
            new_callable=AsyncMock,
            return_value=MOCK_EMBEDDING,
        ):
            generate_embedding(memory.id)

        memory.refresh_from_db()
        assert memory.embedding_status == UserMemory.EmbeddingStatus.DONE

        embeddings = UserMemoryEmbedding.objects.filter(memory_id=memory.id)
        assert embeddings.count() == 1
        assert embeddings[0].chunk_text == "测试内容"
        assert embeddings[0].user_id == 1
        assert embeddings[0].type == memory.type

    def test_memory_not_found(self) -> None:
        """记忆不存在时静默返回"""
        # 不应抛出异常
        generate_embedding(999999)

    @override_settings(MEMORY_EMBEDDING_MAX_RETRY=3)
    def test_retry_exhausted(self) -> None:
        """重试次数耗尽，标记为 failed"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test",
            embedding_status=UserMemory.EmbeddingStatus.FAILED,
            retry_count=3,
        )

        generate_embedding(memory.id)

        memory.refresh_from_db()
        assert memory.embedding_status == UserMemory.EmbeddingStatus.FAILED

    def test_embedding_api_failure_increments_retry(self) -> None:
        """API 调用失败: retry_count + 1, 状态变为 failed"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test",
            embedding_status=UserMemory.EmbeddingStatus.PENDING,
            retry_count=0,
        )

        with patch(
            "apps.memory.services.EmbeddingClient.generate_embedding",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            generate_embedding(memory.id)

        memory.refresh_from_db()
        assert memory.embedding_status == UserMemory.EmbeddingStatus.FAILED
        assert memory.retry_count == 1

    def test_replaces_old_embedding_on_update(self) -> None:
        """更新场景：先删除旧 embedding 再写入新的"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="old content",
            embedding_status=UserMemory.EmbeddingStatus.PENDING,
        )
        # 预先创建一条旧的 embedding
        UserMemoryEmbedding.objects.create(
            memory=memory,
            user_id=1,
            type="memory",
            chunk_text="old content",
        )

        with patch(
            "apps.memory.services.EmbeddingClient.generate_embedding",
            new_callable=AsyncMock,
            return_value=MOCK_EMBEDDING,
        ):
            generate_embedding(memory.id)

        # 应该只有一条新的 embedding
        embeddings = UserMemoryEmbedding.objects.filter(memory_id=memory.id)
        assert embeddings.count() == 1
        assert embeddings[0].chunk_text == "old content"  # content 来自 memory.content

    @override_settings(MEMORY_EMBEDDING_MAX_RETRY=3)
    def test_multiple_failures_reach_max_retry(self) -> None:
        """连续失败达到最大重试次数"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test",
            embedding_status=UserMemory.EmbeddingStatus.PENDING,
            retry_count=2,
        )

        with patch(
            "apps.memory.services.EmbeddingClient.generate_embedding",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            generate_embedding(memory.id)

        memory.refresh_from_db()
        assert memory.retry_count == 3
        assert memory.embedding_status == UserMemory.EmbeddingStatus.FAILED


class TestRetryFailedEmbeddings(TransactionTestCase):
    """retry_failed_embeddings 定时任务测试"""

    def setUp(self):
        self._active_patcher = patch("apps.memory.tasks._has_active_users", return_value=False)
        self._active_patcher.start()

    def tearDown(self):
        self._active_patcher.stop()

    @override_settings(
        MEMORY_EMBEDDING_MAX_RETRY=3,
        MEMORY_EMBEDDING_PENDING_TIMEOUT=300,
    )
    def test_skips_when_active_users(self) -> None:
        """有活跃用户时跳过整个重试扫描"""
        self._active_patcher.stop()
        with patch("apps.memory.tasks._has_active_users", return_value=True):
            UserMemory.objects.create(
                user_id=1,
                content="retryable",
                embedding_status=UserMemory.EmbeddingStatus.FAILED,
                retry_count=1,
            )

            with patch("apps.memory.tasks.generate_embedding.delay") as mock_delay:
                retry_failed_embeddings()

            mock_delay.assert_not_called()
        self._active_patcher.start()

    @override_settings(
        MEMORY_EMBEDDING_MAX_RETRY=3,
        MEMORY_EMBEDDING_PENDING_TIMEOUT=300,
    )
    def test_dispatches_failed_records(self) -> None:
        """扫描 failed 且 retry_count < max_retry 的记录"""
        m1 = UserMemory.objects.create(
            user_id=1,
            content="retryable",
            embedding_status=UserMemory.EmbeddingStatus.FAILED,
            retry_count=1,
        )
        # 超过重试上限的不应被扫描
        UserMemory.objects.create(
            user_id=1,
            content="exhausted",
            embedding_status=UserMemory.EmbeddingStatus.FAILED,
            retry_count=3,
        )

        with patch(
            "apps.memory.tasks.generate_embedding.delay"
        ) as mock_delay:
            retry_failed_embeddings()

        mock_delay.assert_called_once_with(m1.id)

    @override_settings(
        MEMORY_EMBEDDING_MAX_RETRY=3,
        MEMORY_EMBEDDING_PENDING_TIMEOUT=300,
    )
    def test_dispatches_timed_out_pending(self) -> None:
        """扫描超时 pending 记录"""
        m = UserMemory.objects.create(
            user_id=1,
            content="stuck",
            embedding_status=UserMemory.EmbeddingStatus.PENDING,
        )
        # 手动设置 updated_at 到过去
        UserMemory.objects.filter(id=m.id).update(
            updated_at=timezone.now() - timedelta(seconds=600)
        )

        with patch(
            "apps.memory.tasks.generate_embedding.delay"
        ) as mock_delay:
            retry_failed_embeddings()

        mock_delay.assert_called_once_with(m.id)

    @override_settings(
        MEMORY_EMBEDDING_MAX_RETRY=3,
        MEMORY_EMBEDDING_PENDING_TIMEOUT=300,
    )
    def test_no_dispatch_when_clean(self) -> None:
        """没有需要重试的记录时不投递任务"""
        UserMemory.objects.create(
            user_id=1,
            content="done",
            embedding_status=UserMemory.EmbeddingStatus.DONE,
        )

        with patch(
            "apps.memory.tasks.generate_embedding.delay"
        ) as mock_delay:
            retry_failed_embeddings()

        mock_delay.assert_not_called()


# ============================================================================
# 每日/每月总结任务测试 [T068]
# ============================================================================


class TestGenerateDailySummary(TransactionTestCase):
    """generate_daily_summary Celery 任务测试 [T068]"""

    def _create_memory_at(self, user_id, content, mem_type, target_time):
        """创建记忆并手动设置 created_at（绕过 auto_now_add）"""
        m = UserMemory.objects.create(
            user_id=user_id,
            content=content,
            type=mem_type,
        )
        UserMemory.objects.filter(id=m.id).update(created_at=target_time)
        return m

    @patch("apps.memory.services.MemoryService.summarize_and_store", new_callable=AsyncMock)
    def test_daily_summary_with_compaction(self, mock_summarize: AsyncMock) -> None:
        """有 compaction 记忆的用户触发每日总结"""
        from apps.memory.tasks import generate_daily_summary

        yesterday = timezone.now().date() - timedelta(days=1)
        start = timezone.make_aware(
            timezone.datetime.combine(yesterday, timezone.datetime.min.time())
        )

        self._create_memory_at(
            user_id=1,
            content="compaction data",
            mem_type=UserMemory.MemoryType.COMPACTION,
            target_time=start + timedelta(hours=2),
        )

        mock_summarize.return_value = MagicMock()

        generate_daily_summary()

        mock_summarize.assert_called_once()
        assert "compaction data" in str(mock_summarize.call_args)

    @patch("apps.memory.services.MemoryService.summarize_and_store", new_callable=AsyncMock)
    def test_daily_summary_no_active_users(self, mock_summarize: AsyncMock) -> None:
        """无活跃用户时不调用 summarize"""
        from apps.memory.tasks import generate_daily_summary

        generate_daily_summary()
        mock_summarize.assert_not_called()

    @patch("apps.memory.services.MemoryService.summarize_and_store", new_callable=AsyncMock)
    def test_daily_summary_error_per_user_no_crash(self, mock_summarize: AsyncMock) -> None:
        """单用户失败不影响其他用户"""
        from apps.memory.tasks import generate_daily_summary

        yesterday = timezone.now().date() - timedelta(days=1)
        start = timezone.make_aware(
            timezone.datetime.combine(yesterday, timezone.datetime.min.time())
        )

        self._create_memory_at(
            user_id=1,
            content="data1",
            mem_type=UserMemory.MemoryType.COMPACTION,
            target_time=start + timedelta(hours=1),
        )
        self._create_memory_at(
            user_id=2,
            content="data2",
            mem_type=UserMemory.MemoryType.COMPACTION,
            target_time=start + timedelta(hours=1),
        )

        # 第一个用户失败
        mock_summarize.side_effect = [Exception("user 1 failed"), MagicMock()]

        generate_daily_summary()

        assert mock_summarize.call_count == 2


class TestGenerateMonthlySummary(TransactionTestCase):
    """generate_monthly_summary Celery 任务测试 [T068]"""

    def _create_memory_at(self, user_id, content, mem_type, target_time):
        """创建记忆并手动设置 created_at"""
        m = UserMemory.objects.create(
            user_id=user_id,
            content=content,
            type=mem_type,
        )
        UserMemory.objects.filter(id=m.id).update(created_at=target_time)
        return m

    @patch("apps.memory.services.MemoryService.summarize_and_store", new_callable=AsyncMock)
    def test_monthly_summary_with_dailies(self, mock_summarize: AsyncMock) -> None:
        """有 daily-summary 的用户触发每月总结"""
        from apps.memory.tasks import generate_monthly_summary

        now = timezone.now()
        if now.month == 1:
            year, month = now.year - 1, 12
        else:
            year, month = now.year, now.month - 1

        start = timezone.make_aware(timezone.datetime(year, month, 1))

        self._create_memory_at(
            user_id=1,
            content="daily summary content",
            mem_type=UserMemory.MemoryType.DAILY_SUMMARY,
            target_time=start + timedelta(days=5),
        )

        mock_summarize.return_value = MagicMock()

        generate_monthly_summary()

        mock_summarize.assert_called_once()
        assert "daily summary content" in str(mock_summarize.call_args)

    @patch("apps.memory.services.MemoryService.summarize_and_store", new_callable=AsyncMock)
    def test_monthly_summary_no_active_users(self, mock_summarize: AsyncMock) -> None:
        """无活跃用户不调用"""
        from apps.memory.tasks import generate_monthly_summary

        generate_monthly_summary()
        mock_summarize.assert_not_called()

    @patch("apps.memory.services.MemoryService.summarize_and_store", new_callable=AsyncMock)
    def test_monthly_summary_error_per_user(self, mock_summarize: AsyncMock) -> None:
        """单用户失败不影响其他"""
        from apps.memory.tasks import generate_monthly_summary

        now = timezone.now()
        if now.month == 1:
            year, month = now.year - 1, 12
        else:
            year, month = now.year, now.month - 1

        start = timezone.make_aware(timezone.datetime(year, month, 1))

        self._create_memory_at(
            user_id=1,
            content="d1",
            mem_type=UserMemory.MemoryType.DAILY_SUMMARY,
            target_time=start + timedelta(days=1),
        )
        self._create_memory_at(
            user_id=2,
            content="d2",
            mem_type=UserMemory.MemoryType.DAILY_SUMMARY,
            target_time=start + timedelta(days=1),
        )

        mock_summarize.side_effect = [Exception("fail"), MagicMock()]

        generate_monthly_summary()

        assert mock_summarize.call_count == 2
