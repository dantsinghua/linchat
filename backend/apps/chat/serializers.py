from django.conf import settings
from rest_framework import serializers

# 兼容层：MediaAttachmentSerializer 已迁移到 apps.media.serializers
from apps.media.serializers import MediaAttachmentSerializer  # noqa: F401


class ChatRequestSerializer(serializers.Serializer):
    """
    聊天请求序列化器

    参考: rule-model.md#R_MSG_001 - 消息长度≤4000字符
    支持多模态附件: specs/008-multimodal-minicpm/contracts/multimodal-chat.yaml
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
    attachments = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        max_length=settings.MEDIA_MAX_ATTACHMENTS,
        error_messages={
            "max_length": f"单次最多上传{settings.MEDIA_MAX_ATTACHMENTS}个附件",
        },
    )

    def validate_attachments(self, value: list) -> list[str]:
        """校验附件 UUID 格式（T022）

        将 UUID 对象转为字符串，以便后续服务层直接使用。
        附件的存在性、所有权和 media_type 校验由 AgentService 执行。
        """
        return [str(uuid_val) for uuid_val in value]


class RequestIdSerializer(serializers.Serializer):
    """请求ID序列化器（停止/继续/重连共用）"""

    request_id = serializers.CharField(
        required=True,
        max_length=64,
        error_messages={
            "required": "请求ID不能为空",
        },
    )


# 兼容别名，保持现有 import 不变
StopGenerationRequestSerializer = RequestIdSerializer
ResumeGenerationRequestSerializer = RequestIdSerializer
ReconnectRequestSerializer = RequestIdSerializer


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
    """消息响应序列化器

    参考: specs/008-multimodal-minicpm/contracts/multimodal-chat.yaml
    """

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
    attachments = serializers.ListField(default=[])
    is_voice = serializers.BooleanField(default=False)
    speaker_id = serializers.CharField(allow_null=True, default=None)
