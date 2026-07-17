"""日志配置 — 统一 JSON + trace_id 注入（batch-04）。

batch-18 从 core/settings/__init__.py 迁出。DEBUG 用模块内私有 _DEBUG 重算，
避免与 base 循环 import。core.logging_config 只依赖 apps.common（无 settings
import），故此处 import build_logging_dict 无循环风险。聚合 import 时置于最后。
"""

import os

from core.logging_config import build_logging_dict

_DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"  # 与 base 同源同值

LOGGING = build_logging_dict(
    debug=_DEBUG, log_level=os.getenv("DJANGO_LOG_LEVEL", "INFO")
)
