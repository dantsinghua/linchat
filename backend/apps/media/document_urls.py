from django.urls import path

from apps.media import views

urlpatterns = [
    path("parse/", views.parse_document, name="parse_document"),
    path("tasks/<str:task_id>/", views.get_parse_task_status, name="parse_task_status"),
    path("tasks/<str:task_id>/result/", views.get_parse_task_result, name="parse_task_result"),
]
