# 兼容层：已迁移到 apps.graph.services.inference_service
from apps.graph.services.inference_service import (
    InferenceService, inference_service, _task_key as _get_inference_task_key,
)

__all__ = ["InferenceService", "inference_service", "_get_inference_task_key"]
