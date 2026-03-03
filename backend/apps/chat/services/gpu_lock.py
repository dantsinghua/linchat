# 兼容层：已迁移到 apps.graph.services.gpu_lock
from apps.graph.services.gpu_lock import GPULockTimeout, acquire_gpu_lock

__all__ = ["GPULockTimeout", "acquire_gpu_lock"]
