"""WebSocket URL 路由

参考: specs/009-voice-interaction/plan.md WebSocket 路由
"""

from django.urls import re_path

from apps.voice.consumers import VoiceConsumer

websocket_urlpatterns = [
    re_path(r"ws/voice/$", VoiceConsumer.as_asgi()),
]
