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
        try:
            async for chunk in stream:
                data = {"type": chunk.type, "content": chunk.content}
                if chunk.message_id:
                    data["message_id"] = chunk.message_id
                if chunk.request_id:
                    data["request_id"] = chunk.request_id
                if chunk.data:
                    data["data"] = chunk.data
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            logger.info(f"{context_name} SSE connection cancelled for user {user_id}")
            raise
        except Exception as e:
            logger.exception(f"{context_name} error")
            error_data = {"type": "error", "content": getattr(e, "message", str(e))}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def first_validation_error(serializer: serializers.Serializer) -> str:
    return str(next(iter(serializer.errors.values()))[0])
