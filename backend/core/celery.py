"""
Celery 应用配置

参考: research.md RES-003
Broker: Redis DB2 (与 LinChat DB0 / Langfuse DB1 隔离)
"""

import os
import uuid

from celery import Celery
from celery.schedules import crontab
from celery.signals import before_task_publish, task_postrun, task_prerun

from apps.common import trace_id_var

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
    "expire-guests": {
        "task": "users.expire_guests",
        "schedule": crontab(minute=0),  # 每小时整点执行
    },
}
app.conf.timezone = "Asia/Shanghai"


# ============ trace_id 透传（batch-28）============
# 复用 batch-04 的 trace_id_var，把发起者上下文的 trace_id 透传进 Task 并在 worker 侧恢复，
# 使 HTTP 请求与其异步任务（含 beat 周期任务的自动生成值）共享同一 trace_id。
# 每个 task_id 的 contextvar reset token 暂存于此：prerun 存 / postrun 取。
# prefork worker 单进程串行执行 task，dict 有界（≈ 并发数），无泄漏风险。
_trace_tokens: dict[str, object] = {}


@before_task_publish.connect
def _inject_trace_id(headers=None, **_):
    """发布端：把当前 contextvar trace_id 写进 task headers（protocol v2）。"""
    try:
        if headers is not None:
            tid = trace_id_var.get()
            if tid:
                headers["trace_id"] = tid
    except Exception:  # noqa: BLE001 — signal 绝不能打断任务发布
        pass


@task_prerun.connect
def _restore_trace_id(task_id=None, task=None, **_):
    """worker 端：从 request headers 恢复 trace_id；beat 任务无上下文则生成 hex 兜底。"""
    try:
        tid = getattr(task.request, "trace_id", None) if task else None
        if not tid:
            tid = uuid.uuid4().hex
        _trace_tokens[task_id] = trace_id_var.set(tid)
    except Exception:  # noqa: BLE001
        pass


@task_postrun.connect
def _clear_trace_id(task_id=None, **_):
    """worker 端：任务结束 reset contextvar，避免 prefork 进程复用时串味。"""
    token = _trace_tokens.pop(task_id, None)
    if token is not None:
        try:
            trace_id_var.reset(token)
        except Exception:  # noqa: BLE001
            trace_id_var.set("")
