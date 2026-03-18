import logging

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpRequest, StreamingHttpResponse
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.chat.serializers import (
    ChatRequestSerializer,
    HistoryQuerySerializer,
    MessageResponseSerializer,
    ReconnectRequestSerializer,
    ResumeGenerationRequestSerializer,
    StopGenerationRequestSerializer,
)
from apps.chat.services import ChatService, HistoryService
from apps.common.sse import first_validation_error, make_sse_response, parse_sse_request
from apps.common.responses import ApiResponse, error_response

logger = logging.getLogger(__name__)


async def chat(request: HttpRequest) -> StreamingHttpResponse:
    validated, error = parse_sse_request(request, ChatRequestSerializer)
    if error:
        return error

    user_id = request.target_user_id
    content = validated["content"]
    attachment_uuids = validated.get("attachments")

    if attachment_uuids:
        from core.redis import get_redis

        # 多模态限流使用登录用户 ID（限流针对登录用户）
        login_user_id = request.user_id
        rate_limit = getattr(settings, "MULTIMODAL_RATE_LIMIT_SECONDS", 60)
        key = f"user:{login_user_id}:multimodal_rate_limit"
        redis = await get_redis()
        if not await redis.set(key, "1", nx=True, ex=rate_limit):
            ttl = await redis.ttl(key)
            return error_response(
                message=f"多模态推理请求过于频繁，请在 {ttl} 秒后重试",
                code="RATE_LIMIT",
                status_code=429,
            )

    stream = ChatService.send_message(user_id=user_id, content=content, attachment_uuids=attachment_uuids)
    return make_sse_response(stream, user_id, "Chat")


@api_view(["GET"])
def get_messages(request: Request) -> Response:
    serializer = HistoryQuerySerializer(data=request.query_params)
    if not serializer.is_valid():
        return ApiResponse.validation_error(message=first_validation_error(serializer))

    user_id = request.target_user_id
    limit = serializer.validated_data.get("limit", 50)
    before_sequence = serializer.validated_data.get("before_sequence")

    messages = async_to_sync(HistoryService.load_messages)(
        user_id=user_id, limit=limit + 1, before_sequence=before_sequence,
    )
    has_more = len(messages) > limit
    return ApiResponse.success(data={
        "messages": [MessageResponseSerializer(m).data for m in messages],
        "has_more": has_more,
    })


@api_view(["GET"])
def get_generating_message(request: Request) -> Response:
    user_id = request.target_user_id
    message = async_to_sync(HistoryService.get_generating_message)(user_id)
    return ApiResponse.success(data={"message": MessageResponseSerializer(message).data if message else None})


@api_view(["POST"])
def stop_generation(request: Request) -> Response:
    serializer = StopGenerationRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse.validation_error(message=first_validation_error(serializer))

    user_id = request.target_user_id
    request_id = serializer.validated_data["request_id"]
    success = async_to_sync(ChatService.stop_generation)(user_id, request_id)

    if success:
        return ApiResponse.success(message="停止信号已发送")
    return ApiResponse.not_found(message="未找到活跃的生成任务")


async def resume_generation(request: HttpRequest) -> StreamingHttpResponse:
    validated, error = parse_sse_request(request, ResumeGenerationRequestSerializer)
    if error:
        return error

    user_id = request.target_user_id
    request_id = validated["request_id"]
    stream = ChatService.resume_generation(user_id=user_id, request_id=request_id)
    return make_sse_response(stream, user_id, "Resume generation")


async def reconnect_stream(request: HttpRequest) -> StreamingHttpResponse:
    validated, error = parse_sse_request(request, ReconnectRequestSerializer, method="GET", source="query")
    if error:
        return error

    user_id = request.target_user_id
    request_id = validated["request_id"]
    stream = ChatService.reconnect_stream(user_id=user_id, request_id=request_id)
    return make_sse_response(stream, user_id, "Reconnect stream")
