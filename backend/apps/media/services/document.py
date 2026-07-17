import asyncio
import logging
import time
from typing import Any, Optional

import httpx
from django.conf import settings

from apps.common.event_service import EventService, EventType
from apps.common.gateway_utils import build_gateway_headers, parse_gateway_error, record_gateway_span

logger = logging.getLogger(__name__)


class DocumentParseError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        self.code = code; self.message = message; self.details = details
        super().__init__(message)


class DocumentParseService:
    @staticmethod
    def _get_gateway_url() -> str:
        url = getattr(settings, "LLM_GATEWAY_URL", "")
        if not url: raise DocumentParseError(code="GATEWAY_NOT_CONFIGURED", message="未配置 LLM_GATEWAY_URL")
        return url

    @staticmethod
    async def verify_task_ownership(task_id: str, user_id: int) -> None:
        from core.redis import get_redis
        client = await get_redis()
        owner = await client.get(f"doc_parse:{task_id}:owner")
        if owner is None: raise DocumentParseError(code="TASK_NOT_FOUND", message="任务不存在或已过期")
        owner_str = owner.decode("utf-8") if isinstance(owner, bytes) else str(owner)
        if owner_str != str(user_id):
            raise DocumentParseError(code="TASK_ACCESS_DENIED", message="无权访问该解析任务")

    @staticmethod
    async def _gateway_request(method: str, url: str, headers: dict, timeout: float,
                               success_status: int, request_type: str, model: str = "", **kwargs: Any) -> httpx.Response:
        request_id = headers.get("X-Request-ID", ""); start_time = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await getattr(client, method)(url, headers=headers, **kwargs)
            duration = time.monotonic() - start_time
            if response.status_code == success_status:
                logger.info(
                    "Gateway %s %s -> %d (%.1fs) req=%s",
                    method.upper(), url.split("/v1/")[-1], success_status, duration, request_id,
                )
                record_gateway_span(request_type=request_type, model=model, duration=duration, status_code=success_status, request_id=request_id)
                return response
            logger.warning(
                "Gateway %s %s -> %d (%.1fs) req=%s",
                method.upper(), url.split("/v1/")[-1], response.status_code, duration, request_id,
            )
            record_gateway_span(request_type=request_type, model=model, duration=duration, status_code=response.status_code, request_id=request_id, error=f"Gateway HTTP {response.status_code}")
            gw_err = parse_gateway_error(response)
            raise DocumentParseError(code=gw_err.code, message=gw_err.message, details=gw_err.details or None)
        except DocumentParseError:
            raise
        except httpx.TimeoutException:
            duration = time.monotonic() - start_time
            logger.warning(
                "Gateway %s %s -> TIMEOUT (%.1fs) req=%s",
                method.upper(), url.split("/v1/")[-1], duration, request_id,
            )
            record_gateway_span(request_type=request_type, model=model, duration=duration, status_code=504, request_id=request_id, error="timeout")
            raise DocumentParseError(code="GATEWAY_TIMEOUT", message="文档解析请求超时")
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error(f"Gateway 请求失败 ({request_type}): {e}")
            record_gateway_span(request_type=request_type, model=model, duration=duration, status_code=503, request_id=request_id, error=str(e))
            raise DocumentParseError(code="GATEWAY_ERROR", message=f"网关请求失败: {e}")

    @staticmethod
    async def create_parse_task(file_data: bytes, file_name: str, model: str, pages: Optional[str] = None) -> dict:
        gateway_url = DocumentParseService._get_gateway_url()
        headers = build_gateway_headers()
        timeout = getattr(settings, "LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT", 30)
        files = {"file": (file_name, file_data)}
        data: dict[str, str] = {"model": model}
        if pages: data["pages"] = pages
        response = await DocumentParseService._gateway_request(method="post", url=f"{gateway_url}/v1/documents/parse", headers=headers,
            timeout=float(timeout), success_status=202, request_type="document_parse", model=model, files=files, data=data)
        result = response.json()
        logger.info(
            "Doc parse task created: task_id=%s, file=%s, size=%d",
            result.get("task_id", ""), file_name, len(file_data),
        )
        return result

    @staticmethod
    async def poll_task_status(task_id: str) -> dict:
        gateway_url = DocumentParseService._get_gateway_url()
        headers = build_gateway_headers()
        timeout = getattr(settings, "LLM_GATEWAY_POLL_TIMEOUT", 30)
        max_retries = 3
        retry_interval = 2
        for attempt in range(max_retries + 1):
            try:
                response = await DocumentParseService._gateway_request(
                    method="get",
                    url=f"{gateway_url}/v1/documents/tasks/{task_id}",
                    headers=headers,
                    timeout=float(timeout),
                    success_status=200,
                    request_type="document_parse_poll",
                )
                return response.json()
            except DocumentParseError as e:
                if e.code == "GATEWAY_ERROR" and attempt < max_retries:
                    logger.warning(
                        "Gateway 轮询网络重试: task_id=%s, attempt=%d/%d, err=%s",
                        task_id, attempt + 1, max_retries, e.message,
                    )
                    await asyncio.sleep(retry_interval)
                    continue
                raise

    @staticmethod
    async def get_task_result(task_id: str, format: str = "markdown") -> Any:
        gateway_url = DocumentParseService._get_gateway_url()
        headers = build_gateway_headers()
        timeout = getattr(settings, "LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT", 30)
        response = await DocumentParseService._gateway_request(method="get", url=f"{gateway_url}/v1/documents/tasks/{task_id}/result", headers=headers,
            timeout=float(timeout), success_status=200, request_type="document_parse_result", params={"format": format})
        content_type = response.headers.get("content-type", "")
        if "text/markdown" in content_type or format == "markdown": return response.text
        return response.json()

    @staticmethod
    async def parse_document(user_id: int, attachment_uuid: str, pages: Optional[str] = None, skip_background_poll: bool = False) -> dict:
        from apps.media.repositories import media_attachment_repo
        from apps.common.storage.minio_service import minio_service
        attachment = await media_attachment_repo.get_by_uuid_any_user(attachment_uuid)
        if not attachment: raise DocumentParseError(code="ATTACHMENT_NOT_FOUND", message="附件不存在")
        if attachment.user_id != user_id: raise DocumentParseError(code="ATTACHMENT_ACCESS_DENIED", message="无权访问该附件")
        if attachment.media_type != "document":
            raise DocumentParseError(code="INVALID_DOCUMENT_TYPE", message="仅支持 PDF/DOCX 文档",
                                     details={"supported_types": ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]})
        if attachment.is_expired: raise DocumentParseError(code="ATTACHMENT_EXPIRED", message="文件已过期")
        file_data = minio_service.download_file(bucket=settings.MINIO_BUCKET_MEDIA, object_name=attachment.storage_path)
        logger.info(
            "Doc parse start: user=%d, attachment=%s, file=%s, size=%d",
            user_id, attachment_uuid, attachment.file_name, len(file_data),
        )
        model = getattr(settings, "DOC_PARSE_DEFAULT_MODEL", "qwen3.5-9b")
        result = await DocumentParseService.create_parse_task(file_data=file_data, file_name=attachment.file_name, model=model, pages=pages)
        task_id = result.get("task_id", "")
        try:
            from core.redis import get_redis
            redis_client = await get_redis()
            await redis_client.set(f"doc_parse:{task_id}:owner", str(user_id), ex=7 * 24 * 3600)
        except Exception as e:
            logger.warning(f"写入文档解析所有权键失败: task_id={task_id}, error={e}", exc_info=True)
        if not skip_background_poll:
            asyncio.create_task(DocumentParseService._poll_and_notify(user_id, task_id))
        return result

    @staticmethod
    async def _poll_and_notify(user_id: int, task_id: str) -> None:
        poll_interval = getattr(settings, "DOC_PARSE_POLL_INTERVAL", 3)
        max_wait = getattr(settings, "DOC_PARSE_POLL_MAX_WAIT", 900)
        elapsed, last_status = 0, ""
        evt = EventType.DOC_PARSE_PROGRESS.value
        try:
            while elapsed < max_wait:
                await asyncio.sleep(poll_interval); elapsed += poll_interval
                try:
                    status_data = await DocumentParseService.poll_task_status(task_id)
                except DocumentParseError as e:
                    logger.warning(f"轮询任务状态失败: task_id={task_id}, error={e.message}"); continue
                current_status = status_data.get("status", "")
                if current_status != last_status or current_status == "processing":
                    await EventService.publish_event(user_id=user_id, event_type=evt,
                        data={"type": evt, "task_id": task_id, "status": current_status,
                              "progress": status_data.get("progress", {}), "error_message": status_data.get("error_message")})
                    last_status = current_status
                if current_status in ("completed", "failed"): return
            await EventService.publish_event(user_id=user_id, event_type=evt,
                data={"type": evt, "task_id": task_id, "status": "failed", "progress": {}, "error_message": f"轮询超时（{max_wait}秒）"})
        except Exception as e:
            logger.error(f"文档解析轮询异常: task_id={task_id}, error={e}")
            await EventService.publish_event(user_id=user_id, event_type=evt,
                data={"type": evt, "task_id": task_id, "status": "failed", "progress": {}, "error_message": f"轮询异常: {e}"})


document_parse_service = DocumentParseService()
