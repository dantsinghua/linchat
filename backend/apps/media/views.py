import logging
from io import BytesIO

from asgiref.sync import async_to_sync
from django.http import FileResponse
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from django.conf import settings

from apps.common.responses import ApiResponse
from apps.common.sse import first_validation_error
from apps.media.serializers import DocumentParseRequestSerializer, MediaAttachmentSerializer
from apps.media.services import DocumentParseError, DocumentParseService, MediaService, MediaUploadError

logger = logging.getLogger(__name__)


@api_view(["POST"])
@parser_classes([MultiPartParser])
def upload_media(request: Request) -> Response:
    user_id = request.user_id
    if "file" not in request.FILES:
        return ApiResponse.validation_error(message="请选择要上传的文件")
    file = request.FILES["file"]
    try:
        attachment = async_to_sync(MediaService.upload)(user_id=user_id, file_data=file, file_name=file.name or "unknown", mime_type=file.content_type or "application/octet-stream", file_size=file.size)
        return ApiResponse.success(data=MediaAttachmentSerializer(attachment).data)
    except MediaUploadError as e:
        return ApiResponse.error(code=e.code, message=e.message)
    except Exception as e:
        logger.error(f"媒体上传异常: user_id={user_id}, error={e}", exc_info=True)
        return ApiResponse.error(message="上传失败，请稍后重试")


@api_view(["GET"])
def get_media(request: Request, uuid: str) -> Response:
    user_id = request.target_user_id
    attachment = async_to_sync(MediaService.get_attachment_any_user)(uuid)
    if not attachment:
        return ApiResponse.not_found(message="附件不存在")
    if attachment.user_id != user_id:
        return ApiResponse.forbidden(message="无权访问该附件")
    if attachment.is_expired:
        return ApiResponse.error(code="ATTACHMENT_EXPIRED", message="文件已过期", data=None, status_code=410)
    try:
        file_bytes = MediaService.get_media_file(attachment)
        return FileResponse(BytesIO(file_bytes), content_type=attachment.mime_type, filename=attachment.file_name)
    except MediaUploadError as e:
        return ApiResponse.error(code=e.code, message=e.message, status_code=410)
    except Exception as e:
        logger.error(f"获取媒体文件失败: uuid={uuid}, error={e}", exc_info=True)
        return ApiResponse.error(message="获取文件失败")


@api_view(["POST"])
def parse_document(request: Request) -> Response:
    user_id = request.user_id
    sz = DocumentParseRequestSerializer(data=request.data)
    if not sz.is_valid():
        return ApiResponse.validation_error(message=first_validation_error(sz))
    try:
        attachment_uuid = sz.validated_data["attachment_uuid"]
        pages = sz.validated_data.get("pages")

        # T012: 无 pages 参数时检查缓存快速返回
        if not pages:
            from apps.media.repositories import media_attachment_repo

            attachment = async_to_sync(media_attachment_repo.get_by_uuid)(attachment_uuid, user_id)
            if attachment:
                cached = async_to_sync(DocumentParseService.get_cached_result)(attachment)
                if cached:
                    max_len = getattr(settings, "DOC_PARSE_MAX_RESULT_LENGTH", 6000)
                    return ApiResponse.success(data={"cached": True, "content": cached[:max_len], "format": "markdown"})

        result = async_to_sync(DocumentParseService.parse_document)(user_id=user_id, attachment_uuid=attachment_uuid, pages=pages)
        return ApiResponse.success(data=result, status_code=202)
    except DocumentParseError as e:
        status_map = {"ATTACHMENT_NOT_FOUND": 404, "ATTACHMENT_ACCESS_DENIED": 403, "ATTACHMENT_EXPIRED": 410, "GATEWAY_NOT_CONFIGURED": 503, "GATEWAY_TIMEOUT": 504}
        return ApiResponse.error(code=e.code, message=e.message, data=e.details, status_code=status_map.get(e.code, 400))
    except Exception as e:
        logger.error(f"文档解析异常: user_id={user_id}, error={e}", exc_info=True)
        return ApiResponse.error(message="文档解析失败，请稍后重试")


@api_view(["GET"])
def get_parse_task_status(request: Request, task_id: str) -> Response:
    user_id = request.user_id
    try:
        async_to_sync(DocumentParseService.verify_task_ownership)(task_id, user_id)
        result = async_to_sync(DocumentParseService.poll_task_status)(task_id)
        return ApiResponse.success(data=result)
    except DocumentParseError as e:
        status_map = {"TASK_NOT_FOUND": 404, "TASK_ACCESS_DENIED": 403, "E6009": 410}
        return ApiResponse.error(code=e.code, message=e.message, data=e.details, status_code=status_map.get(e.code))
    except Exception as e:
        logger.error(f"查询解析任务异常: task_id={task_id}, error={e}", exc_info=True)
        return ApiResponse.error(message="查询任务状态失败")


@api_view(["GET"])
def get_parse_task_result(request: Request, task_id: str) -> Response:
    user_id = request.user_id
    format_param = request.query_params.get("format", "markdown")
    if format_param not in ("markdown", "json"):
        return ApiResponse.validation_error(message="format 参数仅支持 markdown 或 json")
    try:
        async_to_sync(DocumentParseService.verify_task_ownership)(task_id, user_id)
        result = async_to_sync(DocumentParseService.get_task_result)(task_id, format=format_param)
        return ApiResponse.success(data={"content": result, "format": format_param})
    except DocumentParseError as e:
        status_map = {"TASK_NOT_FOUND": 404, "TASK_ACCESS_DENIED": 403, "E6009": 410}
        return ApiResponse.error(code=e.code, message=e.message, data=e.details, status_code=status_map.get(e.code))
    except Exception as e:
        logger.error(f"获取解析结果异常: task_id={task_id}, error={e}", exc_info=True)
        return ApiResponse.error(message="获取解析结果失败")
