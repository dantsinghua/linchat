"""ambient 语音模式轻量推理路径（batch-08）。

绕过 LangGraph / SubAgent / 工具 / 完整记忆召回，用「精简 system prompt +
最近 N 轮历史（N×2 条 user/assistant）+ 用户消息」直调 LLM Gateway
`/v1/chat/completions`，将 ambient 的 LLM 推理 P50 从 ~6.3s 降到 ~2s。

voice_chat 模式不受影响，仍走完整 AgentService.execute。
开关 VOICE_AMBIENT_LIGHT_ENABLED=false 即回退完整 Agent 路径（首选回滚手段）。

httpx 直调范式照抄 apps/graph/multimodal.py:stream_multimodal_httpx；
配置/解密复用 model_service.get_active_model（内置 SM4 解密），异常分类复用
map_llm_exception，消息落库复用 create_first_token_messages / finalize_message。
"""

import json as _json
import logging
from typing import Any, AsyncGenerator, Optional

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from apps.chat.models import Message
from apps.chat.repositories import message_repo
from apps.chat.services.types import StreamChunk
from apps.common.exceptions import map_llm_exception
from apps.context.loader import render
from apps.graph.services.helpers import create_first_token_messages, finalize_message
from apps.models.services import model_service
from apps.users.repositories import user_repo

logger = logging.getLogger(__name__)


class AmbientLightPipeline:
    """ambient 轻量推理：直调 Gateway，跳过 Agent 编排与记忆召回。"""

    @staticmethod
    async def stream(
        user_id: int,
        request_id: str,
        user_text: str,
    ) -> AsyncGenerator[StreamChunk, None]:
        """流式生成 ambient 回复。yield 的 StreamChunk 与 AgentService.execute 同构，
        voice_pipeline 循环体（content/error/interrupted 分支）可无缝复用。
        """
        config = await sync_to_async(model_service.get_active_model)("tool")
        if not config:
            yield StreamChunk(type="error", content="未配置可用的对话模型")
            return

        messages = await AmbientLightPipeline._build_messages(user_id, user_text)
        start_time = timezone.now()
        max_seq = await message_repo.get_max_sequence(user_id)
        assistant_msg: Optional[Message] = None
        full_response = ""
        prompt_tokens = completion_tokens = 0
        try:
            async for delta, usage in AmbientLightPipeline._call_gateway(
                config, messages
            ):
                if usage:
                    prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                    completion_tokens = usage.get(
                        "completion_tokens", completion_tokens
                    )
                if not delta:
                    continue
                # 首个 content token 才落库 user+assistant（保持与 Agent 路径一致的建消息时机）
                if assistant_msg is None:
                    _, assistant_msg = await create_first_token_messages(
                        user_id,
                        user_text,
                        request_id,
                        max_seq,
                        start_time,
                        timezone.now(),
                        False,
                        None,
                        [],
                    )
                    sc = StreamChunk(
                        type="content",
                        content=delta,
                        message_id=assistant_msg.message_id,
                        request_id=request_id,
                    )
                else:
                    sc = StreamChunk(
                        type="content",
                        content=delta,
                        message_id=assistant_msg.message_id,
                    )
                full_response += delta
                yield sc
        except Exception as e:
            mapped = map_llm_exception(e)
            logger.warning(
                "ambient light stream error: user=%s, req=%s, err=%s",
                user_id,
                request_id,
                e,
            )
            if assistant_msg is not None:
                await AmbientLightPipeline._persist(
                    assistant_msg,
                    full_response,
                    start_time,
                    user_id,
                    prompt_tokens,
                    completion_tokens,
                    Message.STATUS_INTERRUPTED,
                )
            yield StreamChunk(type="error", content=mapped.message)
            return

        if assistant_msg is not None:
            await AmbientLightPipeline._persist(
                assistant_msg,
                full_response,
                start_time,
                user_id,
                prompt_tokens,
                completion_tokens,
                Message.STATUS_NORMAL,
            )
        yield StreamChunk(
            type="done",
            content="",
            message_id=assistant_msg.message_id if assistant_msg else None,
        )

    @staticmethod
    async def _build_messages(user_id: int, user_text: str) -> list[dict[str, str]]:
        """system（精简 prompt）+ 最近 N 轮历史 + 当前用户消息。隔离粒度：user_id。"""
        system_prompt = render(
            "ambient_light_prompt.j2",
            today_date=timezone.localtime().strftime("%Y-%m-%d"),
            user_timezone=settings.TIME_ZONE,
        )
        rounds = getattr(settings, "VOICE_AMBIENT_LIGHT_HISTORY_ROUNDS", 3)
        # 「保留最近 N 轮」= N×2 条 user/assistant 消息；find_latest 按 -created_time 倒序，需 reverse
        history = await message_repo.find_latest_by_user(user_id, limit=rounds * 2)
        history.reverse()

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for m in history:
            if m.role == "user" and m.content:
                messages.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                content = (m.content or "").removesuffix("[已中断]")
                if content:
                    messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": user_text})
        return messages

    @staticmethod
    async def _call_gateway(
        config: dict[str, Any],
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[tuple[str, Optional[dict[str, int]]], None]:
        """照抄 multimodal.py 的 httpx 流式直调范式，逐行解析 SSE。"""
        base_url = config["url"].rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        api_key = config.get("api_key") or "not-needed"
        model_name = config["name"]
        max_tokens = config.get("max_output_tokens", 1024)
        timeout = getattr(settings, "LLM_CALL_TIMEOUT", 60)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0)
        ) as client:
            async with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": messages,
                    "stream": True,
                    "max_tokens": max_tokens,
                },
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise RuntimeError(
                        f"Gateway HTTP {response.status_code}: "
                        f"{body.decode('utf-8', errors='replace')[:500]}"
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = _json.loads(data_str)
                    except _json.JSONDecodeError:
                        logger.warning(
                            "ambient light SSE JSON 解析失败: %s", data_str[:100]
                        )
                        continue
                    if chunk.get("error"):
                        err = chunk["error"]
                        raise RuntimeError(
                            f"Gateway 推理错误 ({err.get('code', '')}): "
                            f"{err.get('message', '未知错误')}"
                        )
                    choices = chunk.get("choices", [])
                    delta = (
                        choices[0].get("delta", {}).get("content", "")
                        if choices
                        else ""
                    )
                    usage = chunk.get("usage")
                    if delta or usage:
                        yield delta, usage

    @staticmethod
    async def _persist(
        assistant_msg: Message,
        full_response: str,
        start_time: Any,
        user_id: int,
        pt: int,
        ct: int,
        status: int,
    ) -> None:
        """自持久化 assistant 消息（绕过 AgentService 后必须自己落库，否则丢历史）。"""
        duration_ms = int((timezone.now() - start_time).total_seconds() * 1000)
        finalize_message(assistant_msg, full_response, status, duration_ms, pt, ct)
        await message_repo.update(assistant_msg)
        await user_repo.add_message_count(user_id, 2)
        await user_repo.add_tokens(user_id, pt + ct)
