import asyncio, logging
from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from apps.graph.subagents.base import _get_user_id, run_subagent

logger = logging.getLogger(__name__)

MULTIMODAL_PROMPT = """你是多媒体分析助手。分析用户上传的图片、视频、音频、文档文件内容。

## 工具选择策略
- 图片、视频、音频附件 → 使用 multimodal_analyze 工具
- PDF、DOCX 文档附件 → 使用 document_parse 工具
- 混合附件（如图片+文档）→ 分别调用对应工具

## 重要规则（必须遵守）
- 收到分析请求时，必须直接调用对应工具，不要质疑或确认附件是否存在
- 附件由工具内部自动从上下文加载，你无需预先验证附件
- 即使你在消息中看不到附件内容，也必须调用工具——工具会自行处理

## 执行策略
- 根据附件类型选择正确的工具
- 将分析结果如实、完整地返回
- 如果分析失败，返回具体错误信息
- 独立完成任务，返回完整的分析结果"""


@tool
async def multimodal_analyze(task: str, config: RunnableConfig) -> str:
    """加载并分析用户上传的图片、视频、音频附件（不含文档）。task 为分析指令，附件自动从上下文加载。"""
    from apps.chat.repositories import media_attachment_repo
    from apps.chat.services.gpu_lock import GPULockTimeout, acquire_gpu_lock
    from apps.graph.agent import build_multimodal_messages, stream_multimodal_httpx
    from apps.models.services import model_service
    user_id = _get_user_id(config)
    cfg = config.get("configurable", {})
    uuids, stop_event, req_id = cfg.get("attachment_uuids", []), cfg.get("stop_event"), cfg.get("request_id", "unknown")
    if not uuids: return "当前没有用户上传的附件"
    attachments = await media_attachment_repo.get_by_uuids(uuids, user_id)
    if not attachments: return "附件加载失败或已过期"
    media = [a for a in attachments if a.media_type != "document"]
    if not media: return "没有需要多模态分析的附件（文档请使用 document_parse 工具）"
    mm_msg, _ = build_multimodal_messages(task, media)
    mm_cfg = await sync_to_async(model_service.get_active_model)("multimodal")
    if not mm_cfg: return "未配置多模态模型，请联系管理员"
    result = ""
    try:
        async with acquire_gpu_lock(req_id):
            async for delta, usage in stream_multimodal_httpx(content=mm_msg.content, mm_config=mm_cfg, system_prompt="", stop_event=stop_event):
                result += delta
    except GPULockTimeout:
        logger.warning("GPU lock timeout: user=%d, req=%s", user_id, req_id); return "GPU 资源繁忙，请稍后重试"
    except Exception as e:
        logger.error("Multimodal fail: user=%d, err=%s", user_id, e); return f"多模态分析失败: {e}"
    return result or "多模态模型未返回结果"


@tool
async def document_parse(task: str, config: RunnableConfig) -> str:
    """解析用户上传的 PDF/DOCX 文档内容。task 为分析指令，文档附件自动从上下文加载。"""
    from apps.chat.repositories import media_attachment_repo
    from apps.chat.services.document_parse_service import DocumentParseError, DocumentParseService
    from apps.chat.services.gpu_lock import GPULockTimeout, acquire_gpu_lock
    from apps.chat.services.inference_service import inference_service
    user_id = _get_user_id(config)
    cfg = config.get("configurable", {})
    uuids, req_id = cfg.get("attachment_uuids", []), cfg.get("request_id", "unknown")
    if not uuids: return "当前没有用户上传的附件"
    attachments = await media_attachment_repo.get_by_uuids(uuids, user_id)
    if not attachments: return "附件加载失败或已过期"
    docs = [a for a in attachments if a.media_type == "document"]
    if not docs: return "没有文档附件（图片/视频/音频请使用 multimodal_analyze 工具）"
    poll_iv = getattr(settings, "DOC_PARSE_POLL_INTERVAL", 3)
    poll_max = getattr(settings, "DOC_PARSE_POLL_MAX_WAIT", 900)
    max_len = getattr(settings, "DOC_PARSE_MAX_RESULT_LENGTH", 8000)
    results = []
    for doc in docs:
        try:
            async with acquire_gpu_lock(req_id):
                tr = await DocumentParseService.parse_document(user_id=user_id, attachment_uuid=doc.attachment_uuid, skip_background_poll=True)
                tid = tr.get("task_id", "")
                logger.info("Doc parse task: tid=%s, file=%s", tid, doc.file_name)
                if not tid:
                    results.append(f"[{doc.file_name}] 创建解析任务失败"); continue
                elapsed, final = 0, ""
                while elapsed < poll_max:
                    await asyncio.sleep(poll_iv); elapsed += poll_iv
                    try: await inference_service.refresh_task_ttl(user_id)
                    except Exception: pass
                    try: sd = await DocumentParseService.poll_task_status(tid)
                    except DocumentParseError: continue
                    final = sd.get("status", "")
                    if final == "completed":
                        logger.info("Doc parse done: tid=%s, elapsed=%ds", tid, elapsed); break
                    elif final == "failed":
                        results.append(f"[{doc.file_name}] 解析失败: {sd.get('error_message', '未知错误')}"); break
                if final == "completed":
                    try:
                        content = await DocumentParseService.get_task_result(tid, format="markdown")
                        if isinstance(content, str) and len(content) > max_len: content = content[:max_len] + "\n\n[内容已截断]"
                        results.append(f"## {doc.file_name}\n\n{content}")
                    except DocumentParseError as e:
                        results.append(f"[{doc.file_name}] 获取结果失败: {e.message}")
                elif final != "failed":
                    results.append(f"[{doc.file_name}] 解析超时（{poll_max}秒）")
        except GPULockTimeout:
            logger.warning("Doc parse GPU timeout: user=%d, file=%s", user_id, doc.file_name)
            results.append(f"[{doc.file_name}] GPU 资源繁忙，请稍后重试")
        except DocumentParseError as e:
            results.append(f"[{doc.file_name}] 解析错误: {e.message}")
        except Exception as e:
            logger.error("Doc parse error: user=%d, file=%s, err=%s", user_id, doc.file_name, e)
            results.append(f"[{doc.file_name}] 解析异常: {e}")
    total = "\n\n".join(results) if results else "未能解析任何文档"
    logger.info("Doc parse return: files=%d, len=%d", len(docs), len(total))
    return total


@tool
async def multimodal_subagent(task: str, config: RunnableConfig) -> str:
    """分析用户上传的图片、视频、音频、文档等多媒体文件内容。当用户上传了附件并需要理解其内容时使用。"""
    cfg = config.get("configurable", {})
    uuids = cfg.get("attachment_uuids", [])
    if uuids:
        task = f"{task}\n\n[系统：用户已上传 {len(uuids)} 个附件，请直接调用对应工具进行分析，附件会自动从上下文加载。]"
    return await run_subagent(task, config, tools=[multimodal_analyze, document_parse], prompt=MULTIMODAL_PROMPT, name="multimodal_subagent", timeout=getattr(settings, "MULTIMODAL_SUBAGENT_TIMEOUT", 1200))
