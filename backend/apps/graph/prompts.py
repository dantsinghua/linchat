"""兼容层：所有 prompt 相关逻辑已迁移到 apps.context

保留此文件确保现有 import 路径不需要修改。
"""

from apps.context import *  # noqa: F401, F403

# 显式重新导出 _MEMORY_TYPE_LABELS（测试中使用）
from apps.context.builder import _MEMORY_TYPE_LABELS  # noqa: F401
