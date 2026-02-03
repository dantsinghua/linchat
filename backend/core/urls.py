"""
LinChat URL Configuration

API 版本: /api/v1/ (符合宪法1.2)
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # API v1 路由
    path("api/v1/", include([
        path("auth/", include("apps.users.urls")),
        path("chat/", include("apps.chat.urls")),
        path("models/", include("apps.models.urls")),
        path("memories/", include("apps.memory.urls")),
        path("", include("apps.common.urls")),
    ])),
]
