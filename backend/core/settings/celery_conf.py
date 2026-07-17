"""Celery 配置。batch-17 从 core/settings.py 迁出。

命名 celery_conf 避免与 core/celery.py 冲突。各值用 os.getenv 独立取值。
"""

import os

# ============ Celery 配置 ============
# 参考: research.md RES-003, CLAUDE.md
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL", "redis://:redis_linchat_123@localhost:6379/2"
)
CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND", "redis://:redis_linchat_123@localhost:6379/2"
)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Shanghai"
CELERY_ENABLE_UTC = False
