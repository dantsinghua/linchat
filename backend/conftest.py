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
