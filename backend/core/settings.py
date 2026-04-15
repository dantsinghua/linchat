"""
Django settings for LinChat project.

基于 data-model.md 和 constitution.md 配置
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

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
REDIS_URL = os.getenv("REDIS_URL", "redis://:redis_linchat_123@localhost:6379/0")

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
SUBAGENT_TIMEOUT = int(
    os.getenv("SUBAGENT_TIMEOUT", "60")
)  # SubAgent 单次执行超时: 60秒
LLM_INITIAL_RETRY_DELAY = float(
    os.getenv("LLM_INITIAL_RETRY_DELAY", "1.0")
)  # 初始重试延迟(秒)
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
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL", "redis://:redis_linchat_123@localhost:6379/2"
)
CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND", "redis://:redis_linchat_123@localhost:6379/2"
)
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


# ============ Context Monitoring 配置 ============
# 参考: specs/005-context-monitoring/tasks.md T001
MAX_TOOL_RESULT_TOKENS = int(os.getenv("MAX_TOOL_RESULT_TOKENS", "1500"))
MONITOR_PUSH_INTERVAL = float(os.getenv("MONITOR_PUSH_INTERVAL", "0.5"))

# ============ Brave Search 配置 ============
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")
BRAVE_SEARCH_QPS = int(os.getenv("BRAVE_SEARCH_QPS", "1"))
BRAVE_SEARCH_MONTHLY_QUOTA = int(os.getenv("BRAVE_SEARCH_MONTHLY_QUOTA", "2000"))

# ============ Home Assistant 配置 ============
# 参考: specs/007-home-assistant-tools/
HA_URL = os.getenv("HA_URL", "")  # HA 实例地址，如 http://192.168.1.100:8123
HA_TOKEN = os.getenv("HA_TOKEN", "")  # Long-Lived Access Token
HA_REQUEST_TIMEOUT = int(os.getenv("HA_REQUEST_TIMEOUT", "10"))  # HTTP 请求超时（秒）
HA_BLOCKED_ENTITIES = [
    e.strip()
    for e in os.getenv("HA_BLOCKED_ENTITIES", "").split(",")
    if e.strip()
]  # 黑名单设备列表
HA_ENABLED = bool(HA_URL and HA_TOKEN)  # 有配置才启用
HA_LAN_HOST = os.getenv("HA_LAN_HOST", "192.100.2.100")  # 局域网可达地址（HA 音箱 play_media 降级路径）

# ============ MinIO 对象存储配置 ============
# 参考: specs/008-multimodal-minicpm/research.md
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9010")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_BUCKET_MEDIA = os.getenv("MINIO_BUCKET_MEDIA", "linchat-media")
MINIO_BUCKET_THUMBNAILS = os.getenv("MINIO_BUCKET_THUMBNAILS", "linchat-thumbnails")
MINIO_AUDIO_BUCKET = os.getenv("MINIO_AUDIO_BUCKET", "audio")  # HA 音箱 TTS 降级路径音频桶

# ============ 多模态推理配置 ============
# 参考: specs/008-multimodal-minicpm/spec.md, FR-032, T003
LLM_GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://127.0.0.1:8100")
LLM_GATEWAY_API_KEY = os.getenv("LLM_GATEWAY_API_KEY", "")
LLM_GATEWAY_TIMEOUT = int(os.getenv("LLM_GATEWAY_TIMEOUT", "180"))  # 通用网关超时: 180秒

# 命名超时常量 (FR-032: 6 种超时配置)
LLM_GATEWAY_INFERENCE_TIMEOUT = int(os.getenv("LLM_GATEWAY_INFERENCE_TIMEOUT", "180"))  # 推理请求: 180秒
LLM_GATEWAY_CANCEL_TIMEOUT = int(os.getenv("LLM_GATEWAY_CANCEL_TIMEOUT", "5"))  # 取消请求: 5秒
LLM_GATEWAY_POLL_TIMEOUT = int(os.getenv("LLM_GATEWAY_POLL_TIMEOUT", "30"))  # 轮询查询: 30秒
LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT = int(os.getenv("LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT", "480"))  # 文档解析创建: 480秒（模型切换可能耗时6分钟）
LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT = int(os.getenv("LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT", "30"))  # 文档解析结果: 30秒
LLM_GATEWAY_GUARDRAILS_LEVEL = os.getenv("LLM_GATEWAY_GUARDRAILS_LEVEL", "fast")  # 护栏级别: fast (< 10ms)

# 文档解析配置
DOC_PARSE_MAX_FILE_SIZE = int(os.getenv("DOC_PARSE_MAX_FILE_SIZE", str(10 * 1024 * 1024)))  # 10MB
DOC_PARSE_MAX_PAGES = int(os.getenv("DOC_PARSE_MAX_PAGES", "200"))
DOC_PARSE_POLL_INTERVAL = int(os.getenv("DOC_PARSE_POLL_INTERVAL", "3"))  # 轮询间隔（秒）
DOC_PARSE_POLL_MAX_WAIT = int(os.getenv("DOC_PARSE_POLL_MAX_WAIT", "900"))  # 最大等待（秒）
DOC_PARSE_DEFAULT_MODEL = os.getenv("DOC_PARSE_DEFAULT_MODEL", "qwen3.5-9b")
DOC_PARSE_MAX_RESULT_LENGTH = int(os.getenv("DOC_PARSE_MAX_RESULT_LENGTH", "6000"))  # 011-document-subagent-rag: document_parse 工具返回结果最大字符数

# 视频预处理配置 (MiniCPM-o 限制: 高分辨率+多帧会导致 vLLM 500 错误)
VIDEO_PREPROCESS_WIDTH = int(os.getenv("VIDEO_PREPROCESS_WIDTH", "320"))  # 视频最大宽度(px)

# 多模态运行参数（模型配置已迁移到 DB model 表 type="multimodal"）
MULTIMODAL_MAX_TOKENS = int(os.getenv("MULTIMODAL_MAX_TOKENS", "1024"))  # 多模态推理最大输出 token（图片占用大量上下文，需控制）
MULTIMODAL_RATE_LIMIT_SECONDS = int(os.getenv("MULTIMODAL_RATE_LIMIT_SECONDS", "60"))  # 多模态推理限流间隔（秒），MiniCPM-o 不支持并发

# 对话历史裁剪配置
CONTEXT_HISTORY_ROUNDS = int(os.getenv("CONTEXT_HISTORY_ROUNDS", "10"))  # 默认保留最近 10 轮对话

# Django 文件上传大小限制（支持多模态大文件上传）
FILE_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024  # 60MB（超此大小写临时文件）
DATA_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024  # 60MB（请求体最大大小）

# 媒体文件限制
MEDIA_MAX_IMAGE_SIZE = int(os.getenv("MEDIA_MAX_IMAGE_SIZE", str(10 * 1024 * 1024)))  # 10MB
MEDIA_MAX_VIDEO_SIZE = int(os.getenv("MEDIA_MAX_VIDEO_SIZE", str(50 * 1024 * 1024)))  # 50MB
MEDIA_MAX_AUDIO_SIZE = int(os.getenv("MEDIA_MAX_AUDIO_SIZE", str(10 * 1024 * 1024)))  # 10MB
MEDIA_MAX_DOCUMENT_SIZE = int(os.getenv("MEDIA_MAX_DOCUMENT_SIZE", str(10 * 1024 * 1024)))  # 10MB
MEDIA_MAX_DURATION_SECONDS = int(os.getenv("MEDIA_MAX_DURATION_SECONDS", "60"))  # 60秒
MEDIA_MAX_ATTACHMENTS = int(os.getenv("MEDIA_MAX_ATTACHMENTS", "5"))  # 单次最多5个附件
MEDIA_EXPIRY_DAYS = int(os.getenv("MEDIA_EXPIRY_DAYS", "7"))  # 媒体文件7天过期

# 推理任务配置
INFERENCE_TASK_TTL = int(os.getenv("INFERENCE_TASK_TTL", "300"))  # 推理任务TTL: 300秒

# ============ Django Channels 配置 (语音交互) ============
# Redis DB3，独立于 DB0(缓存)/DB1(Langfuse)/DB2(Celery Broker)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.getenv("CHANNELS_REDIS_URL", "redis://:redis_linchat_123@localhost:6379/3")],
            "capacity": 1500,
            "expiry": 10,
        },
    },
}

# ============ 语音交互配置 (010-voice-agent-pipeline) ============
# Gateway ASR/TTS WebSocket 端点（通过 frpc-visitor 127.0.0.1:8100 访问）
VOICE_ASR_WS_URL = os.getenv("VOICE_ASR_WS_URL", "ws://127.0.0.1:8100/v1/audio/transcriptions/stream")
VOICE_TTS_URL = os.getenv("VOICE_TTS_URL", "ws://127.0.0.1:8100/v1/audio/speech/stream")
VOICE_TTS_ENABLED = os.getenv("VOICE_TTS_ENABLED", "true").lower() == "true"
VOICE_TTS_VOICE = os.getenv("VOICE_TTS_VOICE", "zf_xiaobei")
VOICE_TTS_TIMEOUT = int(os.getenv("VOICE_TTS_TIMEOUT", "30"))  # wait_for_done 超时秒数
# TTS 播报队列 (013-tts-comfort-queue)
VOICE_TTS_COMFORT_DELAY = float(os.getenv("VOICE_TTS_COMFORT_DELAY", "3.0"))  # 安慰语音触发延迟（秒）
VOICE_TTS_SEGMENT_GAP = float(os.getenv("VOICE_TTS_SEGMENT_GAP", "1.0"))  # 播报段间静默（秒）
VOICE_TTS_COMFORT_TEXTS = json.loads(os.getenv("VOICE_TTS_COMFORT_TEXTS", '["正在思考，请稍后。", "这次可能会久点，我正在做一些复杂操作。", "实在抱歉，我目前的能力有限，还在努力尝试，稍安勿躁。"]'))
VOICE_TTS_ERROR_TEXT = os.getenv("VOICE_TTS_ERROR_TEXT", "大模型调用失败了，请结合日志分析错误原因。")
VOICE_ASR_SPEECH_PAD_MS = int(os.getenv("VOICE_ASR_SPEECH_PAD_MS", "2000"))
VOICE_ASR_LANGUAGE = os.getenv("VOICE_ASR_LANGUAGE", "zh")
VOICE_MAX_SEGMENT_DURATION = int(os.getenv("VOICE_MAX_SEGMENT_DURATION", "60"))  # 单段语音最大时长（秒）

# 语音会话配置
VOICE_SESSION_TTL = int(os.getenv("VOICE_SESSION_TTL", "120"))  # 会话状态 TTL: 120s
VOICE_ACTIVE_CONV_TTL = int(os.getenv("VOICE_ACTIVE_CONV_TTL", "10"))  # 活跃对话 TTL: 10s (30→10, 减少 ambient 误触发)
VOICE_AUDIO_CACHE_TTL = int(os.getenv("VOICE_AUDIO_CACHE_TTL", "300"))  # 音频缓存 TTL: 300s
VOICE_MAX_RECORDING_SECONDS = int(os.getenv("VOICE_MAX_RECORDING_SECONDS", "30"))  # 最大录音: 30s
VOICE_IDLE_TIMEOUT = int(os.getenv("VOICE_IDLE_TIMEOUT", "60"))  # 连接空闲超时: 60s
VOICE_STT_TIMEOUT = int(os.getenv("VOICE_STT_TIMEOUT", "30"))  # STT 转写超时: 30s

# 唤醒词与响应决策
VOICE_DEFAULT_WAKE_WORDS = ["小鱼"]  # 默认唤醒词列表
VOICE_SPEAKER_THRESHOLD = float(os.getenv("VOICE_SPEAKER_THRESHOLD", "0.5"))  # 声纹识别阈值
VOICE_VAD_THRESHOLD = float(os.getenv("VOICE_VAD_THRESHOLD", "0.5"))  # VAD 阈值 (0.0~1.0，越大越不灵敏)
VOICE_WAKE_WORD_FUZZY_THRESHOLD = float(os.getenv("VOICE_WAKE_WORD_FUZZY_THRESHOLD", "0.8"))  # 唤醒词拼音模糊匹配阈值

# 环境语音模式 (014-jarvis-ambient-voice)
VOICE_AMBIENT_AGGREGATE_TIMEOUT = float(os.getenv("VOICE_AMBIENT_AGGREGATE_TIMEOUT", "3.0"))  # 话语聚合静默超时（秒）
VOICE_AMBIENT_MAX_BUFFER_SIZE = int(os.getenv("VOICE_AMBIENT_MAX_BUFFER_SIZE", "10"))  # 聚合缓冲区最大话语数
VOICE_AMBIENT_SESSION_TTL = int(os.getenv("VOICE_AMBIENT_SESSION_TTL", "3600"))  # ambient 会话 TTL: 3600s (1h)
VOICE_AMBIENT_RECORD_ONLY_LIMIT = int(os.getenv("VOICE_AMBIENT_RECORD_ONLY_LIMIT", "20"))  # RECORD_ONLY 消息保留上限
VOICE_DECISION_USE_LLM = os.getenv("VOICE_DECISION_USE_LLM", "true").lower() == "true"  # 是否启用 LLM 意图分类 (016: 默认开启)
VOICE_DECISION_LLM_THRESHOLD = float(os.getenv("VOICE_DECISION_LLM_THRESHOLD", "0.75"))  # LLM 分类置信度阈值 (0.6→0.75, 减少 ambient 误触发)
VOICE_DECISION_LLM_TIMEOUT = float(os.getenv("VOICE_DECISION_LLM_TIMEOUT", "5.0"))  # LLM 分类超时（秒）(016: 1s→5s，宪法豁免)

# 声纹 diarize 配置（多说话人识别与过滤）
VOICE_SPEAKER_MIN_AUDIO_SECONDS = float(os.getenv("VOICE_SPEAKER_MIN_AUDIO_SECONDS", "1.0"))
VOICE_DIARIZE_TIMEOUT = float(os.getenv("VOICE_DIARIZE_TIMEOUT", "15.0"))
VOICE_DIARIZE_MATCH_THRESHOLD = float(os.getenv("VOICE_DIARIZE_MATCH_THRESHOLD", "0.6"))
VOICE_DIARIZE_CLUSTER_THRESHOLD = float(os.getenv("VOICE_DIARIZE_CLUSTER_THRESHOLD", "0.4"))

# ============ 文档 SubAgent + RAG 配置 (011-document-subagent-rag) ============
DOCUMENT_SUBAGENT_TIMEOUT = int(os.getenv("DOCUMENT_SUBAGENT_TIMEOUT", "1200"))  # 文档 SubAgent 超时: 20分钟
DOC_CHUNK_SIZE = int(os.getenv("DOC_CHUNK_SIZE", "800"))  # 分块大小（字符）
DOC_CHUNK_OVERLAP = int(os.getenv("DOC_CHUNK_OVERLAP", "100"))  # 分块重叠（字符）
DOC_VECTOR_WEIGHT = float(os.getenv("DOC_VECTOR_WEIGHT", "0.7"))  # 混合搜索向量权重
DOC_KEYWORD_WEIGHT = float(os.getenv("DOC_KEYWORD_WEIGHT", "0.3"))  # 混合搜索关键词权重
DOC_SEARCH_TOP_K = int(os.getenv("DOC_SEARCH_TOP_K", "5"))  # 搜索结果上限

# 多模态/文档解析超时配置（GPU 模型切换耗时 35-341 秒）
MULTIMODAL_SUBAGENT_TIMEOUT = int(os.getenv("MULTIMODAL_SUBAGENT_TIMEOUT", "1200"))  # 多模态 SubAgent 超时: 20分钟
GPU_LOCK_MAX_WAIT = int(os.getenv("GPU_LOCK_MAX_WAIT", "600"))  # 等待 GPU 锁上限: 10分钟
AGENT_MULTIMODAL_TIMEOUT = int(os.getenv("AGENT_MULTIMODAL_TIMEOUT", "2400"))  # 含文档附件时 Agent 总超时: 40分钟

# SSE 心跳配置（防止代理层空闲超时断连）
SSE_HEARTBEAT_INTERVAL = int(os.getenv("SSE_HEARTBEAT_INTERVAL", "15"))  # 心跳间隔: 15秒


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
        "apps.context.monitoring": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
