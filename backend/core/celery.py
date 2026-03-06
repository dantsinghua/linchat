"""
Celery 应用配置

参考: research.md RES-003
Broker: Redis DB2 (与 LinChat DB0 / Langfuse DB1 隔离)
"""

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

app = Celery("linchat")

# 从 Django settings 中加载以 CELERY_ 开头的配置
app.config_from_object("django.conf:settings", namespace="CELERY")

# 自动发现所有 Django app 中的 tasks.py
app.autodiscover_tasks()

# Beat 定时任务调度
app.conf.beat_schedule = {
    "retry-failed-embeddings": {
        "task": "memory.retry_failed_embeddings",
        "schedule": 300.0,  # 每 5 分钟
    },
    "generate-daily-summary": {
        "task": "memory.generate_daily_summary",
        "schedule": crontab(hour=0, minute=0),  # 每天 00:00
    },
    "generate-monthly-summary": {
        "task": "memory.generate_monthly_summary",
        "schedule": crontab(day_of_month=1, hour=0, minute=0),  # 每月 1 日 00:00
    },
    "embedding-health-check": {
        "task": "memory.embedding_health_check",
        "schedule": crontab(minute=0),  # 每小时整点执行
    },
    "clean-expired-media": {
        "task": "media.clean_expired_media",
        "schedule": crontab(hour=3, minute=0),  # 每日凌晨 3 点
    },
    "retry-failed-doc-embeddings": {
        "task": "media.retry_failed_doc_embeddings",
        "schedule": 300.0,  # 每 5 分钟
    },
}
app.conf.timezone = "Asia/Shanghai"
