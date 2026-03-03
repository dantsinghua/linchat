# 兼容层：已迁移到 apps.media.services.upload
from apps.media.services.upload import (
    MAX_AUDIO_DURATION,
    MAX_VIDEO_DURATION,
    MIN_AUDIO_DURATION,
    SUPPORTED_AUDIO_TYPES,
    SUPPORTED_DOCUMENT_TYPES,
    SUPPORTED_IMAGE_TYPES,
    SUPPORTED_VIDEO_TYPES,
    MediaService,
    MediaUploadError,
    media_service,
)

__all__ = [
    "MediaService", "MediaUploadError", "media_service",
    "SUPPORTED_IMAGE_TYPES", "SUPPORTED_VIDEO_TYPES", "SUPPORTED_AUDIO_TYPES", "SUPPORTED_DOCUMENT_TYPES",
    "MAX_VIDEO_DURATION", "MAX_AUDIO_DURATION", "MIN_AUDIO_DURATION",
]
