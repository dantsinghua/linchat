from django.apps import AppConfig


class ContextConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.context"
    verbose_name = "Prompt 与上下文管理"
