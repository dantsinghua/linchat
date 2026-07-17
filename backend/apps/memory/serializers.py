"""记忆序列化器"""

from django.conf import settings
from rest_framework import serializers

from apps.memory.models import UserMemory


class MemoryCreateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=settings.MEMORY_CONTENT_MAX_LENGTH)
    name = serializers.CharField(max_length=200, required=False, allow_null=True, allow_blank=True, default=None)


class MemoryUpdateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=settings.MEMORY_CONTENT_MAX_LENGTH)


class InternalIngestSerializer(serializers.Serializer):
    """内部摄入端点入参（设备 token 鉴权，不属对外 API 契约）。"""
    content = serializers.CharField(max_length=settings.MEMORY_CONTENT_MAX_LENGTH)
    name = serializers.CharField(max_length=200)
    tag = serializers.CharField(max_length=100, required=False, allow_null=True, default=None)
    source = serializers.ChoiceField(choices=["wechat", "oa"], default="wechat")


class MemoryResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserMemory
        fields = ["id", "type", "name", "content", "embedding_status", "tags", "created_at", "updated_at"]


class MemorySearchSerializer(serializers.Serializer):
    query = serializers.CharField(max_length=settings.MEMORY_CONTENT_MAX_LENGTH)
    limit = serializers.IntegerField(min_value=1, max_value=20, default=5, required=False)


class MemorySearchResultSerializer(MemoryResponseSerializer):
    """搜索结果序列化器 [T033] — 继承 MemoryResponseSerializer + score + match_type"""
    score = serializers.FloatField()
    match_type = serializers.CharField()

    class Meta(MemoryResponseSerializer.Meta):
        fields = MemoryResponseSerializer.Meta.fields + ["score", "match_type"]


class MemoryListQuerySerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=UserMemory.MemoryType.choices, required=False)
    page = serializers.IntegerField(min_value=1, default=1, required=False)
    page_size = serializers.IntegerField(min_value=1, max_value=100, default=20, required=False)
