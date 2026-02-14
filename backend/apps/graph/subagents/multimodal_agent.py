"""多模态 SubAgent — 分析用户上传的图片/视频/音频/文档文件

主 Agent（DeepSeek）委派多媒体分析任务到此 SubAgent，
SubAgent 内部通过工具完成实际推理：
- multimodal_analyze: 图片/视频/音频 → MiniCPM-o httpx 直连
- document_parse: PDF/DOCX 文档 → Gateway 文档解析 API

Langfuse 链路:
  主 Agent → multimodal_subagent(tool) → 内部 DeepSeek
  → multimodal_analyze(tool) → MiniCPM-o httpx
  → document_parse(tool) → Gateway doc parse API
"""

import asyncio
import logging

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
    configurable = config.get("configurable", {})
    attachment_uuids = configurable.get("attachment_uuids", [])
    stop_event = configurable.get("stop_event")
    request_id = configurable.get("request_id", "unknown")

    if not attachment_uuids:
        return "当前没有用户上传的附件"

    # 1. 加载附件，过滤掉文档类型（文档由 document_parse 处理）
    attachments = await media_attachment_repo.get_by_uuids(attachment_uuids, user_id)
    if not attachments:
        return "附件加载失败或已过期"

    media_attachments = [a for a in attachments if a.media_type != "document"]
    if not media_attachments:
        return "没有需要多模态分析的附件（文档请使用 document_parse 工具）"

    # 2. 构建多模态消息
    mm_message, media_types = build_multimodal_messages(task, media_attachments)

    # 3. 从 DB 获取多模态模型配置
    mm_config = await sync_to_async(model_service.get_active_model)("multimodal")
    if not mm_config:
        return "未配置多模态模型，请联系管理员"

    # 4. 获取 GPU 锁并调用 MiniCPM-o
    full_response = ""
    try:
        async with acquire_gpu_lock(request_id):
            async for delta, usage in stream_multimodal_httpx(
                content=mm_message.content,
                mm_config=mm_config,
                system_prompt="",
                stop_event=stop_event,
            ):
                full_response += delta
    except GPULockTimeout:
        logger.warning("GPU 锁等待超时: user_id=%d, request_id=%s", user_id, request_id)
        return "GPU 资源繁忙，请稍后重试"
    except Exception as e:
        logger.error("多模态推理失败: user_id=%d, error=%s", user_id, e)
        return f"多模态分析失败: {e}"

    return full_response or "多模态模型未返回结果"


@tool
async def document_parse(task: str, config: RunnableConfig) -> str:
    """解析用户上传的 PDF/DOCX 文档内容。task 为分析指令，文档附件自动从上下文加载。"""
    from apps.chat.repositories import media_attachment_repo
    from apps.chat.services.document_parse_service import (
        DocumentParseError,
        DocumentParseService,
    )
    from apps.chat.services.gpu_lock import GPULockTimeout, acquire_gpu_lock
    from apps.chat.services.inference_service import inference_service

    user_id = _get_user_id(config)
    configurable = config.get("configurable", {})
    attachment_uuids = configurable.get("attachment_uuids", [])
    request_id = configurable.get("request_id", "unknown")

    if not attachment_uuids:
        return "当前没有用户上传的附件"

    # 1. 加载附件，过滤出文档类型
    attachments = await media_attachment_repo.get_by_uuids(attachment_uuids, user_id)
    if not attachments:
        return "附件加载失败或已过期"

    doc_attachments = [a for a in attachments if a.media_type == "document"]
    if not doc_attachments:
        return "没有文档附件（图片/视频/音频请使用 multimodal_analyze 工具）"

    poll_interval = getattr(settings, "DOC_PARSE_POLL_INTERVAL", 3)
    poll_max_wait = getattr(settings, "DOC_PARSE_POLL_MAX_WAIT", 900)
    max_result_length = getattr(settings, "DOC_PARSE_MAX_RESULT_LENGTH", 8000)

    results = []
    for doc in doc_attachments:
        try:
            # 2. 获取 GPU 锁
            async with acquire_gpu_lock(request_id):
                # 3. 创建解析任务（skip_background_poll=True，由工具内同步轮询）
                task_result = await DocumentParseService.parse_document(
                    user_id=user_id,
                    attachment_uuid=doc.attachment_uuid,
                    skip_background_poll=True,
                )
                task_id = task_result.get("task_id", "")

                if not task_id:
                    results.append(f"[{doc.file_name}] 创建解析任务失败")
                    continue

                # 4. 同步轮询任务状态
                elapsed = 0
                final_status = ""
                while elapsed < poll_max_wait:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                    # 续期推理任务 TTL
                    try:
                        await inference_service.refresh_task_ttl(user_id)
                    except Exception:
                        pass

                    try:
                        status_data = await DocumentParseService.poll_task_status(task_id)
                    except DocumentParseError as e:
                        logger.warning(
                            "文档解析轮询失败: task_id=%s, error=%s", task_id, e.message
                        )
                        continue

                    final_status = status_data.get("status", "")

                    if final_status == "completed":
                        break
                    elif final_status == "failed":
                        error_msg = status_data.get("error_message", "未知错误")
                        results.append(f"[{doc.file_name}] 解析失败: {error_msg}")
                        break

                if final_status == "completed":
                    # 5. 获取解析结果
                    try:
                        content = await DocumentParseService.get_task_result(
                            task_id, format="markdown"
                        )
                        if isinstance(content, str) and len(content) > max_result_length:
                            content = content[:max_result_length] + "\n\n[内容已截断]"
                        results.append(f"## {doc.file_name}\n\n{content}")
                    except DocumentParseError as e:
                        results.append(f"[{doc.file_name}] 获取结果失败: {e.message}")
                elif final_status != "failed":
                    results.append(
                        f"[{doc.file_name}] 解析超时（{poll_max_wait}秒）"
                    )

        except GPULockTimeout:
            logger.warning(
                "文档解析 GPU 锁等待超时: user_id=%d, file=%s",
                user_id,
                doc.file_name,
            )
            results.append(f"[{doc.file_name}] GPU 资源繁忙，请稍后重试")
        except DocumentParseError as e:
            results.append(f"[{doc.file_name}] 解析错误: {e.message}")
        except Exception as e:
            logger.error(
                "文档解析异常: user_id=%d, file=%s, error=%s",
                user_id,
                doc.file_name,
                e,
            )
            results.append(f"[{doc.file_name}] 解析异常: {e}")

    return "\n\n".join(results) if results else "未能解析任何文档"


@tool
async def multimodal_subagent(task: str, config: RunnableConfig) -> str:
    """分析用户上传的图片、视频、音频、文档等多媒体文件内容。
    当用户上传了附件并需要理解其内容时使用。"""
    return await run_subagent(
        task,
        config,
        tools=[multimodal_analyze, document_parse],
        prompt=MULTIMODAL_PROMPT,
        name="multimodal_subagent",
        timeout=getattr(settings, "MULTIMODAL_SUBAGENT_TIMEOUT", 1200),
    )
