"""
Django settings for LinChat project.

基于 data-model.md 和 constitution.md 配置
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-dev-key-change-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")


# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party apps
    "django.contrib.postgres",
    "rest_framework",
    "corsheaders",
    "django_celery_beat",
    # Local apps
    "apps.common",
    "apps.users",
    "apps.chat",
    "apps.models",
    "apps.memory",
    "apps.graph",
    "apps.context",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # 自定义 Token 认证中间件
    # 参考: behavior-model.md#1.3 Token鉴权验证
    "apps.common.middleware.TokenAuthMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases
# 参考: data-model.md#七、配置参数汇总

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:linchat_123@localhost:5432/linchat"
)

# 解析 DATABASE_URL
import re

db_match = re.match(
    r"postgresql://(?P<user>[^:]+):(?P<password>[^@]+)@(?P<host>[^:]+):(?P<port>\d+)/(?P<name>.+)",
    DATABASE_URL,
)
if db_match:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": db_match.group("name"),
            "USER": db_match.group("user"),
            "PASSWORD": db_match.group("password"),
            "HOST": db_match.group("host"),
            "PORT": db_match.group("port"),
            "CONN_MAX_AGE": 60,
            "OPTIONS": {
                "connect_timeout": 10,
            },
        }
    }
else:
    # 回退到默认配置
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "linchat",
            "USER": "postgres",
            "PASSWORD": "linchat_123",
            "HOST": "localhost",
            "PORT": "5432",
        }
    }


# Redis 配置
# 参考: data-model.md#三、Redis缓存设计
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {"max_connections": 50},
            "SOCKET_CONNECT_TIMEOUT": 5,
            "SOCKET_TIMEOUT": 5,
        },
    }
}


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"


# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


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
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = not DEBUG


# 国密算法配置
# 参考: constitution.md#4.2 数据保护
SM4_SECRET_KEY = os.getenv("SM4_SECRET_KEY", "default-sm4-key-16")  # 必须16字节


# LLM 服务配置
# 注: LLM_API_BASE/LLM_API_KEY/LLM_MODEL_NAME 已迁移到数据库（model 表）
# 通过 apps.models.services.model_service.get_active_model() 获取

# LLM 超时和重试配置
# 参考: rule-model.md#R_AGENT_001 和 R_LLM_RETRY_001
LLM_CALL_TIMEOUT = int(os.getenv("LLM_CALL_TIMEOUT", "60"))  # 单次调用超时: 60秒
AGENT_TOTAL_TIMEOUT = int(os.getenv("AGENT_TOTAL_TIMEOUT", "300"))  # Agent总超时: 300秒
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))  # 最大重试次数
LLM_INITIAL_RETRY_DELAY = float(os.getenv("LLM_INITIAL_RETRY_DELAY", "1.0"))  # 初始重试延迟(秒)
LLM_MAX_RETRY_DELAY = float(os.getenv("LLM_MAX_RETRY_DELAY", "8.0"))  # 最大重试延迟(秒)
LLM_RETRY_BACKOFF = float(os.getenv("LLM_RETRY_BACKOFF", "2.0"))  # 退避倍数

# 消息配置
# 参考: rule-model.md#R_MSG_001
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "4000"))  # 最大消息长度


# Langfuse 配置
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3001")


# LangGraph Checkpoint 配置
# 参考: data-model.md#五、LangGraph RedisSaver 配置
# TTL 单位为分钟（已通过 LangGraph 官方文档确认）
# 参考: https://github.com/redis-developer/langgraph-redis
LANGGRAPH_CHECKPOINT_TTL = 60 * 24  # 24小时 = 1440分钟
LANGGRAPH_CHECKPOINT_REFRESH_ON_READ = True  # 读取时刷新TTL


# 认证相关配置
# 参考: data-model.md#3.1 认证相关
AUTH_TOKEN_IDLE_TTL = 3600  # Token无操作过期: 1小时
AUTH_TOKEN_ABSOLUTE_TTL = 86400  # Token绝对过期: 24小时
AUTH_CAPTCHA_TTL = 120  # 验证码: 2分钟
AUTH_FAIL_COUNT_TTL = 900  # 失败计数: 15分钟
AUTH_MAX_FAIL_COUNT = 5  # 最大失败次数
AUTH_LOCK_DURATION = 900  # 锁定时间: 15分钟


# ============ Celery 配置 ============
# 参考: research.md RES-003, CLAUDE.md
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://:redis_linchat_123@localhost:6379/2")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://:redis_linchat_123@localhost:6379/2")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Shanghai"
CELERY_ENABLE_UTC = False


# ============ Memory 业务配置 ============
# 参考: specs/004-context-memory/tasks.md T005
MEMORY_EMBEDDING_PENDING_TIMEOUT = int(
    os.getenv("MEMORY_EMBEDDING_PENDING_TIMEOUT", "300")
)
MEMORY_CONTENT_MAX_LENGTH = int(os.getenv("MEMORY_CONTENT_MAX_LENGTH", "10000"))
MEMORY_EMBEDDING_DIMENSION = int(os.getenv("MEMORY_EMBEDDING_DIMENSION", "1024"))
MEMORY_SEARCH_TOP_K = int(os.getenv("MEMORY_SEARCH_TOP_K", "5"))
MEMORY_VECTOR_WEIGHT = float(os.getenv("MEMORY_VECTOR_WEIGHT", "0.7"))
MEMORY_KEYWORD_WEIGHT = float(os.getenv("MEMORY_KEYWORD_WEIGHT", "0.3"))
MEMORY_EMBEDDING_MAX_RETRY = int(os.getenv("MEMORY_EMBEDDING_MAX_RETRY", "3"))
COMPRESS_LOCK_TIMEOUT = int(os.getenv("COMPRESS_LOCK_TIMEOUT", "60"))


# ============ Brave Search 配置 ============
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")


# 日志配置
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {asctime} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}
