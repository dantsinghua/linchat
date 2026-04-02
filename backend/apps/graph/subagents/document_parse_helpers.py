import asyncio
import logging
import re

from django.conf import settings

from apps.common.event_service import EventService, EventType, build_doc_parse_event

logger = logging.getLogger(__name__)


def extract_outline(content: str, max_headings: int = 30) -> str:
    headings = []
    for match in re.finditer(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE):
        level = len(match.group(1))
        indent = "  " * (level - 1)
        headings.append(f"{indent}- {match.group(2).strip()}")
        if len(headings) >= max_headings:
            headings.append("  ... (更多章节省略)")
            break
    return "\n".join(headings) if headings else ""


def build_truncated_result(file_name: str, content: str, max_len: int, label: str = "") -> str:
    outline = extract_outline(content)
    parts = [f"📄 {file_name}{'（' + label + '）' if label else ''}（共 {len(content)} 字符）"]
    if outline:
        parts.append(f"\n## 文档目录结构\n{outline}")
    parts.append(f"\n## 内容预览（前 {max_len} 字符）\n{content[:max_len]}")
    parts.append(
        "\n---\n"
        "⚠️ 文档内容过长，以上为目录结构和前部内容预览。"
        "文档已完成解析并建立索引。"
        "请根据目录结构，使用 doc_search 工具按关键词检索需要的具体章节内容，"
        "不要反复调用 doc_read 试图获取完整内容。"
    )
    return "\n".join(parts)


async def poll_parse_task(task_id: str, doc, user_id: int, max_len: int):
    from apps.media.services.document import DocumentParseService

    poll_interval = getattr(settings, "DOC_PARSE_POLL_INTERVAL", 3)
    max_wait = getattr(settings, "DOC_PARSE_POLL_MAX_WAIT", 900)
    elapsed = 0
    final_status = ""
    _prev_status = ""
    evt = EventType.DOC_PARSE_PROGRESS.value

    logger.info(
        "[DocPoll] START: task=%s, file=%s, poll_interval=%ds, max_wait=%ds",
        task_id, doc.file_name, poll_interval, max_wait)

    await EventService.publish_event(
        user_id=user_id, event_type=evt,
        data=build_doc_parse_event(task_id, "pending", {"current": 0, "total": 0}, doc.file_name))

    poll_count = 0
    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        poll_count += 1
        try:
            status_data = await DocumentParseService.poll_task_status(task_id)
        except Exception as e:
            logger.warning("[DocPoll] poll error: task=%s, poll#=%d, elapsed=%ds, err=%s", task_id, poll_count, elapsed, e)
            continue
        final_status = status_data.get("status", "")
        progress = status_data.get("progress", {})
        cur = progress.get("current", "?")
        total = progress.get("total", "?")

        logger.info(
            "[DocPoll] poll#=%d: task=%s, status=%s, progress=%s/%s, elapsed=%ds",
            poll_count, task_id, final_status, cur, total, elapsed)
        _prev_status = final_status

        await EventService.publish_event(
            user_id=user_id, event_type=evt,
            data=build_doc_parse_event(task_id, final_status, status_data.get("progress", {}),
                                       doc.file_name, status_data.get("suggestion"), status_data.get("error_message")))

        if final_status == "completed":
            logger.info("[DocPoll] COMPLETED: task=%s, progress=%s/%s, elapsed=%ds, polls=%d", task_id, cur, total, elapsed, poll_count)
            break
        if final_status == "failed":
            err_msg = status_data.get("error_message", "未知错误")
            logger.warning("[DocPoll] FAILED: task=%s, err=%s, elapsed=%ds, polls=%d", task_id, err_msg, elapsed, poll_count)
            return "failed", f"📄 {doc.file_name}: 解析失败 — {err_msg}", status_data
        if final_status == "incomplete":
            # incomplete 且仍有页面未解析 → 继续轮询（gateway 可能还在处理）
            if cur != "?" and total != "?" and int(cur) < int(total):
                logger.info(
                    "[DocPoll] INCOMPLETE but progressing (%s/%s), continue polling: task=%s, elapsed=%ds",
                    cur, total, task_id, elapsed)
                continue
            logger.warning(
                "[DocPoll] INCOMPLETE (final): task=%s, progress=%s/%s, elapsed=%ds, polls=%d, suggestion=%s",
                task_id, cur, total, elapsed, poll_count, status_data.get("suggestion", ""))
            break
    else:
        logger.warning("[DocPoll] TIMEOUT: task=%s, last_status=%s, elapsed=%ds/%ds, polls=%d", task_id, final_status, elapsed, max_wait, poll_count)
        await EventService.publish_event(
            user_id=user_id, event_type=evt,
            data=build_doc_parse_event(task_id, "failed", {"current": 0, "total": 0}, doc.file_name,
                                       error_message=f"解析超时（{max_wait}秒）"))
        return "timeout", f"📄 {doc.file_name}: 解析超时（{max_wait}秒）", {}

    if final_status == "incomplete":
        logger.info("[DocPoll] fetching partial result: task=%s", task_id)
        try:
            partial_md = await DocumentParseService.get_task_result(task_id, format="markdown")
            logger.info("[DocPoll] partial result: task=%s, size=%d", task_id, len(partial_md) if partial_md else 0)
            if partial_md:
                suggestion = status_data.get("suggestion", "")
                warning = "部分解析" + (f"，{suggestion}" if suggestion else "")
                if len(partial_md) > max_len:
                    return "incomplete", build_truncated_result(doc.file_name, partial_md, max_len, label=warning), status_data
                return "incomplete", f"📄 {doc.file_name}（⚠️ {warning}）:\n{partial_md}", status_data
            return "incomplete", f"📄 {doc.file_name}: 部分解析完成，但结果为空", status_data
        except Exception as e:
            logger.warning("[DocPoll] partial result fetch failed: task=%s, err=%s", task_id, e)
            return "incomplete", f"📄 {doc.file_name}: 部分解析完成，获取结果失败", status_data

    return final_status, None, status_data
