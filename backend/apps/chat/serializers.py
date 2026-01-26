"""
聊天模块序列化器

参考:
- rule-model.md#R_MSG_001 消息长度限制规则
- rule-model.md#R_MSG_002 空消息拦截规则
"""

from django.conf import settings
from rest_framework import serializers


class ChatRequestSerializer(serializers.Serializer):
    """
    聊天请求序列化器

    参考: rule-model.md#R_MSG_001 - 消息长度≤4000字符
    """

    content = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=settings.MAX_MESSAGE_LENGTH,
        error_messages={
            "required": "消息内容不能为空",
            "blank": "消息内容不能为空",
            "max_length": f"消息长度不能超过{settings.MAX_MESSAGE_LENGTH}字符",
        },
    )


class StopGenerationRequestSerializer(serializers.Serializer):
    """停止生成请求序列化器"""

    request_id = serializers.CharField(
        required=True,
        max_length=64,
        error_messages={
            "required": "请求ID不能为空",
        },
    )


class ResumeGenerationRequestSerializer(serializers.Serializer):
    """
    继续生成请求序列化器

    参考: behavior-model.md#2.5 继续生成（B_CHAT_005）
    用于 status=3（中断）消息的继续生成
    """

    request_id = serializers.CharField(
        required=True,
        max_length=64,
        error_messages={
            "required": "请求ID不能为空",
        },
    )


class ReconnectRequestSerializer(serializers.Serializer):
    """
    重连请求序列化器

    参考: behavior-model.md#2.4 流式响应重连（B_CHAT_004）
    用于 status=2（生成中）消息的 SSE 重连
    """

    request_id = serializers.CharField(
        required=True,
        max_length=64,
        error_messages={
            "required": "请求ID不能为空",
        },
    )


class HistoryQuerySerializer(serializers.Serializer):
    """历史消息查询序列化器"""

    limit = serializers.IntegerField(
        required=False,
        default=50,
        min_value=1,
        max_value=100,
        error_messages={
            "min_value": "limit 最小为 1",
            "max_value": "limit 最大为 100",
        },
    )
    before_sequence = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        error_messages={
            "min_value": "before_sequence 最小为 1",
        },
    )


class MessageResponseSerializer(serializers.Serializer):
    """消息响应序列化器"""

    message_id = serializers.IntegerField()
    message_uuid = serializers.CharField()
    role = serializers.CharField()
    content = serializers.CharField()
    status = serializers.IntegerField()
    sequence = serializers.IntegerField()
    created_time = serializers.CharField()
    request_id = serializers.CharField(allow_null=True)
    model_name = serializers.CharField(allow_null=True)
    response_time_ms = serializers.IntegerField(allow_null=True)
