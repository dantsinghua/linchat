from asgiref.sync import async_to_sync
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.common.responses import ApiResponse
from apps.graph.services.inference_service import inference_service


@api_view(["POST"])
def cancel_inference(request: Request) -> Response:
    user_id = request.user_id
    request_id = request.data.get("request_id")
    success, cancelled_id = async_to_sync(inference_service.cancel_task)(
        user_id=user_id, request_id=request_id,
    )
    if success:
        return ApiResponse.success(data={"cancelled": True, "request_id": cancelled_id})
    return ApiResponse.error(code="NO_ACTIVE_INFERENCE", message="没有进行中的推理任务", status_code=404)
