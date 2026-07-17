"""pytest 配置"""
import os

import django
import pytest
from django.conf import settings

# 确保 Django 设置在测试前加载
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")


def pytest_configure():
    """pytest 配置钩子"""
    if not settings.configured:
        django.setup()

    # 测试环境禁用 DRF 限流，避免全量测试时触发 429
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_CLASSES": [],
        "DEFAULT_THROTTLE_RATES": {},
    }


@pytest.fixture(autouse=True)
def _clear_process_caches():
    """清 batch-12 引入的进程内 TTL 缓存（model_config / wake_words），
    防跨测试污染（如 config-change-takes-effect 类断言读到上个用例的缓存）。"""
    from apps.models import services as _model_services
    from apps.voice.services import response_decision_service as _rds

    _model_services._invalidate_model_cache()
    _rds.invalidate_wake_words_cache()
    yield
    _model_services._invalidate_model_cache()
    _rds.invalidate_wake_words_cache()
