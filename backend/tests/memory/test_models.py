"""
模型单元测试 [T024]

UserMemory 字段验证、type TextChoices 枚举约束、
embedding_status 默认值、FK CASCADE 级联删除验证
"""

import pytest
from django.test import TestCase

from apps.memory.models import UserMemory, UserMemoryEmbedding


class TestUserMemoryModel(TestCase):
    """UserMemory 模型测试"""

    def setUp(self) -> None:
        """每个测试前清理 UserMemory*，防止 --reuse-db 跨测试残留"""
        super().setUp()
        UserMemoryEmbedding.objects.all().delete()
        UserMemory.objects.all().delete()

    def test_create_with_defaults(self) -> None:
        """创建记忆时默认值正确"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="测试记忆内容",
        )
        assert memory.type == UserMemory.MemoryType.MEMORY
        assert memory.embedding_status == UserMemory.EmbeddingStatus.PENDING
        assert memory.retry_count == 0
        assert memory.name is None
        assert memory.tags is None
        assert memory.importance_score is None
        assert memory.created_at is not None
        assert memory.updated_at is not None

    def test_memory_type_choices(self) -> None:
        """MemoryType TextChoices 枚举值验证"""
        assert UserMemory.MemoryType.MEMORY == "memory"
        assert UserMemory.MemoryType.COMPACTION == "compaction"
        assert UserMemory.MemoryType.DAILY_SUMMARY == "daily-summary"
        assert UserMemory.MemoryType.MONTHLY_SUMMARY == "monthly-summary"
        assert len(UserMemory.MemoryType.choices) == 4

    def test_embedding_status_choices(self) -> None:
        """EmbeddingStatus TextChoices 枚举值验证"""
        assert UserMemory.EmbeddingStatus.PENDING == "pending"
        assert UserMemory.EmbeddingStatus.PROCESSING == "processing"
        assert UserMemory.EmbeddingStatus.DONE == "done"
        assert UserMemory.EmbeddingStatus.FAILED == "failed"
        assert len(UserMemory.EmbeddingStatus.choices) == 4

    def test_str_representation(self) -> None:
        """__str__ 方法"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test",
        )
        assert "UserMemory(" in str(memory)
        assert "user=1" in str(memory)

    def test_create_with_all_types(self) -> None:
        """所有类型都能成功创建"""
        for type_value, _ in UserMemory.MemoryType.choices:
            memory = UserMemory.objects.create(
                user_id=1,
                content=f"测试 {type_value}",
                type=type_value,
            )
            assert memory.type == type_value


class TestUserMemoryEmbedding(TestCase):
    """UserMemoryEmbedding 模型测试"""

    def setUp(self) -> None:
        """每个测试前清理 UserMemory*，防止 --reuse-db 跨测试残留"""
        super().setUp()
        UserMemoryEmbedding.objects.all().delete()
        UserMemory.objects.all().delete()

    def test_create_embedding(self) -> None:
        """创建 embedding 记录"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test",
        )
        embedding = UserMemoryEmbedding.objects.create(
            memory=memory,
            user_id=1,
            type="memory",
            chunk_index=0,
            chunk_text="test",
        )
        assert embedding.memory_id == memory.id
        assert embedding.user_id == 1
        assert embedding.embedding is None  # nullable

    def test_cascade_delete(self) -> None:
        """FK CASCADE 级联删除验证"""
        memory = UserMemory.objects.create(
            user_id=1,
            content="test",
        )
        UserMemoryEmbedding.objects.create(
            memory=memory,
            user_id=1,
            type="memory",
        )
        assert UserMemoryEmbedding.objects.count() == 1

        # 删除 memory 应级联删除 embedding
        memory.delete()
        assert UserMemoryEmbedding.objects.count() == 0

    def test_str_representation(self) -> None:
        """__str__ 方法"""
        memory = UserMemory.objects.create(user_id=1, content="test")
        embedding = UserMemoryEmbedding.objects.create(
            memory=memory, user_id=1, type="memory"
        )
        assert "UserMemoryEmbedding(" in str(embedding)
