import logging

from django.conf import settings
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.context.loader import render
from apps.graph.subagents.base import run_subagent
from apps.graph.subagents.document_parse_helpers import build_truncated_result, poll_parse_task
from apps.graph.tools.user_id import get_user_id as _get_user_id

logger = logging.getLogger(__name__)


@tool
async def doc_list(
    task: str, config: RunnableConfig,
    file_name: str = "", created_after: str = "", created_before: str = "",
    order_by: str = "newest", limit: int = 10,
) -> str:
    """列出用户的文档附件。支持文件名分词搜索、时间范围筛选和排序。"""
    from datetime import datetime
    from apps.media.repositories import media_attachment_repo

    user_id = _get_user_id(config)
    limit = min(limit, 20)
    _parse = lambda s: datetime.fromisoformat(s) if s else None  # noqa: E731
    try:
        dt_after = _parse(created_after)
    except ValueError:
        dt_after = None
    try:
        dt_before = _parse(created_before)
    except ValueError:
        dt_before = None

    docs = await media_attachment_repo.search_documents(
        user_id=user_id, file_name=file_name or None,
        created_after=dt_after, created_before=dt_before,
        order_by=order_by, limit=limit,
    )
    if not docs:
        return "没有找到符合条件的文档"

    lines = [f"找到 {len(docs)} 个文档："]
    for i, doc in enumerate(docs, 1):
        size_mb = doc.file_size / (1024 * 1024) if doc.file_size else 0
        created = doc.created_at.strftime("%Y-%m-%d %H:%M") if doc.created_at else "未知"
        parsed_icon = "✅ 已解析" if doc.parsed_content else "❌ 未解析"
        expired_icon = "⚠️ 原始文件已过期" if doc.is_expired else "📎 原始文件可用"
        lines.append(f"{i}. [{doc.attachment_uuid[:6]}] {doc.file_name} | {size_mb:.1f}MB | {created} | {parsed_icon} | {expired_icon}")
    return "\n".join(lines)


@tool
async def doc_read(attachment_uuid: str, config: RunnableConfig, max_length: int = 8000) -> str:
    """读取指定文档的解析结果（Markdown 全文）。"""
    from apps.media.repositories import media_attachment_repo
    from apps.media.services.document_cache import get_cached_result

    user_id = _get_user_id(config)
    attachment = await media_attachment_repo.get_by_uuid(attachment_uuid, user_id)
    if not attachment:
        return f"文档不存在或无权访问: {attachment_uuid}"
    cached = await get_cached_result(attachment)
    if not cached:
        return f"该文档尚未解析，请先使用 document_parse 工具解析: {attachment.file_name}"
    if len(cached) > max_length:
        return cached[:max_length] + f"\n\n⚠️ 内容已截断（完整共 {len(cached)} 字符）。如需查找特定内容，请使用 doc_search 按关键词检索，不要反复调用 doc_read。"
    return cached


@tool
async def doc_search(query: str, config: RunnableConfig, mode: str = "hybrid", limit: int = 5) -> str:
    """在用户所有已解析文档中检索内容。支持关键词、语义和混合检索。"""
    from apps.media.services.document_rag import search_documents_rag

    user_id = _get_user_id(config)
    if mode not in ("keyword", "semantic", "hybrid"):
        mode = "hybrid"
    limit = min(limit, 20)
    try:
        results = await search_documents_rag(user_id=user_id, query=query, mode=mode, limit=limit)
    except Exception as e:
        logger.error("Doc search fail: user=%d, err=%s", user_id, e)
        return f"文档搜索异常: {e}"
    if not results:
        return "未找到匹配内容"
    lines = [f"搜索到 {len(results)} 个相关片段：\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. 📄 {r.get('file_name', '未知')} [{r.get('attachment_uuid', '')[:6]}] (相关度: {r.get('score', 0):.2f})\n   > {r.get('chunk_text', '')[:200]}")
    return "\n\n".join(lines)


@tool
async def document_parse(task: str, config: RunnableConfig, force: bool = False) -> str:
    """解析用户上传的 PDF/DOCX 文档。已解析过的文档自动返回缓存结果。设置 force=True 可强制重新解析。"""
    import asyncio
    from apps.graph.services import GPULockTimeout, acquire_gpu_lock
    from apps.media.repositories import media_attachment_repo
    from apps.media.services.document import DocumentParseError, DocumentParseService
    from apps.media.services.document_cache import clear_parsed_cache, get_cached_result, save_parsed_result

    user_id = _get_user_id(config)
    cfg = config.get("configurable", {})
    uuids = cfg.get("attachment_uuids", [])
    req_id = cfg.get("request_id", "unknown")
    if not uuids:
        return "当前没有用户上传的附件"
    attachments = await media_attachment_repo.get_by_uuids(uuids, user_id)
    if not attachments:
        return "附件加载失败或已过期"
    docs = [a for a in attachments if a.media_type == "document"]
    if not docs:
        return "没有需要解析的文档附件（仅支持 PDF/DOCX）"

    max_len = getattr(settings, "DOC_PARSE_MAX_RESULT_LENGTH", 6000)
    results = []
    for doc in docs:
        try:
            if force:
                await clear_parsed_cache(doc)
            cached = await get_cached_result(doc)
            if cached:
                logger.info("Doc parse cache hit: attachment=%d, size=%d", doc.attachment_id, len(cached))
                results.append(build_truncated_result(doc.file_name, cached, max_len, label="缓存") if len(cached) > max_len else f"📄 {doc.file_name}（缓存）:\n{cached}")
                continue
            if doc.is_expired:
                results.append(f"📄 {doc.file_name}: 原始文件已过期，无法解析")
                continue
            try:
                async with acquire_gpu_lock(req_id):
                    parse_result = await DocumentParseService.parse_document(user_id=user_id, attachment_uuid=doc.attachment_uuid, skip_background_poll=True)
                    task_id = parse_result.get("task_id", "")
                    if not task_id:
                        results.append(f"📄 {doc.file_name}: 解析任务创建失败")
                        continue
                    logger.info("Doc parse task started: attachment=%d, task_id=%s, file=%s", doc.attachment_id, task_id, doc.file_name)
                    status, result_text, _ = await poll_parse_task(task_id, doc, user_id, max_len)
                    if result_text:
                        results.append(result_text)
                        if status != "completed":
                            continue
                    if status != "completed":
                        continue
                    md_content = await DocumentParseService.get_task_result(task_id, format="markdown")
                    logger.info("Doc parse result fetched: task=%s, size=%d", task_id, len(md_content) if md_content else 0)
                    if not md_content:
                        results.append(f"📄 {doc.file_name}: 解析结果为空")
                        continue
                    await save_parsed_result(doc, md_content)
                    results.append(build_truncated_result(doc.file_name, md_content, max_len) if len(md_content) > max_len else f"📄 {doc.file_name}:\n{md_content}")
            except GPULockTimeout:
                results.append(f"📄 {doc.file_name}: GPU 资源繁忙，请稍后重试")
        except DocumentParseError as e:
            results.append(f"📄 {doc.file_name}: {e.message}")
        except Exception as e:
            logger.error("Doc parse fail: attachment=%d, err=%s", doc.attachment_id, e)
            results.append(f"📄 {doc.file_name}: 解析异常 — {e}")
    return "\n\n---\n\n".join(results) if results else "没有成功解析的文档"


@tool
async def document_subagent(task: str, config: RunnableConfig) -> str:
    """查询和管理用户的文档。查看文档列表、读取解析内容、搜索文档关键词、解析新文档时使用。"""
    cfg = config.get("configurable", {})
    uuids = cfg.get("attachment_uuids", [])
    if uuids:
        task = f"{task}\n\n[系统：用户已上传 {len(uuids)} 个附件，请根据需要调用 document_parse 工具解析文档，附件会自动从上下文加载。]"
    return await run_subagent(
        task, config, tools=[doc_list, doc_read, doc_search, document_parse],
        prompt=render("document_subagent.j2"), name="document_subagent",
        timeout=getattr(settings, "DOCUMENT_SUBAGENT_TIMEOUT", 1200), recursion_limit=40,
    )
