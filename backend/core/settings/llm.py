"""LLM / 多模态 / 文档网关运行参数配置。

batch-18 从 core/settings/__init__.py 迁出。各值用 os.getenv 独立取值，
不依赖 base 内变量。模型凭据（API_BASE/KEY/MODEL）已迁至 DB model 表。
"""

import os

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


# LangGraph Checkpoint 配置
# 参考: data-model.md#五、LangGraph RedisSaver 配置
# TTL 单位为分钟（已通过 LangGraph 官方文档确认）
# 参考: https://github.com/redis-developer/langgraph-redis
LANGGRAPH_CHECKPOINT_TTL = 60 * 24  # 24小时 = 1440分钟
LANGGRAPH_CHECKPOINT_REFRESH_ON_READ = True  # 读取时刷新TTL


# ============ 多模态推理配置 ============
# 参考: specs/008-multimodal-minicpm/spec.md, FR-032, T003
LLM_GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://127.0.0.1:8100")
LLM_GATEWAY_API_KEY = os.getenv("LLM_GATEWAY_API_KEY", "")
LLM_GATEWAY_TIMEOUT = int(
    os.getenv("LLM_GATEWAY_TIMEOUT", "180")
)  # 通用网关超时: 180秒

# 命名超时常量 (FR-032: 6 种超时配置)
LLM_GATEWAY_INFERENCE_TIMEOUT = int(
    os.getenv("LLM_GATEWAY_INFERENCE_TIMEOUT", "180")
)  # 推理请求: 180秒
LLM_GATEWAY_CANCEL_TIMEOUT = int(
    os.getenv("LLM_GATEWAY_CANCEL_TIMEOUT", "5")
)  # 取消请求: 5秒
LLM_GATEWAY_POLL_TIMEOUT = int(
    os.getenv("LLM_GATEWAY_POLL_TIMEOUT", "30")
)  # 轮询查询: 30秒
LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT = int(
    os.getenv("LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT", "480")
)  # 文档解析创建: 480秒（模型切换可能耗时6分钟）
LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT = int(
    os.getenv("LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT", "30")
)  # 文档解析结果: 30秒
LLM_GATEWAY_GUARDRAILS_LEVEL = os.getenv(
    "LLM_GATEWAY_GUARDRAILS_LEVEL", "fast"
)  # 护栏级别: fast (< 10ms)

# 文档解析配置
DOC_PARSE_MAX_FILE_SIZE = int(
    os.getenv("DOC_PARSE_MAX_FILE_SIZE", str(10 * 1024 * 1024))
)  # 10MB
DOC_PARSE_MAX_PAGES = int(os.getenv("DOC_PARSE_MAX_PAGES", "200"))
DOC_PARSE_POLL_INTERVAL = int(
    os.getenv("DOC_PARSE_POLL_INTERVAL", "3")
)  # 轮询间隔（秒）
DOC_PARSE_POLL_MAX_WAIT = int(
    os.getenv("DOC_PARSE_POLL_MAX_WAIT", "900")
)  # 最大等待（秒）
DOC_PARSE_DEFAULT_MODEL = os.getenv("DOC_PARSE_DEFAULT_MODEL", "qwen3.5-9b")
DOC_PARSE_MAX_RESULT_LENGTH = int(
    os.getenv("DOC_PARSE_MAX_RESULT_LENGTH", "6000")
)  # 011-document-subagent-rag: document_parse 工具返回结果最大字符数

# 视频预处理配置 (MiniCPM-o 限制: 高分辨率+多帧会导致 vLLM 500 错误)
VIDEO_PREPROCESS_WIDTH = int(
    os.getenv("VIDEO_PREPROCESS_WIDTH", "320")
)  # 视频最大宽度(px)

# 多模态运行参数（模型配置已迁移到 DB model 表 type="multimodal"）
MULTIMODAL_MAX_TOKENS = int(
    os.getenv("MULTIMODAL_MAX_TOKENS", "1024")
)  # 多模态推理最大输出 token（图片占用大量上下文，需控制）
MULTIMODAL_RATE_LIMIT_SECONDS = int(
    os.getenv("MULTIMODAL_RATE_LIMIT_SECONDS", "60")
)  # 多模态推理限流间隔（秒），MiniCPM-o 不支持并发

# 对话历史裁剪配置
CONTEXT_HISTORY_ROUNDS = int(
    os.getenv("CONTEXT_HISTORY_ROUNDS", "10")
)  # 默认保留最近 10 轮对话

# 推理任务配置
INFERENCE_TASK_TTL = int(os.getenv("INFERENCE_TASK_TTL", "300"))  # 推理任务TTL: 300秒

# ============ 文档 SubAgent + RAG 配置 (011-document-subagent-rag) ============
DOCUMENT_SUBAGENT_TIMEOUT = int(
    os.getenv("DOCUMENT_SUBAGENT_TIMEOUT", "1200")
)  # 文档 SubAgent 超时: 20分钟
DOC_CHUNK_SIZE = int(os.getenv("DOC_CHUNK_SIZE", "800"))  # 分块大小（字符）
DOC_CHUNK_OVERLAP = int(os.getenv("DOC_CHUNK_OVERLAP", "100"))  # 分块重叠（字符）
DOC_VECTOR_WEIGHT = float(os.getenv("DOC_VECTOR_WEIGHT", "0.7"))  # 混合搜索向量权重
DOC_KEYWORD_WEIGHT = float(os.getenv("DOC_KEYWORD_WEIGHT", "0.3"))  # 混合搜索关键词权重
DOC_SEARCH_TOP_K = int(os.getenv("DOC_SEARCH_TOP_K", "5"))  # 搜索结果上限

# 多模态/文档解析超时配置（GPU 模型切换耗时 35-341 秒）
MULTIMODAL_SUBAGENT_TIMEOUT = int(
    os.getenv("MULTIMODAL_SUBAGENT_TIMEOUT", "1200")
)  # 多模态 SubAgent 超时: 20分钟
GPU_LOCK_MAX_WAIT = int(
    os.getenv("GPU_LOCK_MAX_WAIT", "600")
)  # 等待 GPU 锁上限: 10分钟
AGENT_MULTIMODAL_TIMEOUT = int(
    os.getenv("AGENT_MULTIMODAL_TIMEOUT", "2400")
)  # 含文档附件时 Agent 总超时: 40分钟

# SSE 心跳配置（防止代理层空闲超时断连）
SSE_HEARTBEAT_INTERVAL = int(
    os.getenv("SSE_HEARTBEAT_INTERVAL", "15")
)  # 心跳间隔: 15秒
