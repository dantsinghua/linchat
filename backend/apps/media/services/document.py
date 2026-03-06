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
                record_gateway_span(request_type=request_type, model=model, duration=duration, status_code=success_status, request_id=request_id)
                return response
            record_gateway_span(request_type=request_type, model=model, duration=duration, status_code=response.status_code, request_id=request_id, error=f"Gateway HTTP {response.status_code}")
            gw_err = parse_gateway_error(response)
            raise DocumentParseError(code=gw_err.code, message=gw_err.message, details=gw_err.details or None)
        except DocumentParseError:
            raise
        except httpx.TimeoutException:
            duration = time.monotonic() - start_time
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
        return response.json()

    @staticmethod
    async def poll_task_status(task_id: str) -> dict:
        """轮询任务状态 — GATEWAY_ERROR 自动重试（012-doc-parse-progress）"""
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
        model = getattr(settings, "DOC_PARSE_DEFAULT_MODEL", "minicpm-o")
        result = await DocumentParseService.create_parse_task(file_data=file_data, file_name=attachment.file_name, model=model, pages=pages)
        task_id = result.get("task_id", "")
        try:
            from core.redis import get_redis
            redis_client = await get_redis()
            await redis_client.set(f"doc_parse:{task_id}:owner", str(user_id), ex=7 * 24 * 3600)
        except Exception as e:
            logger.warning(f"写入文档解析所有权键失败: task_id={task_id}, error={e}")
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


    # --- 011-document-subagent-rag: 缓存读写方法 ---

    @staticmethod
    async def get_cached_result(attachment: "MediaAttachment") -> str | None:
        """获取缓存的解析结果 — DB parsed_content 优先，MinIO 降级"""
        if attachment.parsed_content:
            return attachment.parsed_content
        if attachment.parsed_content_path:
            try:
                from apps.common.storage.minio_service import minio_service
                data = minio_service.download_file(
                    bucket=settings.MINIO_BUCKET_MEDIA,
                    object_name=attachment.parsed_content_path,
                )
                content = data.decode("utf-8")
                logger.info("Doc cache fallback MinIO: attachment=%d, path=%s", attachment.attachment_id, attachment.parsed_content_path)
                return content
            except Exception as e:
                logger.warning("Doc cache MinIO fallback failed: attachment=%d, err=%s", attachment.attachment_id, e)
        return None

    @staticmethod
    async def save_parsed_result(attachment: "MediaAttachment", content: str) -> bool:
        """双写持久化 — MinIO 先写 → DB 原子更新 → 失败补偿"""
        from datetime import date as _date

        from apps.common.storage.minio_service import minio_service
        from apps.media.repositories import media_attachment_repo

        minio_path = f"parsed/{attachment.user_id}/{_date.today().isoformat()}/{attachment.attachment_uuid}.md"
        content_bytes = content.encode("utf-8")

        # Step 1: MinIO 上传
        try:
            minio_service.upload_bytes(
                bucket=settings.MINIO_BUCKET_MEDIA,
                object_name=minio_path,
                data=content_bytes,
                content_type="text/markdown; charset=utf-8",
            )
        except Exception as e:
            logger.error("Doc cache MinIO upload failed: attachment=%d, err=%s", attachment.attachment_id, e)
            return False

        # Step 2: DB 原子更新
        from django.utils import timezone as tz

        try:
            updated = await media_attachment_repo.update_parsed_cache(
                attachment_id=attachment.attachment_id,
                parsed_content=content,
                parsed_content_path=minio_path,
                parsed_at=tz.now(),
                parsed_content_size=len(content_bytes),
            )
            if updated == 0:
                logger.warning("Doc cache DB update returned 0 rows: attachment=%d", attachment.attachment_id)
        except Exception as e:
            # 补偿: DB 失败 → 删除 MinIO 文件
            logger.error("Doc cache DB update failed, compensating MinIO delete: attachment=%d, err=%s", attachment.attachment_id, e)
            minio_service.delete_file(bucket=settings.MINIO_BUCKET_MEDIA, object_name=minio_path)
            return False

        # Step 3: 异步分发 Embedding 生成任务
        try:
            from apps.media.tasks import generate_document_embeddings
            generate_document_embeddings.delay(attachment.attachment_id)
            logger.info("Doc cache saved + embedding dispatched: attachment=%d, size=%d", attachment.attachment_id, len(content_bytes))
        except Exception as e:
            logger.warning("Doc embedding dispatch failed (non-blocking): attachment=%d, err=%s", attachment.attachment_id, e)

        return True

    @staticmethod
    async def clear_parsed_cache(attachment: "MediaAttachment") -> None:
        """清除解析缓存 — MinIO 文件 + chunk embeddings + DB 字段"""
        from apps.common.storage.minio_service import minio_service
        from apps.media.repositories import doc_chunk_repo, media_attachment_repo

        # 删除 MinIO 备份（忽略 NotFound）
        if attachment.parsed_content_path:
            minio_service.delete_file(bucket=settings.MINIO_BUCKET_MEDIA, object_name=attachment.parsed_content_path)

        # 删除 chunk embeddings
        deleted = await doc_chunk_repo.delete_by_attachment_id(attachment.attachment_id)
        if deleted:
            logger.info("Doc cache clear chunks: attachment=%d, deleted=%d", attachment.attachment_id, deleted)

        # 清除 DB 字段
        await media_attachment_repo.clear_parsed_cache(attachment.attachment_id)
        logger.info("Doc cache cleared: attachment=%d", attachment.attachment_id)


    # --- 011-document-subagent-rag: 分块 + RAG 搜索 ---

    @staticmethod
    def chunk_document(
        content: str,
        chunk_size: int = 800,
        overlap: int = 100,
    ) -> list[str]:
        """按 Markdown 标题分段 → 段落拆分 → 合并小段 → 切分长段"""
        import re

        if not content or not content.strip():
            return []

        # Step 1: 按 Markdown 标题拆分
        sections = re.split(r"\n(?=#{1,6} )", content)
        sections = [s.strip() for s in sections if s.strip()]

        # Step 2: 段落拆分
        paragraphs: list[str] = []
        for section in sections:
            parts = section.split("\n\n")
            for p in parts:
                p = p.strip()
                if p:
                    paragraphs.append(p)

        if not paragraphs:
            return []

        # Step 3: 合并小段 (< chunk_size)
        merged: list[str] = []
        buf = ""
        for p in paragraphs:
            if buf and len(buf) + len(p) + 2 > chunk_size:
                merged.append(buf)
                buf = p
            else:
                buf = f"{buf}\n\n{p}" if buf else p
        if buf:
            merged.append(buf)

        # Step 4: 切分长段 (> chunk_size, with overlap)
        chunks: list[str] = []
        for segment in merged:
            if len(segment) <= chunk_size:
                chunks.append(segment)
            else:
                start = 0
                while start < len(segment):
                    end = start + chunk_size
                    chunks.append(segment[start:end])
                    start = end - overlap
                    if start < 0:
                        break

        return chunks

    @staticmethod
    async def search_documents_rag(
        user_id: int,
        query: str,
        mode: str = "hybrid",
        limit: int = 5,
    ) -> list[dict]:
        """文档 RAG 搜索 — keyword / semantic / hybrid 模式"""
        from apps.media.repositories import doc_chunk_repo, media_attachment_repo

        vector_results: list[tuple] = []
        keyword_results: list[tuple] = []

        # Keyword search
        if mode in ("keyword", "hybrid"):
            try:
                keyword_results = await doc_chunk_repo.keyword_search(user_id, query, limit=limit * 3)
            except Exception as e:
                logger.warning("Doc RAG keyword search failed: user=%d, err=%s", user_id, e)

        # Semantic search
        if mode in ("semantic", "hybrid"):
            try:
                from apps.memory.services import EmbeddingClient

                query_embedding = await EmbeddingClient.generate_embedding(query)
                vector_results = await doc_chunk_repo.vector_search(user_id, query_embedding, limit=limit * 3)
            except Exception as e:
                logger.warning("Doc RAG vector search failed, degrading to keyword: user=%d, err=%s", user_id, e)
                if mode == "semantic":
                    # 纯语义模式向量失败 → 降级为关键词
                    try:
                        keyword_results = await doc_chunk_repo.keyword_search(user_id, query, limit=limit * 3)
                    except Exception as e2:
                        logger.warning("Doc RAG keyword fallback also failed: user=%d, err=%s", user_id, e2)

        # Rerank (hybrid mode)
        if mode == "hybrid" and (vector_results or keyword_results):
            vector_weight = getattr(settings, "DOC_VECTOR_WEIGHT", 0.7)
            keyword_weight = getattr(settings, "DOC_KEYWORD_WEIGHT", 0.3)

            score_map: dict[tuple, dict] = {}  # (attachment_id, chunk_index) → {score, text}

            for att_id, chunk_idx, chunk_text, score in vector_results:
                key = (att_id, chunk_idx)
                if key not in score_map:
                    score_map[key] = {"text": chunk_text, "vector": score, "keyword": 0.0, "attachment_id": att_id}
                else:
                    score_map[key]["vector"] = max(score_map[key].get("vector", 0.0), score)

            for att_id, chunk_idx, chunk_text, score in keyword_results:
                key = (att_id, chunk_idx)
                if key not in score_map:
                    score_map[key] = {"text": chunk_text, "vector": 0.0, "keyword": score, "attachment_id": att_id}
                else:
                    score_map[key]["keyword"] = max(score_map[key].get("keyword", 0.0), score)

            ranked = sorted(
                score_map.values(),
                key=lambda x: x.get("vector", 0.0) * vector_weight + x.get("keyword", 0.0) * keyword_weight,
                reverse=True,
            )[:limit]
        elif vector_results:
            ranked = [{"text": t, "attachment_id": a, "vector": s, "keyword": 0.0} for a, _, t, s in vector_results[:limit]]
        elif keyword_results:
            ranked = [{"text": t, "attachment_id": a, "vector": 0.0, "keyword": s} for a, _, t, s in keyword_results[:limit]]
        else:
            ranked = []

        # Fallback: chunk 搜索无结果 → 降级到 parsed_content GIN 全文索引
        if not ranked:
            try:
                ft_results = await media_attachment_repo.fulltext_search_parsed_content(user_id, query, limit=limit)
                if ft_results:
                    from apps.media.models import MediaAttachment

                    for att_id, att_uuid, file_name, score in ft_results:
                        ranked.append({"text": f"[全文匹配] {file_name}", "attachment_id": att_id, "vector": 0.0, "keyword": score, "match_type": "fulltext"})
            except Exception as e:
                logger.warning("Doc RAG fulltext fallback failed: user=%d, err=%s", user_id, e)

        if not ranked:
            return []

        # Enrich with attachment metadata
        att_ids = list({r["attachment_id"] for r in ranked})
        from apps.media.models import MediaAttachment

        att_map: dict[int, MediaAttachment] = {}
        try:
            from asgiref.sync import sync_to_async

            @sync_to_async
            def _load_atts():
                return {a.attachment_id: a for a in MediaAttachment.objects.filter(attachment_id__in=att_ids)}

            att_map = await _load_atts()
        except Exception:
            pass

        results = []
        vector_weight = getattr(settings, "DOC_VECTOR_WEIGHT", 0.7)
        keyword_weight = getattr(settings, "DOC_KEYWORD_WEIGHT", 0.3)
        for r in ranked:
            att = att_map.get(r["attachment_id"])
            combined = r.get("vector", 0.0) * vector_weight + r.get("keyword", 0.0) * keyword_weight
            results.append({
                "file_name": att.file_name if att else "未知文档",
                "attachment_uuid": att.attachment_uuid if att else "",
                "created_at": att.created_at.isoformat() if att and att.created_at else "",
                "chunk_text": r["text"],
                "score": round(combined, 4),
                "match_type": r.get("match_type", mode),
            })

        return results


document_parse_service = DocumentParseService()
