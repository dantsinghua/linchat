"""安全与 API 访问策略配置（REST_FRAMEWORK/CORS/Cookie/SM4/Auth Token）。

batch-18 从 core/settings/__init__.py 迁出。各值用 os.getenv 独立取值；
DEBUG 用模块内私有 _DEBUG 重算，避免与 base 循环 import。
"""

import os

_DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"  # 与 base 同源同值


# Django REST Framework 配置
# 参考: constitution.md#1.2 接口设计
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [],  # 自定义Token认证
    "DEFAULT_PERMISSION_CLASSES": [],  # 自定义权限控制
    "EXCEPTION_HANDLER": "apps.common.exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "100/hour",  # 匿名用户100次/时
        "user": "1000/hour",  # 认证用户1000次/时
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 20,
}


# CORS 配置
CORS_ALLOWED_ORIGINS = os.getenv(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")
CORS_ALLOW_CREDENTIALS = True  # 允许携带Cookie


# 安全配置
# 参考: constitution.md#4.1 认证授权
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Cookie 安全配置 (Token 必须存储在 httpOnly Cookie)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not _DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = not _DEBUG


# 国密算法配置
# 参考: constitution.md#4.2 数据保护
SM4_SECRET_KEY = os.getenv("SM4_SECRET_KEY", "default-sm4-key-16")  # 必须16字节


# 认证相关配置
# 参考: data-model.md#3.1 认证相关
AUTH_TOKEN_IDLE_TTL = 3600  # Token无操作过期: 1小时
AUTH_TOKEN_ABSOLUTE_TTL = 86400  # Token绝对过期: 24小时
AUTH_CAPTCHA_TTL = 120  # 验证码: 2分钟
AUTH_FAIL_COUNT_TTL = 900  # 失败计数: 15分钟
AUTH_MAX_FAIL_COUNT = 5  # 最大失败次数
AUTH_LOCK_DURATION = 900  # 锁定时间: 15分钟
