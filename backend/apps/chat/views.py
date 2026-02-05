"""
聊天视图 (ASGI 原生异步视图)

参考:
- process-model.md#三、消息发送与流式响应流程（P_CHAT_001）
- process-model.md#四、历史消息加载流程（P_CHAT_002）

注意: SSE 流式响应视图使用 ASGI 原生异步实现，必须使用 uvicorn 启动服务
"""

from asgiref.sync import async_to_sync
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
from apps.chat.sse import first_validation_error, make_sse_response, parse_sse_request
from apps.common.responses import ApiResponse


async def chat(request: HttpRequest) -> StreamingHttpResponse:
    """
    发送消息并获取流式响应 (ASGI 原生异步视图)

    POST /api/v1/chat/
    """
    validated, error = parse_sse_request(request, ChatRequestSerializer)
    if error:
        return error

    user_id = request.user_id
    content = validated["content"]
    stream = ChatService.send_message(user_id=user_id, content=content)
    return make_sse_response(stream, user_id, "Chat")


@api_view(["GET"])
def get_messages(request: Request) -> Response:
    """
    获取历史消息

    GET /api/v1/chat/messages/
    """
    serializer = HistoryQuerySerializer(data=request.query_params)
    if not serializer.is_valid():
        return ApiResponse.validation_error(message=first_validation_error(serializer))

    user_id = request.user_id
    limit = serializer.validated_data.get("limit", 50)
    before_sequence = serializer.validated_data.get("before_sequence")

    messages = async_to_sync(HistoryService.load_messages)(
        user_id=user_id,
        limit=limit + 1,  # 多取一条用于判断是否有更多
        before_sequence=before_sequence,
    )

    has_more = len(messages) > limit

    return ApiResponse.success(
        data={
            "messages": [MessageResponseSerializer(m).data for m in messages],
            "has_more": has_more,
        }
    )


@api_view(["GET"])
def get_generating_message(request: Request) -> Response:
    """
    获取正在生成中的消息（用于页面刷新时检测）

    GET /api/v1/chat/generating/
    """
    user_id = request.user_id
    message = async_to_sync(HistoryService.get_generating_message)(user_id)

    return ApiResponse.success(
        data={"message": MessageResponseSerializer(message).data if message else None}
    )


@api_view(["POST"])
def stop_generation(request: Request) -> Response:
    """
    停止生成

    POST /api/v1/chat/stop/
    """
    serializer = StopGenerationRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse.validation_error(message=first_validation_error(serializer))

    user_id = request.user_id
    request_id = serializer.validated_data["request_id"]

    success = async_to_sync(ChatService.stop_generation)(user_id, request_id)

    if success:
        return ApiResponse.success(message="停止信号已发送")
    else:
        return ApiResponse.not_found(message="未找到活跃的生成任务")


async def resume_generation(request: HttpRequest) -> StreamingHttpResponse:
    """
    继续生成（从中断处恢复）(ASGI 原生异步视图)

    POST /api/v1/chat/resume/
    """
    validated, error = parse_sse_request(request, ResumeGenerationRequestSerializer)
    if error:
        return error

    user_id = request.user_id
    request_id = validated["request_id"]
    stream = ChatService.resume_generation(user_id=user_id, request_id=request_id)
    return make_sse_response(stream, user_id, "Resume generation")


async def reconnect_stream(request: HttpRequest) -> StreamingHttpResponse:
    """
    重连流式响应（用于页面刷新时重连生成中的消息）(ASGI 原生异步视图)

    GET /api/v1/chat/reconnect/?request_id={request_id}
    """
    validated, error = parse_sse_request(
        request, ReconnectRequestSerializer, method="GET", source="query"
    )
    if error:
        return error

    user_id = request.user_id
    request_id = validated["request_id"]
    stream = ChatService.reconnect_stream(user_id=user_id, request_id=request_id)
    return make_sse_response(stream, user_id, "Reconnect stream")
