"""
聊天视图 (ASGI 原生异步视图)

参考:
- process-model.md#三、消息发送与流式响应流程（P_CHAT_001）
- process-model.md#四、历史消息加载流程（P_CHAT_002）
- specs/008-multimodal-minicpm/contracts/media-upload.yaml

注意: SSE 流式响应视图使用 ASGI 原生异步实现，必须使用 uvicorn 启动服务
"""

import logging
from io import BytesIO

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import FileResponse, HttpRequest, StreamingHttpResponse
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from apps.chat.serializers import (
    ChatRequestSerializer,
    DocumentParseRequestSerializer,
    HistoryQuerySerializer,
    MediaAttachmentSerializer,
    MessageResponseSerializer,
    ReconnectRequestSerializer,
    ResumeGenerationRequestSerializer,
    StopGenerationRequestSerializer,
)
from apps.chat.services import (
    ChatService,
    DocumentParseError,
    DocumentParseService,
    HistoryService,
    MediaService,
    MediaUploadError,
    inference_service,
)
from apps.chat.sse import first_validation_error, make_sse_response, parse_sse_request
from apps.common.responses import ApiResponse, error_response

logger = logging.getLogger(__name__)


async def chat(request: HttpRequest) -> StreamingHttpResponse:
    """
    发送消息并获取流式响应 (ASGI 原生异步视图)

    POST /api/v1/chat/
    """
    validated, error = parse_sse_request(request, ChatRequestSerializer)
    if error:
        return error

    user_id = request.user_id
    content = validated["content"]
    attachment_uuids = validated.get("attachments")

    # 多模态请求限流：MiniCPM-o 不支持并发，每 N 秒最多 1 次
    if attachment_uuids:
        from core.redis import get_redis

        rate_limit = getattr(settings, "MULTIMODAL_RATE_LIMIT_SECONDS", 60)
        key = f"user:{user_id}:multimodal_rate_limit"
        redis = await get_redis()
        if not await redis.set(key, "1", nx=True, ex=rate_limit):
            ttl = await redis.ttl(key)
            return error_response(
                message=f"多模态推理请求过于频繁，请在 {ttl} 秒后重试",
                code="RATE_LIMIT",
                status_code=429,
            )

    stream = ChatService.send_message(
        user_id=user_id,
        content=content,
        attachment_uuids=attachment_uuids,
    )
    return make_sse_response(stream, user_id, "Chat")


@api_view(["GET"])
def get_messages(request: Request) -> Response:
    """
    获取历史消息

    GET /api/v1/chat/messages/
    """
    serializer = HistoryQuerySerializer(data=request.query_params)
    if not serializer.is_valid():
        return ApiResponse.validation_error(message=first_validation_error(serializer))

    user_id = request.user_id
    limit = serializer.validated_data.get("limit", 50)
    before_sequence = serializer.validated_data.get("before_sequence")

    messages = async_to_sync(HistoryService.load_messages)(
        user_id=user_id,
        limit=limit + 1,  # 多取一条用于判断是否有更多
        before_sequence=before_sequence,
    )

    has_more = len(messages) > limit

    return ApiResponse.success(
        data={
            "messages": [MessageResponseSerializer(m).data for m in messages],
            "has_more": has_more,
        }
    )


@api_view(["GET"])
def get_generating_message(request: Request) -> Response:
    """
    获取正在生成中的消息（用于页面刷新时检测）

    GET /api/v1/chat/generating/
    """
    user_id = request.user_id
    message = async_to_sync(HistoryService.get_generating_message)(user_id)

    return ApiResponse.success(
        data={"message": MessageResponseSerializer(message).data if message else None}
    )


@api_view(["POST"])
def stop_generation(request: Request) -> Response:
    """
    停止生成

    POST /api/v1/chat/stop/
    """
    serializer = StopGenerationRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse.validation_error(message=first_validation_error(serializer))

    user_id = request.user_id
    request_id = serializer.validated_data["request_id"]

    success = async_to_sync(ChatService.stop_generation)(user_id, request_id)

    if success:
        return ApiResponse.success(message="停止信号已发送")
    else:
        return ApiResponse.not_found(message="未找到活跃的生成任务")


async def resume_generation(request: HttpRequest) -> StreamingHttpResponse:
    """
    继续生成（从中断处恢复）(ASGI 原生异步视图)

    POST /api/v1/chat/resume/
    """
    validated, error = parse_sse_request(request, ResumeGenerationRequestSerializer)
    if error:
        return error

    user_id = request.user_id
    request_id = validated["request_id"]
    stream = ChatService.resume_generation(user_id=user_id, request_id=request_id)
    return make_sse_response(stream, user_id, "Resume generation")


async def reconnect_stream(request: HttpRequest) -> StreamingHttpResponse:
    """
    重连流式响应（用于页面刷新时重连生成中的消息）(ASGI 原生异步视图)

    GET /api/v1/chat/reconnect/?request_id={request_id}
    """
    validated, error = parse_sse_request(
        request, ReconnectRequestSerializer, method="GET", source="query"
    )
    if error:
        return error

    user_id = request.user_id
    request_id = validated["request_id"]
    stream = ChatService.reconnect_stream(user_id=user_id, request_id=request_id)
    return make_sse_response(stream, user_id, "Reconnect stream")


# ============ 媒体上传相关视图 ============


@api_view(["POST"])
@parser_classes([MultiPartParser])
def upload_media(request: Request) -> Response:
    """
    上传媒体文件

    POST /api/v1/chat/media/upload/

    参考: specs/008-multimodal-minicpm/contracts/media-upload.yaml
    """
    user_id = request.user_id

    # 检查文件
    if "file" not in request.FILES:
        return ApiResponse.validation_error(message="请选择要上传的文件")

    file = request.FILES["file"]
    file_name = file.name or "unknown"
    mime_type = file.content_type or "application/octet-stream"
    file_size = file.size

    try:
        attachment = async_to_sync(MediaService.upload)(
            user_id=user_id,
            file_data=file,
            file_name=file_name,
            mime_type=mime_type,
            file_size=file_size,
        )

        serializer = MediaAttachmentSerializer(attachment)
        return ApiResponse.success(data=serializer.data)

    except MediaUploadError as e:
        logger.warning(f"媒体上传失败: user_id={user_id}, code={e.code}, message={e.message}")
        return ApiResponse.error(code=e.code, message=e.message)
    except Exception as e:
        logger.error(f"媒体上传异常: user_id={user_id}, error={e}")
        return ApiResponse.error(message="上传失败，请稍后重试")


@api_view(["GET"])
def get_media(request: Request, uuid: str) -> Response:
    """
    获取原始媒体文件

    GET /api/v1/chat/media/{uuid}/

    参考: specs/008-multimodal-minicpm/contracts/media-upload.yaml
    """
    user_id = request.user_id

    # 两步查询：先检查附件是否存在，再校验所有权 (FR-031)
    attachment = async_to_sync(MediaService.get_attachment_any_user)(uuid)
    if not attachment:
        return ApiResponse.not_found(message="附件不存在")
    if attachment.user_id != user_id:
        return ApiResponse.forbidden(message="无权访问该附件")

    # 检查是否过期
    if attachment.is_expired:
        return ApiResponse.error(
            code="ATTACHMENT_EXPIRED",
            message="文件已过期",
            data=None,
            status_code=410,
        )

    try:
        # 获取文件内容
        file_bytes = MediaService.get_media_file(attachment)

        # 返回文件响应
        return FileResponse(
            BytesIO(file_bytes),
            content_type=attachment.mime_type,
            filename=attachment.file_name,
        )
    except MediaUploadError as e:
        return ApiResponse.error(code=e.code, message=e.message, status_code=410)
    except Exception as e:
        logger.error(f"获取媒体文件失败: uuid={uuid}, error={e}")
        return ApiResponse.error(message="获取文件失败")


# ============ 推理控制相关视图 ============


@api_view(["POST"])
def cancel_inference(request: Request) -> Response:
    """
    取消推理任务

    POST /api/v1/chat/inference/cancel/

    参考: specs/008-multimodal-minicpm/contracts/inference-cancel.yaml
    """
    user_id = request.user_id
    request_id = request.data.get("request_id")

    success, cancelled_id = async_to_sync(inference_service.cancel_task)(
        user_id=user_id,
        request_id=request_id,
    )

    if success:
        return ApiResponse.success(
            data={"cancelled": True, "request_id": cancelled_id}
        )
    else:
        return ApiResponse.error(
            code="NO_ACTIVE_INFERENCE",
            message="没有进行中的推理任务",
            status_code=404,
        )


# ============ 文档解析相关视图 ============


@api_view(["POST"])
def parse_document(request: Request) -> Response:
    """
    创建文档解析任务

    POST /api/v1/chat/documents/parse/

    接收已上传到 MinIO 的文档附件 UUID，后端从 MinIO 下载文件后
    透传 Gateway /v1/documents/parse 创建异步解析任务。

    参考: specs/008-multimodal-minicpm/contracts/document-parse.yaml
    """
    user_id = request.user_id

    serializer = DocumentParseRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return ApiResponse.validation_error(
            message=first_validation_error(serializer)
        )

    attachment_uuid = serializer.validated_data["attachment_uuid"]
    pages = serializer.validated_data.get("pages")

    try:
        result = async_to_sync(DocumentParseService.parse_document)(
            user_id=user_id,
            attachment_uuid=attachment_uuid,
            pages=pages,
        )

        return ApiResponse.success(
            data=result,
            status_code=202,
        )

    except DocumentParseError as e:
        logger.warning(
            f"文档解析失败: user_id={user_id}, code={e.code}, message={e.message}"
        )
        status_map = {
            "ATTACHMENT_NOT_FOUND": 404,
            "ATTACHMENT_ACCESS_DENIED": 403,
            "ATTACHMENT_EXPIRED": 410,
            "GATEWAY_NOT_CONFIGURED": 503,
            "GATEWAY_TIMEOUT": 504,
        }
        return ApiResponse.error(
            code=e.code,
            message=e.message,
            data=e.details,
            status_code=status_map.get(e.code, 400),
        )
    except Exception as e:
        logger.error(f"文档解析异常: user_id={user_id}, error={e}")
        return ApiResponse.error(message="文档解析失败，请稍后重试")


@api_view(["GET"])
def get_parse_task_status(request: Request, task_id: str) -> Response:
    """
    查询文档解析任务状态

    GET /api/v1/chat/documents/tasks/{task_id}/

    两层权限校验：先校验 Redis 所有权键，再查询 Gateway。
    参考: document-parse-api.yaml, T075
    """
    user_id = request.user_id
    try:
        # 所有权校验
        async_to_sync(DocumentParseService.verify_task_ownership)(task_id, user_id)

        result = async_to_sync(DocumentParseService.poll_task_status)(task_id)
        return ApiResponse.success(data=result)

    except DocumentParseError as e:
        logger.warning(
            f"查询解析任务失败: task_id={task_id}, code={e.code}, message={e.message}"
        )
        status_map = {
            "TASK_NOT_FOUND": 404,
            "TASK_ACCESS_DENIED": 403,
            "E6009": 410,
        }
        return ApiResponse.error(
            code=e.code,
            message=e.message,
            data=e.details,
            status_code=status_map.get(e.code),
        )
    except Exception as e:
        logger.error(f"查询解析任务异常: task_id={task_id}, error={e}")
        return ApiResponse.error(message="查询任务状态失败")


@api_view(["GET"])
def get_parse_task_result(request: Request, task_id: str) -> Response:
    """
    获取文档解析结果

    GET /api/v1/chat/documents/tasks/{task_id}/result/

    两层权限校验：先校验 Redis 所有权键，再查询 Gateway。
    参考: document-parse-api.yaml, T075
    """
    user_id = request.user_id
    format_param = request.query_params.get("format", "markdown")
    if format_param not in ("markdown", "json"):
        return ApiResponse.validation_error(
            message="format 参数仅支持 markdown 或 json"
        )

    try:
        # 所有权校验
        async_to_sync(DocumentParseService.verify_task_ownership)(task_id, user_id)

        result = async_to_sync(DocumentParseService.get_task_result)(
            task_id, format=format_param
        )

        return ApiResponse.success(data={"content": result, "format": format_param})

    except DocumentParseError as e:
        logger.warning(
            f"获取解析结果失败: task_id={task_id}, code={e.code}, message={e.message}"
        )
        status_map = {
            "TASK_NOT_FOUND": 404,
            "TASK_ACCESS_DENIED": 403,
            "E6009": 410,
        }
        return ApiResponse.error(
            code=e.code,
            message=e.message,
            data=e.details,
            status_code=status_map.get(e.code),
        )
    except Exception as e:
        logger.error(f"获取解析结果异常: task_id={task_id}, error={e}")
        return ApiResponse.error(message="获取解析结果失败")
