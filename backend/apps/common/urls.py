"""
公共模块路由

参考: process-model.md#一点五、单点登录SSE推送流程
"""

from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from apps.common.views import EventsView

urlpatterns = [
    # SSE 事件订阅 - 需要认证 (ASGI 原生异步视图)
    # GET /api/v1/events
    # 用于单点登录登出事件推送
    path("events", csrf_exempt(EventsView.as_view()), name="events"),
    # 健康检查端点将在 T058 添加
    # path("health/live", HealthLiveView.as_view(), name="health-live"),
    # path("health/ready", HealthReadyView.as_view(), name="health-ready"),
]
