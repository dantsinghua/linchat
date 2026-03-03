# 兼容层：clean_expired_media 已迁移到 apps.media.tasks
from apps.media.tasks import clean_expired_media  # noqa: F401
