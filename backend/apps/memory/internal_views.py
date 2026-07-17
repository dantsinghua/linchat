"""内部端点（设备 token 鉴权，跳过 cookie 中间件）。不属对外 API 契约。

安全红线：/api/v1/internal/ 在 PUBLIC_PATHS 中已跳过 cookie 中间件，
因此本 view 必须自行校验设备 token，token 缺失/无效一律返回 401。
"""
import logging

from asgiref.sync import async_to_sync
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.common.responses import ApiResponse
from apps.memory.serializers import InternalIngestSerializer
from apps.memory.services import MemoryService
from apps.voice.services.device_service import device_service

logger = logging.getLogger(__name__)


@api_view(["POST"])
def internal_ingest(request: Request) -> Response:
    token = request.META.get("HTTP_X_DEVICE_TOKEN", "")
    auth = async_to_sync(device_service.authenticate_by_token)(token)
    if not auth:
        return ApiResponse.unauthorized(message="设备 token 无效")
    user_id = auth["user_id"]
    s = InternalIngestSerializer(data=request.data)
    if not s.is_valid():
        return ApiResponse.validation_error(errors=s.errors)
    d = s.validated_data
    memory, deduped = async_to_sync(MemoryService.ingest_memory)(
        user_id=user_id, content=d["content"], name=d["name"],
        source=d["source"], tag=d.get("tag"))
    logger.info("Internal ingest: user_id=%s type=%s name=%s deduped=%s status=%s",
                user_id, d["source"], d["name"], deduped, memory.embedding_status)
    return ApiResponse.created(data={
        "id": memory.id, "type": memory.type, "name": memory.name,
        "embedding_status": memory.embedding_status, "deduped": deduped},
        message="摄入成功")
