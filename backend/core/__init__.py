# LinChat 核心配置模块

# Celery app 导入，确保 Django 启动时注册
# 参考: research.md RES-003
from .celery import app as celery_app

__all__ = ("celery_app",)
