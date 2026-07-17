"""第三方服务集成配置（Langfuse / Brave Search / Home Assistant）。

batch-18 从 core/settings/__init__.py 迁出。各值用 os.getenv 独立取值。
MinIO 已在 batch-17 迁至 media.py。
"""

import os

# Langfuse 配置
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3001")


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
    e.strip() for e in os.getenv("HA_BLOCKED_ENTITIES", "").split(",") if e.strip()
]  # 黑名单设备列表
HA_ENABLED = bool(HA_URL and HA_TOKEN)  # 有配置才启用
HA_LAN_HOST = os.getenv(
    "HA_LAN_HOST", "192.100.2.100"
)  # 局域网可达地址（HA 音箱 play_media 降级路径）


# ============ 公众号知识库检索 (oa_search / wn-linchat-brain C1) ============
OA_SEARCH_DB_PATH = os.getenv(
    "OA_SEARCH_DB_PATH",
    "/home/dantsinghua/clawd/scripts/wechat-narrator/oa_fts.db",
)
OA_SEARCH_ENABLED = os.getenv("OA_SEARCH_ENABLED", "false").lower() == "true"
