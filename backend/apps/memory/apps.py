"""Memory 应用配置"""

from django.apps import AppConfig


class MemoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.memory"
    verbose_name = "记忆管理"
