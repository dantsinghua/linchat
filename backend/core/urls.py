"""
LinChat URL Configuration

API 版本: /api/v1/ (符合宪法1.2)
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # API v1 路由（注意顺序：具体前缀必须在通用前缀之前）
    path("api/v1/", include([
        path("auth/", include("apps.users.urls")),
        path("members/", include("apps.users.member_urls")),
        path("chat/media/", include("apps.media.urls")),
        path("chat/documents/", include("apps.media.document_urls")),
        path("chat/inference/", include("apps.graph.urls")),
        path("chat/", include("apps.chat.urls")),
        path("models/", include("apps.models.urls")),
        path("memories/", include("apps.memory.urls")),
        path("voice/", include("apps.voice.urls")),
        path("", include("apps.common.urls")),
    ])),
]
