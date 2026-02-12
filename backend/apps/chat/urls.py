"""
聊天模块路由

参考:
- process-model.md#三、消息发送与流式响应流程（P_CHAT_001）
- process-model.md#四、历史消息加载流程（P_CHAT_002）
- specs/008-multimodal-minicpm/contracts/media-upload.yaml
"""

from django.urls import path

from apps.chat import views
from apps.common.decorators import async_csrf_exempt

urlpatterns = [
    # POST /api/v1/chat/ - 发送消息并获取流式响应 (ASGI 原生异步视图)
    path("", async_csrf_exempt(views.chat), name="chat"),
    # GET /api/v1/chat/messages/ - 获取历史消息
    path("messages/", views.get_messages, name="messages"),
    # GET /api/v1/chat/generating/ - 获取正在生成中的消息
    path("generating/", views.get_generating_message, name="generating"),
    # POST /api/v1/chat/stop/ - 停止生成
    path("stop/", views.stop_generation, name="stop"),
    # POST /api/v1/chat/resume/ - 继续生成（status=3中断消息）(ASGI 原生异步视图)
    path("resume/", async_csrf_exempt(views.resume_generation), name="resume"),
    # GET /api/v1/chat/reconnect/ - 重连流式响应（status=2生成中消息）(ASGI 原生异步视图)
    path("reconnect/", views.reconnect_stream, name="reconnect"),
    # ============ 媒体文件相关 ============
    # POST /api/v1/chat/media/upload/ - 上传媒体文件
    path("media/upload/", views.upload_media, name="upload_media"),
    # GET /api/v1/chat/media/{uuid}/ - 获取原始媒体文件
    path("media/<str:uuid>/", views.get_media, name="get_media"),
    # ============ 推理控制相关 ============
    # POST /api/v1/chat/inference/cancel/ - 取消推理任务
    path("inference/cancel/", views.cancel_inference, name="cancel_inference"),
    # ============ 文档解析相关 ============
    # POST /api/v1/chat/documents/parse/ - 创建文档解析任务
    path("documents/parse/", views.parse_document, name="parse_document"),
    # GET /api/v1/chat/documents/tasks/{task_id}/ - 查询任务状态
    path("documents/tasks/<str:task_id>/", views.get_parse_task_status, name="parse_task_status"),
    # GET /api/v1/chat/documents/tasks/{task_id}/result/ - 获取解析结果
    path("documents/tasks/<str:task_id>/result/", views.get_parse_task_result, name="parse_task_result"),
    # ============ TTS 语音合成相关 ============
    # POST /api/v1/chat/tts/ - 获取 AI 回复的语音合成
    path("tts/", views.get_tts_audio, name="get_tts_audio"),
]
