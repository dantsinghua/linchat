"""
聊天视图

参考:
- process-model.md#三、消息发送与流式响应流程（P_CHAT_001）
- process-model.md#四、历史消息加载流程（P_CHAT_002）
"""

import asyncio
import json
import logging

from asgiref.sync import async_to_sync
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from apps.chat.serializers import (ChatRequestSerializer,
                                   HistoryQuerySerializer,
                                   MessageResponseSerializer,
                                   ReconnectRequestSerializer,
                                   ResumeGenerationRequestSerializer,
                                   StopGenerationRequestSerializer)
from apps.chat.services import ChatService, HistoryService
from apps.common.responses import ApiResponse

logger = logging.getLogger(__name__)


@csrf_exempt
def chat(request: HttpRequest) -> StreamingHttpResponse:
    """
    发送消息并获取流式响应

    POST /api/v1/chat/

    参考: process-model.md#三、消息发送与流式响应流程

    Request Body:
        content: str - 消息内容（最大4000字符）

    Response:
        SSE 流式响应
        - data: {"type": "content", "content": "...", "message_id": 123}
        - data: {"type": "done", "content": "", "message_id": 123}
        - data: {"type": "error", "content": "错误信息"}
        - data: {"type": "interrupted", "content": "[已中断]", "message_id": 123}
    """
    if request.method != "POST":
        return JsonResponse(
            {"code": "METHOD_NOT_ALLOWED", "message": "Method not allowed", "data": None},
            status=405,
        )

    # 解析请求体
    try:
        body = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse(
            {"code": "VALIDATION_ERROR", "message": "Invalid JSON", "data": None},
            status=400,
        )

    # 验证请求数据
    serializer = ChatRequestSerializer(data=body)
    if not serializer.is_valid():
        errors = serializer.errors
        first_error = next(iter(errors.values()))[0]
        return JsonResponse(
            {"code": "VALIDATION_ERROR", "message": str(first_error), "data": None},
            status=400,
        )

    user_id = request.user_id
    content = serializer.validated_data["content"]

    def event_generator():
        """同步 SSE 事件生成器，使用 async_to_sync 包装异步调用"""
        import queue
        import threading

        result_queue = queue.Queue()

        def run_async():
            """在新线程中运行异步生成器"""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def collect():
                    try:
                        async for chunk in ChatService.send_message(
                            user_id=user_id, content=content
                        ):
                            data = {
                                "type": chunk.type,
                                "content": chunk.content,
                            }
                            if chunk.message_id:
                                data["message_id"] = chunk.message_id
                            result_queue.put(f"data: {json.dumps(data, ensure_ascii=False)}\n\n")
                    except Exception as e:
                        logger.exception("Chat error")
                        error_data = {"type": "error", "content": getattr(e, "message", str(e))}
                        result_queue.put(f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n")
                    finally:
                        result_queue.put(None)  # 结束信号

                loop.run_until_complete(collect())
            finally:
                loop.close()

        # 启动异步线程
        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()

        # 从队列中读取结果并 yield
        while True:
            item = result_queue.get()
            if item is None:
                break
            yield item

    response = StreamingHttpResponse(
        event_generator(), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # Nginx 禁用缓冲
    return response


@api_view(["GET"])
def get_messages(request: Request) -> Response:
    """
    获取历史消息

    GET /api/v1/chat/messages/

    参考: process-model.md#四、历史消息加载流程

    Query Parameters:
        limit: int - 返回数量（默认50，最大100）
        before_sequence: int - 游标序号（分页用）

    Response:
        {
            "code": "OK",
            "message": "success",
            "data": {
                "messages": [...],
                "has_more": true
            }
        }
    """
    # 验证查询参数
    serializer = HistoryQuerySerializer(data=request.query_params)
    if not serializer.is_valid():
        errors = serializer.errors
        first_error = next(iter(errors.values()))[0]
        return ApiResponse.validation_error(message=str(first_error))

    user_id = request.user_id
    limit = serializer.validated_data.get("limit", 50)
    before_sequence = serializer.validated_data.get("before_sequence")

    messages = async_to_sync(HistoryService.load_messages)(
        user_id=user_id,
        limit=limit + 1,  # 多取一条用于判断是否有更多
        before_sequence=before_sequence,
    )

    # 判断是否有更多
    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

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

    参考: behavior-model.md#2.4 流式响应重连

    Response:
        {
            "code": "OK",
            "data": {
                "message": {...} | null
            }
        }
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

    参考: spec.md US2场景9 - 停止按钮逻辑

    Request Body:
        request_id: str - 请求ID

    Response:
        {
            "code": "OK",
            "message": "停止信号已发送"
        }
    """
    serializer = StopGenerationRequestSerializer(data=request.data)
    if not serializer.is_valid():
        errors = serializer.errors
        first_error = next(iter(errors.values()))[0]
        return ApiResponse.validation_error(message=str(first_error))

    user_id = request.user_id
    request_id = serializer.validated_data["request_id"]

    success = async_to_sync(ChatService.stop_generation)(user_id, request_id)

    if success:
        return ApiResponse.success(message="停止信号已发送")
    else:
        return ApiResponse.not_found(message="未找到活跃的生成任务")


@csrf_exempt
@require_http_methods(["POST"])
def resume_generation(request: HttpRequest) -> StreamingHttpResponse:
    """
    继续生成（从中断处恢复）

    POST /api/v1/chat/resume/

    参考: behavior-model.md#2.5 继续生成（B_CHAT_005）
    用于 status=3（中断）消息的继续生成

    Request Body:
        request_id: str - 原请求ID

    Response:
        SSE 流式响应
    """
    import queue
    import threading

    # 解析请求体
    try:
        body = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse(
            {"code": "VALIDATION_ERROR", "message": "Invalid JSON", "data": None},
            status=400,
        )

    serializer = ResumeGenerationRequestSerializer(data=body)
    if not serializer.is_valid():
        errors = serializer.errors
        first_error = next(iter(errors.values()))[0]
        return JsonResponse(
            {"code": "VALIDATION_ERROR", "message": str(first_error), "data": None},
            status=400,
        )

    user_id = request.user_id
    request_id = serializer.validated_data["request_id"]

    def event_generator():
        """同步 SSE 事件生成器"""
        result_queue = queue.Queue()

        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def collect():
                    try:
                        async for chunk in ChatService.resume_generation(
                            user_id=user_id, request_id=request_id
                        ):
                            data = {
                                "type": chunk.type,
                                "content": chunk.content,
                            }
                            if chunk.message_id:
                                data["message_id"] = chunk.message_id
                            result_queue.put(f"data: {json.dumps(data, ensure_ascii=False)}\n\n")
                    except Exception as e:
                        logger.exception("Resume generation error")
                        error_data = {"type": "error", "content": str(e)}
                        result_queue.put(f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n")
                    finally:
                        result_queue.put(None)

                loop.run_until_complete(collect())
            finally:
                loop.close()

        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()

        while True:
            item = result_queue.get()
            if item is None:
                break
            yield item

    response = StreamingHttpResponse(
        event_generator(), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_http_methods(["GET"])
def reconnect_stream(request: HttpRequest) -> StreamingHttpResponse:
    """
    重连流式响应（用于页面刷新时重连生成中的消息）

    GET /api/v1/chat/reconnect/?request_id={request_id}

    参考: behavior-model.md#2.4 流式响应重连（B_CHAT_004）
    用于 status=2（生成中）消息的 SSE 重连

    Query Parameters:
        request_id: str - 请求ID

    Response:
        SSE 流式响应
    """
    import queue
    import threading

    query_params = request.GET

    serializer = ReconnectRequestSerializer(data=query_params)
    if not serializer.is_valid():
        errors = serializer.errors
        first_error = next(iter(errors.values()))[0]
        return JsonResponse(
            {"code": "VALIDATION_ERROR", "message": str(first_error), "data": None},
            status=400,
        )

    user_id = request.user_id
    request_id = serializer.validated_data["request_id"]

    def event_generator():
        """同步 SSE 事件生成器"""
        result_queue = queue.Queue()

        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def collect():
                    try:
                        async for chunk in ChatService.reconnect_stream(
                            user_id=user_id, request_id=request_id
                        ):
                            data = {
                                "type": chunk.type,
                                "content": chunk.content,
                            }
                            if chunk.message_id:
                                data["message_id"] = chunk.message_id
                            result_queue.put(f"data: {json.dumps(data, ensure_ascii=False)}\n\n")
                    except Exception as e:
                        logger.exception("Reconnect stream error")
                        error_data = {"type": "error", "content": str(e)}
                        result_queue.put(f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n")
                    finally:
                        result_queue.put(None)

                loop.run_until_complete(collect())
            finally:
                loop.close()

        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()

        while True:
            item = result_queue.get()
            if item is None:
                break
            yield item

    response = StreamingHttpResponse(
        event_generator(), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
