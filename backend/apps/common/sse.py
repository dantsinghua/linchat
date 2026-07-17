import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from rest_framework import serializers

from apps.graph.services.types import StreamChunk

logger = logging.getLogger(__name__)


def parse_sse_request(
    request: HttpRequest,
    serializer_class: type[serializers.Serializer],
    method: str = "POST",
    source: str = "body",
) -> tuple[Optional[dict], Optional[JsonResponse]]:
    if request.method != method:
        return None, JsonResponse(
            {"code": "METHOD_NOT_ALLOWED", "message": "Method not allowed", "data": None}, status=405,
        )
    if source == "body":
        try:
            data = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return None, JsonResponse(
                {"code": "VALIDATION_ERROR", "message": "Invalid JSON", "data": None}, status=400,
            )
    else:
        data = request.GET

    sz = serializer_class(data=data)
    if not sz.is_valid():
        first_error = next(iter(sz.errors.values()))[0]
        return None, JsonResponse(
            {"code": "VALIDATION_ERROR", "message": str(first_error), "data": None}, status=400,
        )
    return sz.validated_data, None


def make_sse_response(stream: AsyncGenerator[StreamChunk, None], user_id: int, context_name: str) -> StreamingHttpResponse:
    async def event_generator():
        from django.conf import settings

        heartbeat_interval = getattr(settings, "SSE_HEARTBEAT_INTERVAL", 15)
        try:
            aiter = stream.__aiter__()
            # 用 shield 保护 __anext__() 不被 wait_for 超时取消，
            # 否则 async generator 状态会被破坏导致流提前终止
            pending_next = None
            while True:
                try:
                    if pending_next is None:
                        pending_next = asyncio.ensure_future(aiter.__anext__())
                    chunk = await asyncio.wait_for(
                        asyncio.shield(pending_next), timeout=heartbeat_interval
                    )
                    pending_next = None  # 已消费，下轮重新获取
                except asyncio.TimeoutError:
                    # 心跳 data 事件：前端可感知并更新 lastDataTime，用于超时检测
                    # batch-05：wire 保持 {"type":"heartbeat"} 不变（前端零改动）；
                    # trace_id 仅从后端 log extra 聚合，通过 JSONFormatter 自动注入
                    logger.debug("sse heartbeat", extra={"context_name": context_name, "user_id": user_id})
                    yield 'data: {"type":"heartbeat"}\n\n'
                    continue  # 重用同一个 pending_next
                except StopAsyncIteration:
                    break
                data = {"type": chunk.type, "content": chunk.content}
                if chunk.message_id:
                    data["message_id"] = chunk.message_id
                if chunk.request_id:
                    data["request_id"] = chunk.request_id
                if chunk.data:
                    data["data"] = chunk.data
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            logger.info("sse cancelled", extra={"context_name": context_name, "user_id": user_id})
            raise
        except Exception as e:
            logger.exception("sse error", extra={"context_name": context_name, "user_id": user_id})
            error_data = {"type": "error", "content": getattr(e, "message", str(e))}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def first_validation_error(serializer: serializers.Serializer) -> str:
    return str(next(iter(serializer.errors.values()))[0])
