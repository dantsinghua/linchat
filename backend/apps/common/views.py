"""
公共模块视图

参考: process-model.md#一点五、单点登录SSE推送流程
"""
import logging

from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.common.event_service import EventService, create_sse_response

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class EventsView(View):
    """
    SSE 事件视图

    GET /api/v1/events - SSE 事件订阅

    参考: process-model.md#一点五、单点登录SSE推送流程
    """

    def get(self, request: HttpRequest) -> StreamingHttpResponse:
        """
        SSE 事件订阅

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

        # 创建 SSE 响应
        generator = EventService.subscribe_user_events(user_id)
        return create_sse_response(generator)
