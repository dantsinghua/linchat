"""
文档解析服务

封装 LLM Gateway 文档解析三步流程：
1. POST /v1/documents/parse — 创建解析任务
2. GET /v1/documents/tasks/{task_id} — 查询任务状态
3. GET /v1/documents/tasks/{task_id}/result — 获取解析结果

参考:
- document-parse-api.yaml
- specs/008-multimodal-minicpm/plan.md
"""

import asyncio
import logging
import time
from typing import Any, Optional

import httpx
from django.conf import settings

from apps.common.event_service import EventService, EventType
from apps.common.gateway_utils import (
    build_gateway_headers,
    parse_gateway_error,
    record_gateway_span,
)

logger = logging.getLogger(__name__)

# 支持的文件类型
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


class DocumentParseError(Exception):
    """文档解析错误"""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


class DocumentParseService:
    """文档解析服务

    封装 Gateway 文档解析 API 的三步异步流程，
    通过 EventService 推送解析进度到前端。
    """

    @staticmethod
    def _get_gateway_url() -> str:
        """获取 Gateway 基础地址"""
        url = getattr(settings, "LLM_GATEWAY_URL", "")
        if not url:
            raise DocumentParseError(
                code="GATEWAY_NOT_CONFIGURED",
                message="未配置 LLM_GATEWAY_URL，无法使用文档解析功能",
            )
        return url

    @staticmethod
    async def verify_task_ownership(task_id: str, user_id: int) -> None:
        """校验任务所有权

        从 Redis 读取 doc_parse:{task_id}:owner 键校验所有权。
        键不存在时返回 404（任务不存在或已过期），
        所有者不匹配时返回 403。

        Args:
            task_id: 任务 ID
            user_id: 当前用户 ID

        Raises:
            DocumentParseError: 无权访问或任务不存在
        """
        from core.redis import get_redis

        client = await get_redis()
        owner = await client.get(f"doc_parse:{task_id}:owner")

        if owner is None:
            raise DocumentParseError(
                code="TASK_NOT_FOUND",
                message="任务不存在或已过期",
            )

        owner_str = owner.decode("utf-8") if isinstance(owner, bytes) else str(owner)
        if owner_str != str(user_id):
            raise DocumentParseError(
                code="TASK_ACCESS_DENIED",
                message="无权访问该解析任务",
            )

    @staticmethod
    async def _gateway_request(
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float,
        success_status: int,
        request_type: str,
        model: str = "",
        **kwargs: Any,
    ) -> httpx.Response:
        """通用 Gateway 请求（统一异常处理和 span 记录）

        Args:
            method: HTTP 方法 (get/post)
            url: 请求 URL
            headers: 请求头
            timeout: 超时秒数
            success_status: 期望的成功状态码
            request_type: 请求类型标识（Langfuse span 用）
            model: 模型名称
            **kwargs: 传给 httpx 的额外参数

        Returns:
            httpx.Response（仅当状态码 == success_status）

        Raises:
            DocumentParseError: 任何失败场景
        """
        request_id = headers.get("X-Request-ID", "")
        start_time = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await getattr(client, method)(
                    url, headers=headers, **kwargs
                )

            duration = time.monotonic() - start_time

            if response.status_code == success_status:
                record_gateway_span(
                    request_type=request_type,
                    model=model,
                    duration=duration,
                    status_code=success_status,
                    request_id=request_id,
                )
                return response

            record_gateway_span(
                request_type=request_type,
                model=model,
                duration=duration,
                status_code=response.status_code,
                request_id=request_id,
                error=f"Gateway HTTP {response.status_code}",
            )
            gw_err = parse_gateway_error(response)
            raise DocumentParseError(
                code=gw_err.code,
                message=gw_err.message,
                details=gw_err.details or None,
            )

        except DocumentParseError:
            raise
        except httpx.TimeoutException:
            duration = time.monotonic() - start_time
            record_gateway_span(
                request_type=request_type,
                model=model,
                duration=duration,
                status_code=504,
                request_id=request_id,
                error="timeout",
            )
            raise DocumentParseError(
                code="GATEWAY_TIMEOUT",
                message="文档解析请求超时",
            )
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error(f"Gateway 请求失败 ({request_type}): {e}")
            record_gateway_span(
                request_type=request_type,
                model=model,
                duration=duration,
                status_code=503,
                request_id=request_id,
                error=str(e),
            )
            raise DocumentParseError(
                code="GATEWAY_ERROR",
                message=f"网关请求失败: {e}",
            )

    @staticmethod
    async def create_parse_task(
        file_data: bytes,
        file_name: str,
        model: str,
        pages: Optional[str] = None,
    ) -> dict[str, Any]:
        """步骤1: 创建解析任务

        Args:
            file_data: 文件二进制内容
            file_name: 文件名
            model: VL 模型 ID（必填）
            pages: 页码范围（可选）

        Returns:
            Gateway 响应数据（含 task_id, status 等）

        Raises:
            DocumentParseError: Gateway 返回错误
        """
        gateway_url = DocumentParseService._get_gateway_url()
        headers = build_gateway_headers()
        timeout = getattr(settings, "LLM_GATEWAY_DOC_PARSE_CREATE_TIMEOUT", 30)

        files = {"file": (file_name, file_data)}
        data: dict[str, str] = {"model": model}
        if pages:
            data["pages"] = pages

        response = await DocumentParseService._gateway_request(
            method="post",
            url=f"{gateway_url}/v1/documents/parse",
            headers=headers,
            timeout=float(timeout),
            success_status=202,
            request_type="document_parse",
            model=model,
            files=files,
            data=data,
        )
        return response.json()

    @staticmethod
    async def poll_task_status(task_id: str) -> dict[str, Any]:
        """步骤2: 查询任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态数据

        Raises:
            DocumentParseError: 查询失败
        """
        gateway_url = DocumentParseService._get_gateway_url()
        headers = build_gateway_headers()
        poll_timeout = getattr(settings, "LLM_GATEWAY_POLL_TIMEOUT", 30)

        response = await DocumentParseService._gateway_request(
            method="get",
            url=f"{gateway_url}/v1/documents/tasks/{task_id}",
            headers=headers,
            timeout=float(poll_timeout),
            success_status=200,
            request_type="document_parse_poll",
        )
        return response.json()

    @staticmethod
    async def get_task_result(
        task_id: str, format: str = "markdown"
    ) -> Any:
        """步骤3: 获取解析结果

        Args:
            task_id: 任务 ID
            format: 结果格式 (markdown/json)

        Returns:
            - format=markdown: 返回 Markdown 文本字符串
            - format=json: 返回 JSON 字典

        Raises:
            DocumentParseError: 获取失败
        """
        gateway_url = DocumentParseService._get_gateway_url()
        headers = build_gateway_headers()
        result_timeout = getattr(settings, "LLM_GATEWAY_DOC_PARSE_RESULT_TIMEOUT", 30)

        response = await DocumentParseService._gateway_request(
            method="get",
            url=f"{gateway_url}/v1/documents/tasks/{task_id}/result",
            headers=headers,
            timeout=float(result_timeout),
            success_status=200,
            request_type="document_parse_result",
            params={"format": format},
        )

        content_type = response.headers.get("content-type", "")
        if "text/markdown" in content_type or format == "markdown":
            return response.text
        return response.json()

    @staticmethod
    async def parse_document(
        user_id: int,
        attachment_uuid: str,
        pages: Optional[str] = None,
        skip_background_poll: bool = False,
    ) -> dict[str, Any]:
        """主方法: 校验附件 → 下载 MinIO → 创建 Gateway 任务 → 后台轮询

        从 MediaAttachment 获取元数据，使用 settings.DOC_PARSE_DEFAULT_MODEL
        作为 model 参数（默认 minicpm-o）。

        Args:
            user_id: 用户 ID（用于事件推送和所有权校验）
            attachment_uuid: 已上传到 MinIO 的文档附件 UUID
            pages: 页码范围（可选，语法如 "1,3-5,8"）
            skip_background_poll: 跳过后台轮询（Agent 内部调用时为 True，
                避免与工具内同步轮询重复）

        Returns:
            {"task_id": "...", "status": "pending", ...}

        Raises:
            DocumentParseError: 附件不存在/无权访问/类型错误/Gateway 错误
        """
        from apps.chat.repositories import media_attachment_repo
        from apps.chat.services.minio_service import minio_service

        # 1. 查询附件元数据并校验所有权
        attachment = await media_attachment_repo.get_by_uuid_any_user(
            attachment_uuid
        )
        if not attachment:
            raise DocumentParseError(
                code="ATTACHMENT_NOT_FOUND", message="附件不存在"
            )
        if attachment.user_id != user_id:
            raise DocumentParseError(
                code="ATTACHMENT_ACCESS_DENIED", message="无权访问该附件"
            )
        if attachment.media_type != "document":
            raise DocumentParseError(
                code="INVALID_DOCUMENT_TYPE",
                message="仅支持 PDF/DOCX 文档",
                details={
                    "supported_types": [
                        "application/pdf",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    ]
                },
            )
        if attachment.is_expired:
            raise DocumentParseError(
                code="ATTACHMENT_EXPIRED", message="文件已过期"
            )

        # 2. 从 MinIO 下载文件
        file_data = minio_service.download_file(
            bucket=settings.MINIO_BUCKET_MEDIA,
            object_name=attachment.storage_path,
        )

        # 3. 创建 Gateway 解析任务
        model = getattr(settings, "DOC_PARSE_DEFAULT_MODEL", "minicpm-o")
        result = await DocumentParseService.create_parse_task(
            file_data=file_data,
            file_name=attachment.file_name,
            model=model,
            pages=pages,
        )

        task_id = result.get("task_id", "")

        # 4. 写入 Redis 所有权键（用于后续状态/结果查询的所有权校验）
        try:
            from core.redis import get_redis

            redis_client = await get_redis()
            await redis_client.set(
                f"doc_parse:{task_id}:owner",
                str(user_id),
                ex=7 * 24 * 3600,  # 7 天 TTL
            )
        except Exception as e:
            logger.warning(f"写入文档解析所有权键失败: task_id={task_id}, error={e}")

        # 5. 启动后台轮询协程（Agent 内部调用时跳过，由工具内同步轮询）
        if not skip_background_poll:
            asyncio.create_task(
                DocumentParseService._poll_and_notify(user_id, task_id)
            )

        return result

    @staticmethod
    async def _poll_and_notify(user_id: int, task_id: str) -> None:
        """后台轮询任务状态并推送进度事件

        Args:
            user_id: 用户 ID
            task_id: 任务 ID
        """
        poll_interval = getattr(settings, "DOC_PARSE_POLL_INTERVAL", 3)
        max_wait = getattr(settings, "DOC_PARSE_POLL_MAX_WAIT", 900)
        elapsed = 0
        last_status = ""

        try:
            while elapsed < max_wait:
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

                try:
                    status_data = await DocumentParseService.poll_task_status(task_id)
                except DocumentParseError as e:
                    logger.warning(f"轮询任务状态失败: task_id={task_id}, error={e.message}")
                    continue

                current_status = status_data.get("status", "")
                progress = status_data.get("progress", {})

                # 状态变化时推送事件
                if current_status != last_status or current_status == "processing":
                    await EventService.publish_event(
                        user_id=user_id,
                        event_type=EventType.DOC_PARSE_PROGRESS.value,
                        data={
                            "type": EventType.DOC_PARSE_PROGRESS.value,
                            "task_id": task_id,
                            "status": current_status,
                            "progress": progress,
                            "error_message": status_data.get("error_message"),
                        },
                    )
                    last_status = current_status

                # 终态退出
                if current_status in ("completed", "failed"):
                    logger.info(
                        f"文档解析{current_status}: task_id={task_id}, "
                        f"user_id={user_id}"
                    )
                    return

            # 超时
            logger.warning(
                f"文档解析轮询超时: task_id={task_id}, "
                f"user_id={user_id}, elapsed={elapsed}s"
            )
            await EventService.publish_event(
                user_id=user_id,
                event_type=EventType.DOC_PARSE_PROGRESS.value,
                data={
                    "type": EventType.DOC_PARSE_PROGRESS.value,
                    "task_id": task_id,
                    "status": "failed",
                    "progress": {},
                    "error_message": f"轮询超时（{max_wait}秒）",
                },
            )

        except Exception as e:
            logger.error(
                f"文档解析轮询异常: task_id={task_id}, "
                f"user_id={user_id}, error={e}"
            )
            await EventService.publish_event(
                user_id=user_id,
                event_type=EventType.DOC_PARSE_PROGRESS.value,
                data={
                    "type": EventType.DOC_PARSE_PROGRESS.value,
                    "task_id": task_id,
                    "status": "failed",
                    "progress": {},
                    "error_message": f"轮询异常: {e}",
                },
            )



# 单例实例
document_parse_service = DocumentParseService()
