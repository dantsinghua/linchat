"""pytest 配置"""
import os

import django
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
