"""
ASGI config for LinChat project.

ProtocolTypeRouter 多协议路由：
- HTTP 请求走 Django ASGI 应用
- WebSocket 请求通过 WebSocketTokenAuthMiddleware 路由到 voice routing

必须使用 uvicorn 启动: uvicorn core.asgi:application --host 0.0.0.0 --port 8002
"""
import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

# Django ASGI 应用必须在导入路由之前初始化
django_asgi_app = get_asgi_application()

from apps.common.websocket_auth import WebSocketTokenAuthMiddleware  # noqa: E402
from apps.voice.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": WebSocketTokenAuthMiddleware(
            URLRouter(websocket_urlpatterns)
        ),
    }
)
