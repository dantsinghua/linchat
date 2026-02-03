"""
记忆 API 路由

/api/v1/memories/           → 列表 / 创建
/api/v1/memories/<id>/      → 详情 / 更新 / 删除
/api/v1/memories/search/    → 搜索 (Phase 4)
"""

from django.urls import path

from apps.memory.views import memory_detail, memory_list_create, memory_search

urlpatterns = [
    path("", memory_list_create, name="memory-list-create"),
    path("search/", memory_search, name="memory-search"),
    path("<int:memory_id>/", memory_detail, name="memory-detail"),
]
