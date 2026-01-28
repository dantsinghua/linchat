"""
公共模块视图 (ASGI 原生异步视图)

参考: process-model.md#一点五、单点登录SSE推送流程
"""
import logging

from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.views import View

from apps.common.event_service import EventService

logger = logging.getLogger(__name__)


class EventsView(View):
    """
    SSE 事件视图 (ASGI 原生异步视图)

    GET /api/v1/events - SSE 事件订阅

    参考: process-model.md#一点五、单点登录SSE推送流程
    """

    async def get(self, request: HttpRequest) -> StreamingHttpResponse:
        """
        SSE 事件订阅 (ASGI 原生异步)

        前端建立长连接监听服务端推送事件（如单点登录登出通知）
        """
        user_id = getattr(request, "user_id", None)

        if not user_id:
            return JsonResponse(
                {
                    "code": "UNAUTHORIZED",
                    "message": "未登录",
                    "data": None,
                },
                status=401,
            )

        async def event_generator():
            """ASGI 原生异步 SSE 事件生成器"""
            async for event in EventService.subscribe_user_events(user_id):
                yield event

        response = StreamingHttpResponse(
            event_generator(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
