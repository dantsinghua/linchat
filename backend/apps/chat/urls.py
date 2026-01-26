"""
聊天模块路由

参考:
- process-model.md#三、消息发送与流式响应流程（P_CHAT_001）
- process-model.md#四、历史消息加载流程（P_CHAT_002）
"""

from django.urls import path

from apps.chat import views

urlpatterns = [
    # POST /api/v1/chat/ - 发送消息并获取流式响应
    path("", views.chat, name="chat"),
    # GET /api/v1/chat/messages/ - 获取历史消息
    path("messages/", views.get_messages, name="messages"),
    # GET /api/v1/chat/generating/ - 获取正在生成中的消息
    path("generating/", views.get_generating_message, name="generating"),
    # POST /api/v1/chat/stop/ - 停止生成
    path("stop/", views.stop_generation, name="stop"),
    # POST /api/v1/chat/resume/ - 继续生成（status=3中断消息）
    path("resume/", views.resume_generation, name="resume"),
    # GET /api/v1/chat/reconnect/ - 重连流式响应（status=2生成中消息）
    path("reconnect/", views.reconnect_stream, name="reconnect"),
]
