"""Voice WebSocket Consumer

参考:
- specs/009-voice-interaction/plan.md Phase 3
- docs/voice-capability-requirements.md#6 WebSocket 持续监控模式

职责：
- Web 端 Cookie 认证（已由 WebSocketTokenAuthMiddleware 处理）
- 上游 llmgateway WebSocket 连接代理
- 音频帧透传（Binary 帧 PCM16）
- llmgateway 事件处理与转发
- 语音会话生命周期管理
- 连接空闲超时检测
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from apps.users.repositories import user_repo
from apps.voice.services.device_service import device_service
from apps.voice.services.gateway_client import GatewayClient
from apps.voice.services.response_decision_service import (
    DecisionResult,
    response_decision_service,
)
from apps.voice.services.speaker_service import speaker_service
from apps.voice.services.voice_session_service import voice_session_service
from core.redis import get_redis

logger = logging.getLogger(__name__)


class VoiceConsumer(AsyncWebsocketConsumer):
    """语音交互 WebSocket Consumer

    编排 GatewayClient（上游连接）和 VoiceSessionService（会话/持久化），
    实现完整的语音流式交互。

    状态追踪：
    - _current_response_id: 当前进行中的推理 response_id
    - _accumulated_content: 累积的 response.delta 文本
    - _current_segment_id: 当前语音段 ID（vad.speech_start 时生成）
    - _response_start_time: 推理开始时间（计算 response_time_ms）
    - _current_speaker_id: 当前识别到的说话人 ID
    - _response_cancelled: 标记 cancel 后不再期望 response.end
    """

    async def connect(self) -> None:
        """WebSocket 连接建立

        支持两种认证方式：
        1. Cookie 认证（Web 端，由 WebSocketTokenAuthMiddleware 处理）
        2. 设备 API Token 认证（外部设备，通过 query_string 参数 token）
        """
        user_id = self.scope.get("user_id")

        # T037: 设备 API Token 认证
        if not user_id:
            # 中间件未认证，检查 query_string 中的 token 参数
            query_string = self.scope.get("query_string", b"").decode("utf-8")
            params = parse_qs(query_string)
            token_list = params.get("token", [])

            if token_list:
                token = token_list[0]
                auth_result = await device_service.authenticate_by_token(token)
                if auth_result:
                    user_id = auth_result["user_id"]
                    self.user_id: int = user_id
                    self.username: str = auth_result.get("device_name", "")
                    self._is_device_connection: bool = True
                    logger.info(
                        "Voice WS device authenticated: user_id=%s, "
                        "device_uuid=%s",
                        user_id,
                        auth_result.get("device_uuid"),
                    )
                else:
                    logger.warning("Voice WS device token auth failed")
                    await self.close(code=4001)
                    return
            else:
                # 既没有 Cookie 认证也没有 Token 参数
                await self.close(code=4001)
                return
        else:
            self.user_id = user_id
            self.username = self.scope.get("username", "")
            self._is_device_connection = False

        # T055a: WebSocket 连接频率检查（10次/分）
        ws_rate_key = f"voice:ws_connect_rate:{self.user_id}"
        try:
            redis_client = await get_redis()
            try:
                count = await redis_client.incr(ws_rate_key)
                if count == 1:
                    await redis_client.expire(ws_rate_key, 60)
                if count > 10:
                    logger.warning(
                        "Voice WS rate limited: user_id=%s, count=%s",
                        self.user_id,
                        count,
                    )
                    await self.accept()
                    await self.send(text_data=json.dumps({
                        "type": "error",
                        "data": {
                            "code": "WS_RATE_LIMIT",
                            "message": "连接过于频繁，请稍后重试",
                            "recoverable": False,
                        },
                    }))
                    await self.close(code=4029)
                    return
            finally:
                await redis_client.aclose()
        except Exception as e:
            logger.warning(
                "Voice WS rate check failed: user_id=%s, error=%s",
                self.user_id,
                e,
            )

        # 状态初始化
        self._gateway: Optional[GatewayClient] = None
        self._current_response_id: Optional[str] = None
        self._accumulated_content: str = ""
        self._current_segment_id: Optional[str] = None
        self._response_start_time: Optional[float] = None
        self._current_speaker_id: Optional[str] = None
        self._response_cancelled: bool = False
        self._last_activity: float = time.time()
        self._idle_check_task: Optional[asyncio.Task] = None
        self._configured: bool = False
        self._identified_user_id: Optional[int] = None
        self._mode: str = "voice_chat"  # voice_chat | continuous_listen
        self._closed: bool = False  # 标记 WebSocket 是否已关闭

        logger.info("Voice WS connected: user_id=%s", user_id)
        await self.accept()

    async def disconnect(self, close_code: int) -> None:
        """WebSocket 连接断开，清理所有资源"""
        self._closed = True  # 立即标记，阻止后续 _send_json 发送
        user_id = getattr(self, "user_id", None)
        logger.info(
            "Voice WS disconnecting: user_id=%s, code=%s",
            user_id,
            close_code,
        )

        # 停止空闲检测
        if self._idle_check_task and not self._idle_check_task.done():
            self._idle_check_task.cancel()
            try:
                await self._idle_check_task
            except asyncio.CancelledError:
                pass

        # 取消正在进行的推理响应（防止 gateway 继续发送 delta 导致大量发送失败）
        if (
            self._current_response_id
            and self._gateway
            and self._gateway.connected
        ):
            try:
                await self._gateway.send_json({
                    "type": "response.cancel",
                    "response_id": self._current_response_id,
                })
                logger.info(
                    "Cancelled active response on disconnect: "
                    "user_id=%s, response_id=%s",
                    user_id,
                    self._current_response_id,
                )
            except Exception:
                pass

        # 断开上游 Gateway
        if self._gateway and self._gateway.connected:
            await self._gateway.disconnect()

        # 清理 Redis 会话状态
        if user_id:
            await voice_session_service.close_session(user_id)

        logger.info("Voice WS disconnected: user_id=%s", user_id)

    async def receive(
        self, text_data: str = None, bytes_data: bytes = None
    ) -> None:
        """接收客户端消息

        - text_data: JSON 控制消息（session.configure, session.close, response.cancel）
        - bytes_data: Binary PCM16 音频帧
        """
        self._last_activity = time.time()

        if bytes_data:
            await self._handle_audio_frame(bytes_data)
        elif text_data:
            await self._handle_json_message(text_data)

    # ========== JSON 消息处理 ==========

    async def _handle_json_message(self, text_data: str) -> None:
        """处理客户端发送的 JSON 控制消息"""
        try:
            message = json.loads(text_data)
        except json.JSONDecodeError:
            await self._send_error("INVALID_JSON", "无效的 JSON 格式")
            return

        msg_type = message.get("type")
        data = message.get("data", {})

        if msg_type == "session.configure":
            await self._handle_session_configure(data)
        elif msg_type == "session.reconnect":
            await self._handle_session_reconnect(data)
        elif msg_type == "session.close":
            await self._handle_session_close()
        elif msg_type == "response.cancel":
            await self._handle_response_cancel(data)
        else:
            logger.warning(
                "Voice WS unknown message type: user_id=%s, type=%s",
                self.user_id,
                msg_type,
            )

    async def _handle_session_configure(
        self, data: dict[str, Any]
    ) -> None:
        """处理 session.configure：建立上游连接并配置

        客户端发送 session.configure 后：
        1. 创建 Redis 会话（单会话强制 FR-034）
        2. 建立到 llmgateway 的 WebSocket 连接
        3. 转发 session.configure 到 llmgateway
        4. 启动空闲超时检测
        """
        # T051: 创建语音会话（多标签页冲突检测）
        created = await voice_session_service.create_session(self.user_id)
        if not created:
            # 已有活跃会话，通知客户端冲突后强制关闭旧会话
            await self._send_json({
                "type": "session.conflict",
                "data": {
                    "message": "检测到其他标签页的活跃语音会话，已自动接管",
                },
            })
            await voice_session_service.close_session(self.user_id)
            await voice_session_service.create_session(self.user_id)

        # 断开已有的上游连接
        if self._gateway and self._gateway.connected:
            await self._gateway.disconnect()

        # 建立到 llmgateway 的上游连接
        self._gateway = GatewayClient(
            on_event=self._handle_gateway_event,
            user_id=self.user_id,
        )

        connected = await self._gateway.connect()
        if not connected:
            await self._send_error(
                "GATEWAY_CONNECT_FAILED",
                "语音服务连接失败，请稍后重试",
                recoverable=False,
            )
            await voice_session_service.close_session(self.user_id)
            return

        # T042: 解析模式参数
        mode = data.get("mode", "voice_chat")
        if mode not in ("voice_chat", "continuous_listen"):
            mode = "voice_chat"
        self._mode = mode

        is_continuous = mode == "continuous_listen"

        # 构建 llmgateway session.configure 参数
        config = {
            "vad_enabled": True,
            "vad_threshold": data.get(
                "vad_threshold", settings.VOICE_VAD_THRESHOLD
            ),
            "speaker_identify": data.get("speaker_identify", False)
            or is_continuous,  # continuous_listen 强制开启声纹识别
            "speaker_threshold": data.get(
                "speaker_threshold", settings.VOICE_SPEAKER_THRESHOLD
            ),
            "auto_respond": not is_continuous,  # continuous_listen 禁用自动回复
            "audio_output": False,  # 当前版本不请求音频回复
            "model": "minicpm-o",
            "chunk_duration_ms": 30,
        }

        configured = await self._gateway.configure(config)
        if not configured:
            await self._send_error(
                "GATEWAY_CONFIGURE_FAILED",
                "语音服务配置失败",
                recoverable=False,
            )
            await self._gateway.disconnect()
            await voice_session_service.close_session(self.user_id)
            return

        # 更新会话状态
        await voice_session_service.update_session(
            self.user_id,
            upstream_connected=True,
            gateway_session_id=self._gateway.session_id,
        )

        self._configured = True

        # 启动空闲超时检测
        if self._idle_check_task and not self._idle_check_task.done():
            self._idle_check_task.cancel()
        self._idle_check_task = asyncio.create_task(
            self._idle_timeout_loop()
        )

        # 通知客户端配置完成
        await self._send_json({
            "type": "session.configured",
            "data": {
                "status": "ok",
                "session_id": self._gateway.session_id,
                "mode": self._mode,
            },
        })

        logger.info(
            "Voice session configured: user_id=%s, mode=%s, "
            "gateway_session=%s",
            self.user_id,
            self._mode,
            self._gateway.session_id,
        )

    async def _handle_session_reconnect(
        self, data: dict[str, Any]
    ) -> None:
        """T052: 处理 session.reconnect 断线恢复

        客户端断线重连后发送此消息。检查 Redis 会话状态：
        - 有活跃会话：恢复上游连接，发送 session.reconnected
        - 无活跃会话：要求客户端重新 configure
        """
        session = await voice_session_service.get_session(self.user_id)

        if not session:
            # 无活跃会话，要求重新配置
            await self._send_json({
                "type": "session.reconnect_failed",
                "data": {
                    "reason": "no_session",
                    "message": "会话已过期，请重新开始语音模式",
                },
            })
            return

        # 有活跃会话，重建上游 Gateway 连接
        if self._gateway and self._gateway.connected:
            await self._gateway.disconnect()

        self._gateway = GatewayClient(
            on_event=self._handle_gateway_event,
            user_id=self.user_id,
        )

        connected = await self._gateway.connect()
        if not connected:
            # 上游连接失败，清理会话
            await voice_session_service.close_session(self.user_id)
            await self._send_json({
                "type": "session.reconnect_failed",
                "data": {
                    "reason": "gateway_failed",
                    "message": "语音服务重连失败，请重新开始",
                },
            })
            return

        # 恢复上游配置
        mode = data.get("mode", "voice_chat")
        if mode not in ("voice_chat", "continuous_listen"):
            mode = "voice_chat"
        self._mode = mode
        is_continuous = mode == "continuous_listen"

        config = {
            "vad_enabled": True,
            "vad_threshold": data.get(
                "vad_threshold", settings.VOICE_VAD_THRESHOLD
            ),
            "speaker_identify": data.get("speaker_identify", False)
            or is_continuous,
            "speaker_threshold": data.get(
                "speaker_threshold", settings.VOICE_SPEAKER_THRESHOLD
            ),
            "auto_respond": not is_continuous,
            "audio_output": False,
            "model": "minicpm-o",
            "chunk_duration_ms": 30,
        }

        configured = await self._gateway.configure(config)
        if not configured:
            await self._gateway.disconnect()
            await voice_session_service.close_session(self.user_id)
            await self._send_json({
                "type": "session.reconnect_failed",
                "data": {
                    "reason": "configure_failed",
                    "message": "语音服务配置失败，请重新开始",
                },
            })
            return

        self._configured = True

        # 重启空闲检测
        if self._idle_check_task and not self._idle_check_task.done():
            self._idle_check_task.cancel()
        self._idle_check_task = asyncio.create_task(
            self._idle_timeout_loop()
        )

        await self._send_json({
            "type": "session.reconnected",
            "data": {
                "status": "ok",
                "session_id": self._gateway.session_id,
                "mode": self._mode,
            },
        })

        logger.info(
            "Voice session reconnected: user_id=%s, mode=%s",
            self.user_id,
            self._mode,
        )

    async def _handle_session_close(self) -> None:
        """处理 session.close：断开上游连接，清理状态"""
        logger.info("Voice session.close: user_id=%s", self.user_id)

        if self._gateway and self._gateway.connected:
            await self._gateway.disconnect()

        await voice_session_service.close_session(self.user_id)
        self._configured = False
        self._reset_response_state()

        await self._send_json({
            "type": "session.closed",
            "data": {"status": "ok"},
        })

    async def _handle_response_cancel(
        self, data: dict[str, Any]
    ) -> None:
        """处理 response.cancel：中断当前推理

        cancel 后 llmgateway 不发送 response.end，需主动清理状态。
        """
        response_id = data.get(
            "response_id", self._current_response_id
        )
        if not response_id:
            logger.warning(
                "Voice cancel no response_id: user_id=%s", self.user_id
            )
            return

        if not self._gateway or not self._gateway.connected:
            return

        # 转发 cancel 到 llmgateway
        await self._gateway.cancel_response(response_id)

        # 标记已取消，不再期望 response.end
        self._response_cancelled = True

        # 主动触发被打断的消息持久化
        await self._persist_interrupted_response()

        logger.info(
            "Voice response cancelled: user_id=%s, response_id=%s",
            self.user_id,
            response_id,
        )

    # ========== Binary 音频帧处理 ==========

    async def _handle_audio_frame(self, pcm_data: bytes) -> None:
        """透传 PCM16 音频帧到 llmgateway，同时缓存用于 STT"""
        if not self._configured:
            return

        if not self._gateway or not self._gateway.connected:
            return

        # 透传到 llmgateway
        await self._gateway.send_audio(pcm_data)

        # 如果有活跃的语音段，缓存音频帧用于 STT + 持久化
        if self._current_segment_id:
            await voice_session_service.cache_audio_chunk(
                self.user_id, self._current_segment_id, pcm_data
            )

        # 刷新会话 TTL
        await voice_session_service.refresh_session(self.user_id)

    # ========== llmgateway 事件处理 ==========

    async def _handle_gateway_event(
        self, event: dict[str, Any]
    ) -> None:
        """处理来自 llmgateway 的下行事件"""
        event_type = event.get("type")
        event_data = event.get("data", {})

        if event_type == "vad.speech_start":
            await self._on_vad_speech_start(event_data)
        elif event_type == "vad.speech_end":
            await self._on_vad_speech_end(event_data)
        elif event_type == "speaker.identified":
            await self._on_speaker_identified(event_data)
        elif event_type == "response.start":
            await self._on_response_start(event_data)
        elif event_type == "response.delta":
            await self._on_response_delta(event_data)
        elif event_type == "response.end":
            await self._on_response_end(event_data)
        elif event_type == "session.configured":
            # 上游配置确认（已在 _handle_session_configure 中处理）
            pass
        elif event_type == "error":
            await self._on_gateway_error(event_data)
        else:
            # 其他事件直接转发
            await self._send_json(event)

    async def _on_vad_speech_start(
        self, data: dict[str, Any]
    ) -> None:
        """VAD 检测到语音起始：生成 segment_id，通知客户端"""
        self._current_segment_id = str(uuid.uuid4())[:8]
        self._last_activity = time.time()

        # 标记活跃对话
        await voice_session_service.set_active_conversation(
            self.user_id
        )

        # 转发给客户端
        await self._send_json({
            "type": "vad.speech_start",
            "data": {
                **data,
                "segment_id": self._current_segment_id,
            },
        })

        logger.info(
            "VAD speech_start: user_id=%s, segment=%s",
            self.user_id,
            self._current_segment_id,
        )

    async def _on_vad_speech_end(
        self, data: dict[str, Any]
    ) -> None:
        """VAD 检测到语音结束：启动异步 STT 转写

        continuous_listen 模式下：STT 完成后交由决策服务决定是否回复。
        """
        segment_id = self._current_segment_id

        # 转发给客户端
        await self._send_json({
            "type": "vad.speech_end",
            "data": {
                **data,
                "segment_id": segment_id,
            },
        })

        # 启动异步 STT 转写
        if segment_id:
            await voice_session_service.start_stt_transcription(
                self.user_id, segment_id
            )

            # T042: continuous_listen 模式，等待 STT 结果后做决策
            if self._mode == "continuous_listen":
                asyncio.create_task(
                    self._continuous_listen_decision(segment_id),
                    name=f"cl_decision_{self.user_id}_{segment_id}",
                )

        logger.info(
            "VAD speech_end: user_id=%s, segment=%s, duration=%sms, "
            "mode=%s",
            self.user_id,
            segment_id,
            data.get("duration_ms"),
            self._mode,
        )

    async def _on_speaker_identified(
        self, data: dict[str, Any]
    ) -> None:
        """声纹识别结果（T036 完整声纹识别处理）

        处理 llmgateway 声纹识别事件：
        - identified=true 且匹配成功：设置说话人 ID + 消息归属用户
        - identified=true 但无映射 / identified=false：SPEAKER_NOT_FOUND，
          消息归属 unknown 用户
        - 将 speaker_id 加入 Redis recent_speakers 缓存（SADD + EXPIRE 60s）
        """
        identified = data.get("identified", False)
        gateway_speaker_id = data.get("speaker_id")

        if identified and gateway_speaker_id:
            # 通过 speaker_service 查找 SpeakerProfile 映射
            speaker_info = await speaker_service.identify_speaker(
                gateway_speaker_id
            )

            if speaker_info:
                # 匹配成功：设置说话人 ID，记录识别出的用户
                self._current_speaker_id = gateway_speaker_id
                self._identified_user_id = speaker_info["user_id"]

                # 在 data 中附加用户信息，转发给客户端
                data["user_id"] = speaker_info["user_id"]
                data["user_name"] = speaker_info["username"]

                await self._send_json({
                    "type": "speaker.identified",
                    "data": data,
                })

                logger.info(
                    "Speaker identified successfully: owner=%s, "
                    "speaker_id=%s, identified_user_id=%s, "
                    "username=%s, confidence=%s",
                    self.user_id,
                    gateway_speaker_id,
                    speaker_info["user_id"],
                    speaker_info["username"],
                    data.get("confidence"),
                )
            else:
                # 映射表无记录，归属 unknown 用户
                await self._assign_unknown_user()

                await self._send_error(
                    "SPEAKER_NOT_FOUND",
                    "声纹未注册，无法识别说话人",
                )
                await self._send_json({
                    "type": "speaker.identified",
                    "data": data,
                })

                logger.warning(
                    "Speaker not found in mapping: owner=%s, "
                    "speaker_id=%s, confidence=%s",
                    self.user_id,
                    gateway_speaker_id,
                    data.get("confidence"),
                )
        else:
            # identified=false，归属 unknown 用户
            await self._assign_unknown_user()

            await self._send_error(
                "SPEAKER_NOT_FOUND",
                "声纹识别失败，无法识别说话人",
            )
            await self._send_json({
                "type": "speaker.identified",
                "data": data,
            })

            logger.warning(
                "Speaker identification failed: owner=%s, "
                "identified=%s, speaker_id=%s, confidence=%s",
                self.user_id,
                identified,
                gateway_speaker_id,
                data.get("confidence"),
            )

        # 将 speaker_id 加入 Redis recent_speakers 缓存
        if gateway_speaker_id:
            try:
                redis_key = f"voice:recent_speakers:{self.user_id}"
                redis_client = await get_redis()
                try:
                    await redis_client.sadd(redis_key, gateway_speaker_id)
                    await redis_client.expire(redis_key, 60)
                finally:
                    await redis_client.aclose()
            except Exception as e:
                logger.warning(
                    "Failed to update recent_speakers cache: "
                    "user_id=%s, error=%s",
                    self.user_id,
                    e,
                )

    async def _assign_unknown_user(self) -> None:
        """将消息归属设为 unknown 用户

        查找 username="unknown" 的系统用户，若存在则设置
        _identified_user_id 为该用户 ID。
        """
        unknown_user = await user_repo.find_by_username("unknown")
        if unknown_user:
            self._identified_user_id = unknown_user.user_id
            logger.info(
                "Assigned to unknown user: user_id=%s",
                unknown_user.user_id,
            )
        else:
            # unknown 用户不存在，保持 _identified_user_id 为 None
            logger.warning(
                "Unknown user not found in database, "
                "messages will use owner user_id=%s",
                self.user_id,
            )

    async def _on_response_start(
        self, data: dict[str, Any]
    ) -> None:
        """推理开始：记录 response_id，重置累积内容

        C12 修复: voice_chat 模式下 auto_respond=true 时推理由
        llmgateway 自动触发，在此处检查频率限制。超限时主动取消推理。
        """
        response_id = data.get("response_id")

        # voice_chat 模式频率限制检查
        if self._mode == "voice_chat":
            allowed = await voice_session_service.check_llm_rate_limit(
                self.user_id
            )
            if not allowed and response_id and self._gateway:
                logger.warning(
                    "LLM rate limit exceeded in voice_chat mode, "
                    "cancelling response: user_id=%s, response_id=%s",
                    self.user_id,
                    response_id,
                )
                await self._gateway.send_json({
                    "type": "response.cancel",
                    "response_id": response_id,
                })
                await self._send_json({
                    "type": "error",
                    "data": {
                        "code": "LLM_RATE_LIMIT",
                        "message": "语音推理频率超限，请稍后再试",
                        "recoverable": True,
                    },
                })
                return

        self._current_response_id = response_id
        self._accumulated_content = ""
        self._response_start_time = time.time()
        self._response_cancelled = False

        # 转发给客户端
        await self._send_json({
            "type": "response.start",
            "data": data,
        })

        logger.info(
            "Response start: user_id=%s, response_id=%s",
            self.user_id,
            self._current_response_id,
        )

    async def _on_response_delta(
        self, data: dict[str, Any]
    ) -> None:
        """推理增量：累积内容并转发

        注意 data.delta.content 嵌套结构。
        """
        if self._response_cancelled:
            return

        # 提取文本增量（嵌套结构 data.delta.content）
        delta = data.get("delta", {})
        content = delta.get("content")

        if content:
            self._accumulated_content += content

        # 转发给客户端
        await self._send_json({
            "type": "response.delta",
            "data": data,
        })

    async def _on_response_end(
        self, data: dict[str, Any]
    ) -> None:
        """推理完成：触发消息持久化

        usage 包含 input_tokens/output_tokens/audio_duration_ms。
        """
        if self._response_cancelled:
            # cancel 后不应收到 response.end，忽略
            logger.warning(
                "Response.end after cancel: user_id=%s, response_id=%s",
                self.user_id,
                data.get("response_id"),
            )
            return

        response_id = data.get("response_id")
        usage = data.get("usage", {})

        # 计算响应时间
        response_time_ms = None
        if self._response_start_time:
            response_time_ms = int(
                (time.time() - self._response_start_time) * 1000
            )

        # 转发给客户端
        await self._send_json({
            "type": "response.end",
            "data": data,
        })

        # 触发消息持久化
        # T036: 如果声纹识别出了用户，使用识别出的 user_id 作为消息归属
        persist_user_id = self._identified_user_id or self.user_id
        segment_id = self._current_segment_id
        if segment_id and self._accumulated_content:
            result = await voice_session_service.persist_voice_message(
                user_id=persist_user_id,
                segment_id=segment_id,
                assistant_content=self._accumulated_content,
                speaker_id=self._current_speaker_id,
                response_usage=usage,
                response_time_ms=response_time_ms,
            )

            if result:
                # 发送 message.saved 事件通知客户端
                await self._send_json({
                    "type": "message.saved",
                    "data": {
                        "user_message_id": result.get(
                            "user_message_id"
                        ),
                        "user_message_uuid": result.get(
                            "user_message_uuid"
                        ),
                        "assistant_message_id": result.get(
                            "assistant_message_id"
                        ),
                        "assistant_message_uuid": result.get(
                            "assistant_message_uuid"
                        ),
                        "response_id": response_id,
                    },
                })

                # 检查 STT 是否已完成，发送 transcription 事件
                await self._check_and_send_transcription(
                    segment_id, result
                )

        # T043: 刷新活跃对话状态（AI 回复后保持活跃窗口 30s）
        await voice_session_service.set_active_conversation(self.user_id)

        logger.info(
            "Response end: user_id=%s, response_id=%s, "
            "content_len=%d, time=%sms, tokens=%s/%s",
            self.user_id,
            response_id,
            len(self._accumulated_content),
            response_time_ms,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )

        # 重置响应状态，准备下一轮
        self._reset_response_state()

    async def _on_gateway_error(
        self, data: dict[str, Any]
    ) -> None:
        """处理 llmgateway 错误事件

        T050: 将 llmgateway 错误映射到宪法 4.3 异常体系，
        提供标准化错误码和用户友好提示。
        """
        code = data.get("code", "UNKNOWN")
        message = data.get("message", "")
        recoverable = data.get("recoverable", True)

        # T050: 映射到宪法异常体系
        mapped = GatewayClient.map_gateway_error(
            code, message, recoverable
        )

        logger.warning(
            "Gateway error: user_id=%s, code=%s->%s, "
            "message=%s, retry=%s, recoverable=%s",
            self.user_id,
            code,
            mapped["mapped_code"],
            message,
            mapped["should_retry"],
            mapped["recoverable"],
        )

        # 发送映射后的标准化错误给客户端
        error_data = {
            "code": mapped["mapped_code"],
            "message": mapped["mapped_message"],
            "original_code": code,
            "recoverable": mapped["recoverable"],
            "should_retry": mapped["should_retry"],
            "max_retries": mapped["max_retries"],
        }
        if "retry_after" in mapped:
            error_data["retry_after"] = mapped["retry_after"]

        await self._send_json({
            "type": "error",
            "data": error_data,
        })

        # 不可恢复的错误，断开连接
        if not mapped["recoverable"]:
            await self._send_json({
                "type": "session.closed",
                "data": {
                    "status": "error",
                    "reason": mapped["mapped_message"],
                },
            })
            await self.close(code=4002)

    # ========== 辅助方法 ==========

    async def _persist_interrupted_response(self) -> None:
        """持久化被打断的回复（response.cancel 后主动调用）"""
        segment_id = self._current_segment_id
        if not segment_id:
            return

        # C7 修复: 即使 _accumulated_content 为空（cancel 发生在第一个 delta 之前），
        # 也应持久化用户消息和音频附件，仅 assistant 内容为空字符串

        # T036: 如果声纹识别出了用户，使用识别出的 user_id 作为消息归属
        persist_user_id = self._identified_user_id or self.user_id

        response_time_ms = None
        if self._response_start_time:
            response_time_ms = int(
                (time.time() - self._response_start_time) * 1000
            )

        result = await voice_session_service.persist_voice_message(
            user_id=persist_user_id,
            segment_id=segment_id,
            assistant_content=self._accumulated_content,
            speaker_id=self._current_speaker_id,
            response_time_ms=response_time_ms,
            is_interrupted=True,
        )

        if result:
            await self._send_json({
                "type": "message.saved",
                "data": {
                    "user_message_id": result.get(
                        "user_message_id"
                    ),
                    "user_message_uuid": result.get(
                        "user_message_uuid"
                    ),
                    "assistant_message_id": result.get(
                        "assistant_message_id"
                    ),
                    "assistant_message_uuid": result.get(
                        "assistant_message_uuid"
                    ),
                    "interrupted": True,
                },
            })

            # 检查 STT 并发送转写结果
            await self._check_and_send_transcription(
                segment_id, result
            )

        self._reset_response_state()

    async def _check_and_send_transcription(
        self,
        segment_id: str,
        persist_result: dict[str, Any],
    ) -> None:
        """检查 STT 转写状态并发送结果

        STT 可能先于或后于 response.end 完成。
        如果已完成，立即发送 transcription.complete。
        如果未完成，启动后台任务等待结果。
        """
        status = await voice_session_service.get_stt_status(
            self.user_id, segment_id
        )

        if status == "completed":
            text = await voice_session_service.get_stt_result(
                self.user_id, segment_id
            )
            if text:
                # 更新消息内容
                user_msg_id = persist_result.get("user_message_id")
                if user_msg_id:
                    await voice_session_service.update_message_content(
                        user_msg_id, text
                    )

                await self._send_json({
                    "type": "transcription.complete",
                    "data": {
                        "text": text,
                        "message_id": user_msg_id,
                        "segment_id": segment_id,
                    },
                })
        elif status == "failed":
            await self._send_json({
                "type": "transcription.failed",
                "data": {
                    "error": "语音转写失败",
                    "message_id": persist_result.get(
                        "user_message_id"
                    ),
                    "segment_id": segment_id,
                },
            })
        else:
            # STT 仍在进行中，启动后台等待任务
            asyncio.create_task(
                self._wait_for_stt(segment_id, persist_result),
                name=f"stt_wait_{self.user_id}_{segment_id}",
            )

    async def _wait_for_stt(
        self,
        segment_id: str,
        persist_result: dict[str, Any],
    ) -> None:
        """后台等待 STT 转写完成（最多 35 秒）"""
        max_wait = settings.VOICE_STT_TIMEOUT + 5
        start = time.time()

        while time.time() - start < max_wait:
            await asyncio.sleep(1)

            status = await voice_session_service.get_stt_status(
                self.user_id, segment_id
            )

            if status == "completed":
                text = await voice_session_service.get_stt_result(
                    self.user_id, segment_id
                )
                if text:
                    user_msg_id = persist_result.get(
                        "user_message_id"
                    )
                    if user_msg_id:
                        await voice_session_service.update_message_content(
                            user_msg_id, text
                        )

                    try:
                        await self._send_json({
                            "type": "transcription.complete",
                            "data": {
                                "text": text,
                                "message_id": user_msg_id,
                                "segment_id": segment_id,
                            },
                        })
                    except Exception:
                        pass  # 连接可能已断开
                return

            if status == "failed":
                try:
                    await self._send_json({
                        "type": "transcription.failed",
                        "data": {
                            "error": "语音转写失败",
                            "message_id": persist_result.get(
                                "user_message_id"
                            ),
                            "segment_id": segment_id,
                        },
                    })
                except Exception:
                    pass
                return

        # 超时
        logger.warning(
            "STT wait timeout: user_id=%s, segment=%s",
            self.user_id,
            segment_id,
        )
        try:
            await self._send_json({
                "type": "transcription.failed",
                "data": {
                    "error": "语音转写超时",
                    "message_id": persist_result.get(
                        "user_message_id"
                    ),
                    "segment_id": segment_id,
                },
            })
        except Exception:
            pass

    def _reset_response_state(self) -> None:
        """重置响应相关状态，准备下一轮对话"""
        self._current_response_id = None
        self._accumulated_content = ""
        self._response_start_time = None
        self._current_speaker_id = None
        self._response_cancelled = False
        # segment_id 保持到下一次 vad.speech_start 更新

    async def _idle_timeout_loop(self) -> None:
        """空闲超时检测循环

        60 秒未收到客户端消息时主动断开并清理状态。
        """
        idle_timeout = settings.VOICE_IDLE_TIMEOUT
        check_interval = 15  # 每 15 秒检查一次

        try:
            while True:
                await asyncio.sleep(check_interval)
                elapsed = time.time() - self._last_activity

                if elapsed >= idle_timeout:
                    logger.info(
                        "Voice idle timeout: user_id=%s, "
                        "idle=%ds, threshold=%ds",
                        self.user_id,
                        int(elapsed),
                        idle_timeout,
                    )
                    await self._send_json({
                        "type": "session.closed",
                        "data": {
                            "status": "idle_timeout",
                            "reason": "连接空闲超时",
                        },
                    })
                    await self.close(code=4003)
                    return

        except asyncio.CancelledError:
            pass

    # ========== T042: continuous_listen 决策 ==========

    async def _continuous_listen_decision(
        self, segment_id: str
    ) -> None:
        """continuous_listen 模式：等待 STT 结果后做响应决策

        决策结果：
        - RESPOND: 发送 input.commit 让 llmgateway 开始推理
        - RECORD_ONLY: 仅持久化消息，不触发推理
        - STOP: 发送 response.cancel 并通知客户端
        """
        # 等待 STT 完成（最多 STT_TIMEOUT + 5 秒）
        max_wait = settings.VOICE_STT_TIMEOUT + 5
        start = time.time()
        text = None

        while time.time() - start < max_wait:
            await asyncio.sleep(0.5)
            status = await voice_session_service.get_stt_status(
                self.user_id, segment_id
            )
            if status == "completed":
                text = await voice_session_service.get_stt_result(
                    self.user_id, segment_id
                )
                break
            if status == "failed":
                break

        if not text:
            logger.info(
                "CL decision: no STT text, skip: user_id=%s, "
                "segment=%s",
                self.user_id,
                segment_id,
            )
            return

        # 调用决策服务
        decision, reason = await response_decision_service.decide(
            transcription_text=text,
            speaker_id=self._current_speaker_id,
            user_id=self.user_id,
        )

        # 通知客户端决策结果
        await self._send_json({
            "type": "decision.result",
            "data": {
                "decision": decision.value,
                "reason": reason,
                "text": text,
                "segment_id": segment_id,
            },
        })

        if decision == DecisionResult.RESPOND:
            # T055a: LLM 推理频率限制检查（宪法 4.1：60次/分）
            allowed = await voice_session_service.check_llm_rate_limit(
                self.user_id
            )
            if not allowed:
                await self._send_error(
                    "LLM_RATE_LIMIT",
                    "语音推理请求过于频繁，请稍后再试",
                    recoverable=True,
                )
                logger.warning(
                    "CL decision RESPOND blocked by rate limit: "
                    "user_id=%s",
                    self.user_id,
                )
                return

            # 发送 input.commit 让 llmgateway 开始推理
            if self._gateway and self._gateway.connected:
                await self._gateway.send_json({
                    "type": "input.commit",
                })
            logger.info(
                "CL decision RESPOND: user_id=%s, text=%s",
                self.user_id,
                text[:30],
            )

        elif decision == DecisionResult.RECORD_ONLY:
            # 仅持久化用户消息（不触发推理，不创建 assistant 消息）
            persist_user_id = self._identified_user_id or self.user_id
            await voice_session_service.persist_voice_message(
                user_id=persist_user_id,
                segment_id=segment_id,
                assistant_content="",
                speaker_id=self._current_speaker_id,
                create_assistant=False,
            )
            logger.info(
                "CL decision RECORD_ONLY: user_id=%s, text=%s",
                self.user_id,
                text[:30],
            )

        elif decision == DecisionResult.STOP:
            # 取消当前推理（如果有）并通知客户端
            if (
                self._current_response_id
                and self._gateway
                and self._gateway.connected
            ):
                await self._gateway.cancel_response(
                    self._current_response_id
                )
                self._response_cancelled = True

            await self._send_json({
                "type": "session.stop_requested",
                "data": {"reason": reason},
            })
            logger.info(
                "CL decision STOP: user_id=%s, text=%s",
                self.user_id,
                text[:30],
            )

    async def _send_json(self, data: dict[str, Any]) -> None:
        """发送 JSON 消息到客户端

        连接已关闭时静默跳过，防止 disconnect 后 gateway 回调导致
        大量 "Cannot call send once a close message has been sent" 错误。
        """
        if self._closed:
            return
        try:
            await self.send(text_data=json.dumps(data, ensure_ascii=False))
        except Exception:
            # WebSocket 已关闭，标记并静默忽略
            self._closed = True

    async def _send_error(
        self,
        code: str,
        message: str,
        recoverable: bool = True,
    ) -> None:
        """发送错误消息到客户端"""
        await self._send_json({
            "type": "error",
            "data": {
                "code": code,
                "message": message,
                "recoverable": recoverable,
            },
        })
