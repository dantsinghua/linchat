from rest_framework import serializers

from apps.media.models import MediaAttachment


class MediaAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaAttachment
        fields = [
            "attachment_uuid", "media_type", "mime_type", "file_name",
            "file_size", "width", "height", "duration_seconds", "is_expired", "expires_at",
        ]
        read_only_fields = fields


class DocumentParseRequestSerializer(serializers.Serializer):
    attachment_uuid = serializers.CharField(required=True, max_length=36, error_messages={"required": "attachment_uuid 参数为必填项", "blank": "attachment_uuid 不能为空"})
    pages = serializers.CharField(required=False, max_length=128, allow_blank=True)
