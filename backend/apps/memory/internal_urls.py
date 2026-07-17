"""内部端点路由（设备 token 鉴权，跳过 cookie 中间件）。

/api/v1/internal/ingest/  → POST 摄入（wechat/oa 外部来源）
"""

from django.urls import path

from apps.memory.internal_views import internal_ingest

urlpatterns = [
    path("ingest/", internal_ingest, name="internal-ingest"),
]
