from django.urls import path

from apps.chat import views
from apps.common.decorators import async_csrf_exempt

urlpatterns = [
    path("", async_csrf_exempt(views.chat), name="chat"),
    path("messages/", views.get_messages, name="messages"),
    path("generating/", views.get_generating_message, name="generating"),
    path("stop/", views.stop_generation, name="stop"),
    path("resume/", async_csrf_exempt(views.resume_generation), name="resume"),
    path("reconnect/", views.reconnect_stream, name="reconnect"),
]
