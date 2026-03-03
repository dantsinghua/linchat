import base64
import json as _json
import logging
from typing import Any, AsyncIterator, Optional

from django.conf import settings
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


def build_multimodal_messages(user_message: str, attachments: list[Any]) -> tuple[HumanMessage, list[str]]:
    from apps.common.storage.minio_service import minio_service
    from apps.media.services.video import preprocess_video

    if not attachments:
        return HumanMessage(content=user_message), []

    content: list[dict[str, Any]] = []
    media_types: list[str] = []
    has_audio = any(a.media_type == "audio" for a in attachments)
    effective_message = "" if (has_audio and user_message == "[语音消息]") else user_message

    if effective_message:
        content.append({"type": "text", "text": effective_message})

    for att in attachments:
        if att.media_type not in media_types:
            media_types.append(att.media_type)
        try:
            file_bytes = minio_service.download_file(
                bucket=settings.MINIO_BUCKET_MEDIA, object_name=att.storage_path,
            )
            b64 = base64.b64encode(file_bytes).decode("utf-8")
            if att.media_type == "image":
                content.append({"type": "image_url", "image_url": {"url": f"data:{att.mime_type};base64,{b64}"}})
            elif att.media_type == "video":
                processed = preprocess_video(file_bytes) or file_bytes
                vb64 = base64.b64encode(processed).decode("utf-8")
                content.append({"type": "video_url", "video_url": {"url": f"data:video/mp4;base64,{vb64}"}})
            elif att.media_type == "audio":
                content.append({"type": "audio_url", "audio_url": {"url": f"data:{att.mime_type};base64,{b64}"}})
        except Exception as e:
            logger.warning("处理附件失败: uuid=%s, error=%s", att.attachment_uuid, e)
            content.append({"type": "text", "text": f"[附件加载失败: {att.file_name}]"})

    return HumanMessage(content=content), media_types


async def stream_multimodal_httpx(
    content: list[dict], mm_config: dict, system_prompt: str = "",
    stop_event: Optional[Any] = None,
) -> AsyncIterator[tuple[str, Optional[dict[str, int]]]]:
    import httpx

    base_url = mm_config["url"].rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    api_key = mm_config.get("api_key") or "not-needed"
    model_name, max_tokens = mm_config["name"], mm_config.get("max_output_tokens", 1024)
    timeout = getattr(settings, "LLM_GATEWAY_INFERENCE_TIMEOUT", 180)

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0)) as client:
        async with client.stream(
            "POST", f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model_name, "messages": messages, "stream": True, "max_tokens": max_tokens},
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise RuntimeError(
                    f"Gateway HTTP {response.status_code}: {body.decode('utf-8', errors='replace')[:500]}"
                )
            async for line in response.aiter_lines():
                if stop_event and stop_event.is_set():
                    break
                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = _json.loads(data_str)
                    if chunk.get("error"):
                        err = chunk["error"]
                        raise RuntimeError(
                            f"Gateway 多模态推理错误 ({err.get('code', '')}): {err.get('message', '未知错误')}"
                        )
                    choices = chunk.get("choices", [])
                    delta = choices[0].get("delta", {}).get("content", "") if choices else ""
                    usage = chunk.get("usage")
                    if delta or usage:
                        yield delta, usage
                except _json.JSONDecodeError:
                    logger.warning("多模态 SSE JSON 解析失败: %s", data_str[:100])
