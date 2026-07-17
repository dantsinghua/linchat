"""媒体存储与上传限制配置（MinIO + 文件上传 + 媒体大小）。

batch-17 从 core/settings.py 迁出。各值用 os.getenv 独立取值。
"""

import os

# ============ MinIO 对象存储配置 ============
# 参考: specs/008-multimodal-minicpm/research.md
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9010")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_BUCKET_MEDIA = os.getenv("MINIO_BUCKET_MEDIA", "linchat-media")
MINIO_BUCKET_THUMBNAILS = os.getenv("MINIO_BUCKET_THUMBNAILS", "linchat-thumbnails")
MINIO_AUDIO_BUCKET = os.getenv(
    "MINIO_AUDIO_BUCKET", "audio"
)  # HA 音箱 TTS 降级路径音频桶

# Django 文件上传大小限制（支持多模态大文件上传）
FILE_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024  # 60MB（超此大小写临时文件）
DATA_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024  # 60MB（请求体最大大小）

# 媒体文件限制
MEDIA_MAX_IMAGE_SIZE = int(
    os.getenv("MEDIA_MAX_IMAGE_SIZE", str(10 * 1024 * 1024))
)  # 10MB
MEDIA_MAX_VIDEO_SIZE = int(
    os.getenv("MEDIA_MAX_VIDEO_SIZE", str(50 * 1024 * 1024))
)  # 50MB
MEDIA_MAX_AUDIO_SIZE = int(
    os.getenv("MEDIA_MAX_AUDIO_SIZE", str(10 * 1024 * 1024))
)  # 10MB
MEDIA_MAX_DOCUMENT_SIZE = int(
    os.getenv("MEDIA_MAX_DOCUMENT_SIZE", str(10 * 1024 * 1024))
)  # 10MB
MEDIA_MAX_DURATION_SECONDS = int(os.getenv("MEDIA_MAX_DURATION_SECONDS", "60"))  # 60秒
MEDIA_MAX_ATTACHMENTS = int(os.getenv("MEDIA_MAX_ATTACHMENTS", "5"))  # 单次最多5个附件
MEDIA_EXPIRY_DAYS = int(os.getenv("MEDIA_EXPIRY_DAYS", "7"))  # 媒体文件7天过期
