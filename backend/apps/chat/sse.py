"""
SSE 视图辅助函数

提取 3 个 SSE 视图的公共逻辑：请求解析、流式响应包装。
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from rest_framework import serializers

from apps.chat.services.types import StreamChunk

logger = logging.getLogger(__name__)


def parse_sse_request(
    request: HttpRequest,
    serializer_class: type[serializers.Serializer],
    method: str = "POST",
    source: str = "body",
) -> tuple[Optional[dict], Optional[JsonResponse]]:
    """
    统一解析 SSE 视图请求：方法检查 + JSON 解析 + 序列化验证。

    Args:
        request: HTTP 请求
        serializer_class: 序列化器类
        method: 期望的 HTTP 方法
        source: 数据来源，"body" 表示请求体，"query" 表示查询参数

    Returns:
        (validated_data, None) 成功时返回验证后数据
        (None, error_response) 失败时返回错误响应
    """
    if request.method != method:
        return None, JsonResponse(
            {"code": "METHOD_NOT_ALLOWED", "message": "Method not allowed", "data": None},
            status=405,
        )

    if source == "body":
        try:
            data = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return None, JsonResponse(
                {"code": "VALIDATION_ERROR", "message": "Invalid JSON", "data": None},
                status=400,
            )
    else:
        data = request.GET

    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        first_error = next(iter(serializer.errors.values()))[0]
        return None, JsonResponse(
            {"code": "VALIDATION_ERROR", "message": str(first_error), "data": None},
            status=400,
        )

    return serializer.validated_data, None


def make_sse_response(
    stream: AsyncGenerator[StreamChunk, None],
    user_id: int,
    context_name: str,
) -> StreamingHttpResponse:
    """
    将 StreamChunk 异步生成器包装为 SSE StreamingHttpResponse。

    统一处理 JSON 序列化、CancelledError/Exception 捕获、响应头设置。
    """

    async def event_generator():
        try:
            async for chunk in stream:
                data = {"type": chunk.type, "content": chunk.content}
                if chunk.message_id:
                    data["message_id"] = chunk.message_id
                if chunk.request_id:
                    data["request_id"] = chunk.request_id
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            logger.info(f"{context_name} SSE connection cancelled for user {user_id}")
            raise
        except Exception as e:
            logger.exception(f"{context_name} error")
            error_data = {"type": "error", "content": getattr(e, "message", str(e))}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    response = StreamingHttpResponse(
        event_generator(), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def first_validation_error(serializer: serializers.Serializer) -> str:
    """从序列化器错误中提取第一个验证错误消息"""
    return str(next(iter(serializer.errors.values()))[0])
