"""语音交互配置（010-voice-agent-pipeline ~ 017-ambient-speaker-id）。

batch-17 从 core/settings.py 迁出。各值用 os.getenv 独立取值。
"""

import json
import os

# ============ Django Channels 配置 (语音交互) ============
# Redis DB3，独立于 DB0(缓存)/DB1(Langfuse)/DB2(Celery Broker)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                os.getenv(
                    "CHANNELS_REDIS_URL", "redis://:redis_linchat_123@localhost:6379/3"
                )
            ],
            "capacity": 1500,
            "expiry": 10,
        },
    },
}

# ============ 语音交互配置 (010-voice-agent-pipeline) ============
# Gateway ASR/TTS WebSocket 端点（通过 frpc-visitor 127.0.0.1:8100 访问）
VOICE_ASR_WS_URL = os.getenv(
    "VOICE_ASR_WS_URL", "ws://127.0.0.1:8100/v1/audio/transcriptions/stream"
)
VOICE_TTS_URL = os.getenv("VOICE_TTS_URL", "ws://127.0.0.1:8100/v1/audio/speech/stream")
VOICE_TTS_ENABLED = os.getenv("VOICE_TTS_ENABLED", "true").lower() == "true"
VOICE_TTS_VOICE = os.getenv("VOICE_TTS_VOICE", "zf_xiaobei")
VOICE_TTS_TIMEOUT = int(os.getenv("VOICE_TTS_TIMEOUT", "30"))  # wait_for_done 超时秒数
# TTS 播报队列 (013-tts-comfort-queue)
VOICE_TTS_COMFORT_DELAY = float(
    os.getenv("VOICE_TTS_COMFORT_DELAY", "3.0")
)  # 安慰语音触发延迟（秒）
VOICE_TTS_SEGMENT_GAP = float(
    os.getenv("VOICE_TTS_SEGMENT_GAP", "1.0")
)  # 播报段间静默（秒）
VOICE_TTS_COMFORT_TEXTS = json.loads(
    os.getenv(
        "VOICE_TTS_COMFORT_TEXTS",
        '["正在思考，请稍后。", "这次可能会久点，我正在做一些复杂操作。", "实在抱歉，我目前的能力有限，还在努力尝试，稍安勿躁。"]',
    )
)
VOICE_TTS_ERROR_TEXT = os.getenv(
    "VOICE_TTS_ERROR_TEXT", "大模型调用失败了，请结合日志分析错误原因。"
)
# batch-09：Agent token 流式增量送稿至 TTS（句子边界实时切片，与 LLM 推理重叠）。
# false 回退整体 enqueue 旧路径（旧路径代码保留，随时可运行时回滚）。
VOICE_TTS_INCREMENTAL_ENABLED = (
    os.getenv("VOICE_TTS_INCREMENTAL_ENABLED", "true").lower() == "true"
)
# batch-10：TTS WS 预连接——把 begin_stream（connect ~1s）从「首句就绪」提前到 pipeline 起点，
# 与整段 Agent 推理并行建连，首句到达时连接已就绪。默认 false 便于灰度，压测通过后置 true。
VOICE_TTS_PRECONNECT_ENABLED = (
    os.getenv("VOICE_TTS_PRECONNECT_ENABLED", "false").lower() == "true"
)
VOICE_ASR_SPEECH_PAD_MS = int(os.getenv("VOICE_ASR_SPEECH_PAD_MS", "2000"))
VOICE_ASR_LANGUAGE = os.getenv("VOICE_ASR_LANGUAGE", "zh")
VOICE_MAX_SEGMENT_DURATION = int(
    os.getenv("VOICE_MAX_SEGMENT_DURATION", "60")
)  # 单段语音最大时长（秒）

# 语音会话配置
VOICE_SESSION_TTL = int(os.getenv("VOICE_SESSION_TTL", "120"))  # 会话状态 TTL: 120s
VOICE_ACTIVE_CONV_TTL = int(
    os.getenv("VOICE_ACTIVE_CONV_TTL", "10")
)  # 活跃对话 TTL: 10s (30→10, 减少 ambient 误触发)
VOICE_AUDIO_CACHE_TTL = int(
    os.getenv("VOICE_AUDIO_CACHE_TTL", "300")
)  # 音频缓存 TTL: 300s
VOICE_MAX_RECORDING_SECONDS = int(
    os.getenv("VOICE_MAX_RECORDING_SECONDS", "30")
)  # 最大录音: 30s
VOICE_IDLE_TIMEOUT = int(os.getenv("VOICE_IDLE_TIMEOUT", "60"))  # 连接空闲超时: 60s
VOICE_STT_TIMEOUT = int(os.getenv("VOICE_STT_TIMEOUT", "30"))  # STT 转写超时: 30s

# 唤醒词与响应决策
VOICE_DEFAULT_WAKE_WORDS = ["小鱼"]  # 默认唤醒词列表
VOICE_SPEAKER_THRESHOLD = float(
    os.getenv("VOICE_SPEAKER_THRESHOLD", "0.5")
)  # 声纹识别阈值
VOICE_VAD_THRESHOLD = float(
    os.getenv("VOICE_VAD_THRESHOLD", "0.5")
)  # VAD 阈值 (0.0~1.0，越大越不灵敏)
VOICE_WAKE_WORD_FUZZY_THRESHOLD = float(
    os.getenv("VOICE_WAKE_WORD_FUZZY_THRESHOLD", "0.8")
)  # 唤醒词拼音模糊匹配阈值

# 环境语音模式 (014-jarvis-ambient-voice)
VOICE_AMBIENT_AGGREGATE_TIMEOUT = float(
    os.getenv("VOICE_AMBIENT_AGGREGATE_TIMEOUT", "1.5")
)  # 话语聚合静默超时（秒）(3.0→1.5, 加快响应)
VOICE_AMBIENT_MAX_BUFFER_SIZE = int(
    os.getenv("VOICE_AMBIENT_MAX_BUFFER_SIZE", "10")
)  # 聚合缓冲区最大话语数
VOICE_AMBIENT_SESSION_TTL = int(
    os.getenv("VOICE_AMBIENT_SESSION_TTL", "3600")
)  # ambient 会话 TTL: 3600s (1h)
VOICE_AMBIENT_RECORD_ONLY_LIMIT = int(
    os.getenv("VOICE_AMBIENT_RECORD_ONLY_LIMIT", "20")
)  # RECORD_ONLY 消息保留上限
# batch-08: ambient 轻量推理路径（跳过 LangGraph/工具/记忆召回，直调 Gateway）。关=回退完整 Agent（首选回滚手段）
VOICE_AMBIENT_LIGHT_ENABLED = (
    os.getenv("VOICE_AMBIENT_LIGHT_ENABLED", "true").lower() == "true"
)
VOICE_AMBIENT_LIGHT_HISTORY_ROUNDS = int(
    os.getenv("VOICE_AMBIENT_LIGHT_HISTORY_ROUNDS", "3")
)  # 保留最近 N 轮（N×2 条 user/assistant）
VOICE_DECISION_USE_LLM = (
    os.getenv("VOICE_DECISION_USE_LLM", "true").lower() == "true"
)  # 是否启用 LLM 意图分类 (016: 默认开启)
VOICE_DECISION_LLM_THRESHOLD = float(
    os.getenv("VOICE_DECISION_LLM_THRESHOLD", "0.75")
)  # LLM 分类置信度阈值 (0.6→0.75, 减少 ambient 误触发)
VOICE_DECISION_LLM_TIMEOUT = float(
    os.getenv("VOICE_DECISION_LLM_TIMEOUT", "2.0")
)  # LLM 分类超时（秒）(5.0→2.0, 加快响应)

# 说话人识别 (017-ambient-speaker-id)
VOICE_SPEAKER_IDENTIFICATION_ENABLED = (
    os.getenv("VOICE_SPEAKER_IDENTIFICATION_ENABLED", "true").lower() == "true"
)

# 声纹 diarize 配置（多说话人识别与过滤）
VOICE_SPEAKER_MIN_AUDIO_SECONDS = float(
    os.getenv("VOICE_SPEAKER_MIN_AUDIO_SECONDS", "1.0")
)
VOICE_DIARIZE_TIMEOUT = float(os.getenv("VOICE_DIARIZE_TIMEOUT", "15.0"))
VOICE_DIARIZE_MATCH_THRESHOLD = float(os.getenv("VOICE_DIARIZE_MATCH_THRESHOLD", "0.6"))
VOICE_DIARIZE_CLUSTER_THRESHOLD = float(
    os.getenv("VOICE_DIARIZE_CLUSTER_THRESHOLD", "0.4")
)
