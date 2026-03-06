import logging
from asgiref.sync import sync_to_async
from django.conf import settings
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from apps.graph.subagents.base import _get_user_id, run_subagent

logger = logging.getLogger(__name__)

MULTIMODAL_PROMPT = """你是多媒体分析助手。仅处理图片、视频、音频分析，不处理文档。

## 工具
- multimodal_analyze: 分析图片、视频、音频附件

## 重要规则（必须遵守）
- 收到分析请求时，必须直接调用 multimodal_analyze 工具，不要质疑或确认附件是否存在
- 附件由工具内部自动从上下文加载，你无需预先验证附件
- 即使你在消息中看不到附件内容，也必须调用工具——工具会自行处理
- PDF/DOCX 文档不由本助手处理，文档请求应路由到 document_subagent

## 执行策略
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
async def multimodal_subagent(task: str, config: RunnableConfig) -> str:
    """分析用户上传的图片、视频、音频等多媒体文件内容。当用户上传了附件并需要理解其内容时使用（不含文档，文档请使用 document_subagent）。"""
    cfg = config.get("configurable", {})
    uuids = cfg.get("attachment_uuids", [])
    if uuids:
        task = f"{task}\n\n[系统：用户已上传 {len(uuids)} 个附件，请直接调用对应工具进行分析，附件会自动从上下文加载。]"
    return await run_subagent(task, config, tools=[multimodal_analyze], prompt=MULTIMODAL_PROMPT, name="multimodal_subagent", timeout=getattr(settings, "MULTIMODAL_SUBAGENT_TIMEOUT", 1200))
