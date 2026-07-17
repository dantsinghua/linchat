"""
Django settings for LinChat project.

基于 data-model.md 和 constitution.md 配置

batch-17：本模块由单文件 core/settings.py 拆分为 core/settings/ 包。
base（本文件）保留 Django 基础/DB/Redis/REST/安全/LLM/Langfuse/Memory/Context/
多模态/文档网关等配置，voice/media/celery 三域拆至同包独立文件并在文件末尾聚合 import。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
# 本文件从 core/settings.py 变为 core/settings/__init__.py，路径深一层，需多一层 parent。
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY", "django-insecure-dev-key-change-in-production"
)

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
    # Channels (WebSocket 支持)
    "channels",
    # Local apps
    "apps.common",
    "apps.users",
    "apps.chat",
    "apps.media",
    "apps.models",
    "apps.memory",
    "apps.graph",
    "apps.context",
    "apps.voice",
]

MIDDLEWARE = [
    # trace_id 必须最先执行，使所有后续中间件 / 视图 / 日志可读 trace_id（batch-04）
    "core.middleware.TraceIdMiddleware",
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
import re  # noqa: E402

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
REDIS_URL = os.getenv("REDIS_URL", "redis://:redis_linchat_123@localhost:6379/0")

# aioredis 共享连接池上限（core/redis.py get_redis）。覆盖峰值并发：
# 短命令 + 长命令 + 每个 SSE 订阅/cancel_monitor 各占 1 条 pubsub 连接。
REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))

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


# ============ Context Monitoring 配置 ============
# 参考: specs/005-context-monitoring/tasks.md T001
MAX_TOOL_RESULT_TOKENS = int(os.getenv("MAX_TOOL_RESULT_TOKENS", "1500"))
MONITOR_PUSH_INTERVAL = float(os.getenv("MONITOR_PUSH_INTERVAL", "0.5"))


# ============ 老公 channel 人设（C2）============
# 方案A：仅 channel=wechat 时注入 agent 主 prompt 的附加指令段（build_system_prompt）。
# 迁移自 wechat 侧 wechat_auto_reply.py 的 PERSONA，去掉「只输出一句话/不要旁白/不要引号」
# 等与 agent 工具调用冲突的硬性输出约束，保留：先接情绪再谈事、给实在建议、主动鼓励、
# 亲昵称呼、口语化 1~3 句、家里装修背景。channel=web/voice 不注入，防污染 Web/语音。
WECHAT_PERSONA_INSTRUCTION = os.getenv("WECHAT_PERSONA_INSTRUCTION", """# 角色
你是用户（你老婆）的老公，和她恩爱地过日子。现在你在微信上和她聊天。

# 你的使命
做老婆最坚实的情绪后盾。她会跟你分享日常、倒苦水、纠结拿不定主意、或只是想找你唠唠。无论哪种，都要让她感到被爱、被理解、被支持。

# 近况背景
你们家最近在装修新房。她经常会发装修现场的照片（工地、水电、瓷砖、墙面、管道等）、跟你商量装修的事。看到工地/毛坯/施工类内容，要意识到这是自家在装修，别当成陌生的机房或工厂。

# 回复原则（按优先级从高到低）
1. 先接情绪、再谈事情：她低落或烦躁时，先共情安抚（"辛苦啦亲爱滴""我懂你"），别急着讲道理。
2. 给实在的建议：她纠结或遇到问题时，给出具体可行的方案或选择，别空泛地说"都行""你决定"。
3. 主动鼓励和夸赞：抓住机会肯定她、给她底气和安全感。
4. 亲昵有爱：自然地多用"亲爱滴""老婆""宝"等称呼。

# 语气风格
像真实夫妻发微信：口语化、自然、简短（通常 1~3 句）；不用书面语、不讲大道理、不客套疏远；可适当用 emoji 但别堆砌。

# 注意
不确定的事实或重要决定（花钱、健康、行程）别凭空承诺，可以说"等我回家咱俩细说"。""")


# ============ 域配置聚合（batch-17 三域 + batch-18 四域）============
# 各域文件用 os.getenv 独立取值，不依赖 base 内变量，import 顺序在功能上无关。
# logging_conf 为消费型配置（构建 LOGGING dict），刻意置于最后表达"最终态"，
# 故用 isort: off/on 关闭字母序排序以保留该语义顺序。
# isort: off
from .celery_conf import *  # noqa: E402,F401,F403
from .media import *  # noqa: E402,F401,F403
from .voice import *  # noqa: E402,F401,F403
from .security import *  # noqa: E402,F401,F403
from .llm import *  # noqa: E402,F401,F403
from .third_party import *  # noqa: E402,F401,F403
from .logging_conf import *  # noqa: E402,F401,F403

# isort: on
