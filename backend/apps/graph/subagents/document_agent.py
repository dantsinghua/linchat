"""文档 SubAgent — 文档列表查询、解析内容读取、RAG 检索、文档解析（缓存复用）

011-document-subagent-rag: 从 multimodal_subagent 拆分，专注文档管理和检索。
"""

import logging

from django.conf import settings
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.common.event_service import EventService, EventType
from apps.context.loader import render
from apps.graph.subagents.base import _get_user_id, run_subagent

logger = logging.getLogger(__name__)


@tool
async def doc_list(
    task: str,
    config: RunnableConfig,
    file_name: str = "",
    created_after: str = "",
    created_before: str = "",
    order_by: str = "newest",
    limit: int = 10,
) -> str:
    """列出用户的文档附件。支持文件名分词搜索、时间范围筛选和排序。"""
    from datetime import datetime

    from apps.media.repositories import media_attachment_repo

    user_id = _get_user_id(config)
    limit = min(limit, 20)

    # 解析时间参数
    dt_after = None
    dt_before = None
    if created_after:
        try:
            dt_after = datetime.fromisoformat(created_after)
        except ValueError:
            pass
    if created_before:
        try:
            dt_before = datetime.fromisoformat(created_before)
        except ValueError:
            pass

    docs = await media_attachment_repo.search_documents(
        user_id=user_id,
        file_name=file_name or None,
        created_after=dt_after,
        created_before=dt_before,
        order_by=order_by,
        limit=limit,
    )

    if not docs:
        return "没有找到符合条件的文档"

    lines = [f"找到 {len(docs)} 个文档："]
    for i, doc in enumerate(docs, 1):
        size_mb = doc.file_size / (1024 * 1024) if doc.file_size else 0
        created = doc.created_at.strftime("%Y-%m-%d %H:%M") if doc.created_at else "未知"
        parsed_icon = "✅ 已解析" if doc.parsed_content else "❌ 未解析"
        expired_icon = "⚠️ 原始文件已过期" if doc.is_expired else "📎 原始文件可用"
        short_uuid = doc.attachment_uuid[:6]
        lines.append(f"{i}. [{short_uuid}] {doc.file_name} | {size_mb:.1f}MB | {created} | {parsed_icon} | {expired_icon}")

    return "\n".join(lines)


@tool
async def doc_read(
    attachment_uuid: str,
    config: RunnableConfig,
    max_length: int = 8000,
) -> str:
    """读取指定文档的解析结果（Markdown 全文）。"""
    from apps.media.repositories import media_attachment_repo
    from apps.media.services.document import DocumentParseService

    user_id = _get_user_id(config)
    attachment = await media_attachment_repo.get_by_uuid(attachment_uuid, user_id)
    if not attachment:
        return f"文档不存在或无权访问: {attachment_uuid}"

    cached = await DocumentParseService.get_cached_result(attachment)
    if not cached:
        return f"该文档尚未解析，请先使用 document_parse 工具解析: {attachment.file_name}"

    if len(cached) > max_length:
        return cached[:max_length] + f"\n\n[内容已截断，完整内容共 {len(cached)} 字符]"
    return cached


@tool
async def doc_search(
    query: str,
    config: RunnableConfig,
    mode: str = "hybrid",
    limit: int = 5,
) -> str:
    """在用户所有已解析文档中检索内容。支持关键词、语义和混合检索。"""
    from apps.media.services.document import DocumentParseService

    user_id = _get_user_id(config)
    if mode not in ("keyword", "semantic", "hybrid"):
        mode = "hybrid"
    limit = min(limit, 20)

    try:
        results = await DocumentParseService.search_documents_rag(
            user_id=user_id, query=query, mode=mode, limit=limit,
        )
    except Exception as e:
        logger.error("Doc search fail: user=%d, err=%s", user_id, e)
        return f"文档搜索异常: {e}"

    if not results:
        return "未找到匹配内容"

    lines = [f"搜索到 {len(results)} 个相关片段：\n"]
    for i, r in enumerate(results, 1):
        short_uuid = r.get("attachment_uuid", "")[:6]
        score = r.get("score", 0)
        preview = r.get("chunk_text", "")[:200]
        lines.append(f"{i}. 📄 {r.get('file_name', '未知')} [{short_uuid}] (相关度: {score:.2f})\n   > {preview}")

    return "\n\n".join(lines)


@tool
async def document_parse(task: str, config: RunnableConfig, force: bool = False) -> str:
    """解析用户上传的 PDF/DOCX 文档。已解析过的文档自动返回缓存结果。设置 force=True 可强制重新解析。"""
    import asyncio

    from apps.graph.services import GPULockTimeout, acquire_gpu_lock
    from apps.media.repositories import media_attachment_repo
    from apps.media.services.document import DocumentParseError, DocumentParseService

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
            # 1. force → 清除旧缓存
            if force:
                await DocumentParseService.clear_parsed_cache(doc)
                logger.info("Doc parse force clear: attachment=%d", doc.attachment_id)

            # 2. 检查缓存
            cached = await DocumentParseService.get_cached_result(doc)
            if cached:
                logger.info("Doc parse cache hit: attachment=%d, size=%d", doc.attachment_id, len(cached))
                content = cached[:max_len]
                if len(cached) > max_len:
                    content += f"\n\n[内容已截断，完整内容共 {len(cached)} 字符]"
                results.append(f"📄 {doc.file_name}（缓存）:\n{content}")
                continue

            # 3. 原始文件已过期 → 无法解析
            if doc.is_expired:
                results.append(f"📄 {doc.file_name}: 原始文件已过期，无法解析")
                continue

            # 4. 缓存未命中 → GPU 锁 → Gateway 解析
            try:
                async with acquire_gpu_lock(req_id):
                    parse_result = await DocumentParseService.parse_document(
                        user_id=user_id,
                        attachment_uuid=doc.attachment_uuid,
                        skip_background_poll=True,
                    )
                    task_id = parse_result.get("task_id", "")
                    if not task_id:
                        results.append(f"📄 {doc.file_name}: 解析任务创建失败")
                        continue

                    # 轮询直到完成（012-doc-parse-progress: SSE 进度推送）
                    poll_interval = getattr(settings, "DOC_PARSE_POLL_INTERVAL", 3)
                    max_wait = getattr(settings, "DOC_PARSE_POLL_MAX_WAIT", 900)
                    elapsed = 0
                    final_status = ""
                    evt = EventType.DOC_PARSE_PROGRESS.value

                    # SSE: pending
                    await EventService.publish_event(user_id=user_id, event_type=evt,
                        data={"type": evt, "task_id": task_id, "status": "pending",
                              "progress": {"current": 0, "total": 0}, "file_name": doc.file_name,
                              "suggestion": None, "error_message": None})

                    while elapsed < max_wait:
                        await asyncio.sleep(poll_interval)
                        elapsed += poll_interval
                        status_data = await DocumentParseService.poll_task_status(task_id)
                        final_status = status_data.get("status", "")

                        # SSE: push current status
                        await EventService.publish_event(user_id=user_id, event_type=evt,
                            data={"type": evt, "task_id": task_id, "status": final_status,
                                  "progress": status_data.get("progress", {}),
                                  "file_name": doc.file_name,
                                  "suggestion": status_data.get("suggestion"),
                                  "error_message": status_data.get("error_message")})

                        if final_status == "completed":
                            break
                        if final_status == "failed":
                            err_msg = status_data.get("error_message", "未知错误")
                            results.append(f"📄 {doc.file_name}: 解析失败 — {err_msg}")
                            break
                        if final_status == "incomplete":
                            break
                    else:
                        # SSE: timeout as failed（012-doc-parse-progress T004）
                        await EventService.publish_event(user_id=user_id, event_type=evt,
                            data={"type": evt, "task_id": task_id, "status": "failed",
                                  "progress": {"current": 0, "total": 0}, "file_name": doc.file_name,
                                  "suggestion": None, "error_message": f"解析超时（{max_wait}秒）"})
                        results.append(f"📄 {doc.file_name}: 解析超时（{max_wait}秒）")
                        continue

                    # incomplete: 获取部分结果（012-doc-parse-progress T006）
                    if final_status == "incomplete":
                        try:
                            partial_md = await DocumentParseService.get_task_result(task_id, format="markdown")
                            if partial_md:
                                suggestion = status_data.get("suggestion", "")
                                warning = f"⚠️ 部分解析完成" + (f"（{suggestion}）" if suggestion else "")
                                display = partial_md[:max_len]
                                if len(partial_md) > max_len:
                                    display += f"\n\n[内容已截断，完整内容共 {len(partial_md)} 字符]"
                                results.append(f"📄 {doc.file_name}（{warning}）:\n{display}")
                            else:
                                results.append(f"📄 {doc.file_name}: 部分解析完成，但结果为空")
                        except Exception as e:
                            logger.warning("Incomplete result fetch failed: task=%s, err=%s", task_id, e)
                            results.append(f"📄 {doc.file_name}: 部分解析完成，获取结果失败")
                        continue

                    if final_status != "completed":
                        continue

                    # 获取解析结果
                    md_content = await DocumentParseService.get_task_result(task_id, format="markdown")
                    if not md_content:
                        results.append(f"📄 {doc.file_name}: 解析结果为空")
                        continue

                    # 5. 双写缓存 + dispatch Embedding
                    await DocumentParseService.save_parsed_result(doc, md_content)

                    # 6. 截断输出
                    display = md_content[:max_len]
                    if len(md_content) > max_len:
                        display += f"\n\n[内容已截断，完整内容共 {len(md_content)} 字符]"
                    results.append(f"📄 {doc.file_name}:\n{display}")

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
    prompt = render("document_subagent.j2")
    return await run_subagent(
        task,
        config,
        tools=[doc_list, doc_read, doc_search, document_parse],
        prompt=prompt,
        name="document_subagent",
        timeout=getattr(settings, "DOCUMENT_SUBAGENT_TIMEOUT", 1200),
    )
