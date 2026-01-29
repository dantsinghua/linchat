"""
模型配置 URL 路由

参考: contracts/api.yaml
"""
from django.urls import path

from apps.models.views import ModelDetailView, ModelListView

urlpatterns = [
    path("", ModelListView.as_view(), name="model-list"),
    path("<int:pk>/", ModelDetailView.as_view(), name="model-detail"),
]
