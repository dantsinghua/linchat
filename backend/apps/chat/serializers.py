"""
聊天模块序列化器

参考:
- rule-model.md#R_MSG_001 消息长度限制规则
- rule-model.md#R_MSG_002 空消息拦截规则
- specs/008-multimodal-minicpm/contracts/media-upload.yaml
"""

from django.conf import settings
from rest_framework import serializers

from apps.chat.models import MediaAttachment


class MediaAttachmentSerializer(serializers.ModelSerializer):
    """
    媒体附件序列化器

    参考: specs/008-multimodal-minicpm/contracts/media-upload.yaml
    """

    class Meta:
        model = MediaAttachment
        fields = [
            "attachment_uuid",
            "media_type",
            "mime_type",
            "file_name",
            "file_size",
            "width",
            "height",
            "duration_seconds",
            "is_expired",
            "expires_at",
        ]
        read_only_fields = fields


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


class DocumentParseRequestSerializer(serializers.Serializer):
    """文档解析请求序列化器

    参考: specs/008-multimodal-minicpm/contracts/document-parse.yaml
    接收已上传到 MinIO 的文档附件 UUID，model 由后端 settings 配置决定。
    """

    attachment_uuid = serializers.CharField(
        required=True,
        max_length=36,
        error_messages={
            "required": "attachment_uuid 参数为必填项",
            "blank": "attachment_uuid 不能为空",
        },
    )
    pages = serializers.CharField(
        required=False,
        max_length=128,
        allow_blank=True,
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
