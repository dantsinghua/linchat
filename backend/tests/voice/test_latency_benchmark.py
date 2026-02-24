"""
端到端语音延迟基准测试 (SC-001)

使用 channels.testing.WebsocketCommunicator 模拟完整语音流程，
测量从最后一帧 PCM16 音频到 response.start 的端到端延迟。

覆盖:
1. 语音到响应延迟（test_voice_to_response_latency）
2. 带网络延迟的语音到响应延迟（test_voice_to_response_with_network_delay）
3. 完整管道延迟（包含 STT 和 response.end）

测试方式: pytest-asyncio + channels.testing.WebsocketCommunicator + mock
非 CI 必跑，标记为 @pytest.mark.benchmark
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from channels.testing import WebsocketCommunicator

from apps.voice.consumers import VoiceConsumer


# ========== 辅助工具 ==========

# 统一 patch 路径前缀
_C = "apps.voice.consumers"


def _make_communicator(
    user_id=None, username="benchmark_user", query_string=b""
):
    """创建 WebsocketCommunicator，直接构造 scope 绕过中间件

    Args:
        user_id: 用户 ID（模拟 Cookie 认证成功时设置）
        username: 用户名
        query_string: URL query string

    Returns:
        WebsocketCommunicator 实例
    """
    app = VoiceConsumer.as_asgi()
    communicator = WebsocketCommunicator(
        app,
        "/ws/voice/",
    )
    if user_id is not None:
        communicator.scope["user_id"] = user_id
        communicator.scope["username"] = username
    if query_string:
        communicator.scope["query_string"] = query_string
    return communicator


async def _receive_json(communicator, timeout=2):
    """从 communicator 接收 JSON 消息"""
    response = await communicator.receive_from(timeout=timeout)
    return json.loads(response)


def _mock_redis_no_rate_limit():
    """构造 mock Redis 客户端，不触发频率限制"""
    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.sadd = AsyncMock(return_value=1)
    return mock_redis


def _mock_gateway(
    connect_ok=True, configure_ok=True, session_id="sess-bench-001"
):
    """构造 mock GatewayClient 实例"""
    gw = AsyncMock()
    gw.connect = AsyncMock(return_value=connect_ok)
    gw.configure = AsyncMock(return_value=configure_ok)
    gw.disconnect = AsyncMock()
    gw.send_audio = AsyncMock(return_value=True)
    gw.send_json = AsyncMock(return_value=True)
    gw.cancel_response = AsyncMock(return_value=True)
    gw.connected = connect_ok
    gw.session_id = session_id
    return gw


async def _setup_configured_session(communicator, MockGateway, gw):
    """辅助方法：完成 session.configure 流程

    Args:
        communicator: WebsocketCommunicator 实例
        MockGateway: GatewayClient mock 类
        gw: mock gateway 实例

    Returns:
        on_event 回调函数
    """
    connected, _ = await communicator.connect()
    assert connected is True

    # 发送 session.configure
    await communicator.send_to(
        text_data=json.dumps({
            "type": "session.configure",
            "data": {"mode": "voice_chat"},
        })
    )

    # 接收 session.configured
    resp = await _receive_json(communicator)
    assert resp["type"] == "session.configured"
    assert resp["data"]["status"] == "ok"

    # 获取 on_event 回调
    on_event = MockGateway.call_args[1]["on_event"]
    return on_event


# ========== 1. 语音到响应延迟测试 ==========


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestVoiceToResponseLatency:
    """测量从最后一帧 PCM16 音频到收到 response.start 的延迟

    SC-001 验收标准：端到端延迟 < 5 秒
    """

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_voice_to_response_latency(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """测量从最后一帧音频发送到 response.start 接收的延迟

        流程:
        1. session.configure 建立会话
        2. 发送多帧 PCM16 音频（模拟 300ms 语音）
        3. mock llmgateway 触发 response.start 回调
        4. 测量从最后一帧到 response.start 的延迟

        断言延迟 < 5 秒 (SC-001)
        """
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=100)
        on_event = await _setup_configured_session(
            communicator, MockGateway, gw
        )

        # 触发 vad.speech_start 生成 segment_id
        await on_event({
            "type": "vad.speech_start",
            "data": {"timestamp": 1000},
        })
        await _receive_json(communicator)  # vad.speech_start

        # 发送 10 帧 PCM16 音频（模拟 ~300ms 16kHz 语音）
        pcm_frame = b"\x00\x01" * 480  # 30ms 帧 (16kHz * 0.03s * 2 bytes)
        for _ in range(10):
            await communicator.send_to(bytes_data=pcm_frame)

        # 等待音频帧处理完成
        await asyncio.sleep(0.05)

        # 记录最后一帧发送时间
        last_frame_time = time.monotonic()

        # 模拟 llmgateway 触发 response.start（模拟 Gateway 处理延迟）
        await asyncio.sleep(0.01)  # 最小处理延迟
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-latency-001"},
        })

        # 接收 response.start
        resp = await _receive_json(communicator, timeout=5)
        response_start_time = time.monotonic()

        assert resp["type"] == "response.start"
        assert resp["data"]["response_id"] == "resp-latency-001"

        # 计算延迟
        latency_ms = (response_start_time - last_frame_time) * 1000
        print(
            f"\n[基准测试] 语音→响应延迟: {latency_ms:.2f}ms "
            f"(SC-001 阈值: 5000ms)"
        )

        # SC-001: 端到端延迟 < 5 秒
        assert latency_ms < 5000, (
            f"端到端延迟 {latency_ms:.2f}ms 超过 SC-001 "
            f"阈值 5000ms"
        )

        # 验证音频帧确实被转发
        assert gw.send_audio.call_count == 10
        mock_session_svc.refresh_session.assert_called()

        await communicator.disconnect()


# ========== 2. 带网络延迟的语音到响应测试 ==========


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestVoiceToResponseWithNetworkDelay:
    """模拟网络延迟场景下的端到端延迟测试"""

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_voice_to_response_with_network_delay(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """模拟 Gateway 有 500ms 网络延迟的场景

        流程:
        1. session.configure 建立会话
        2. 发送多帧 PCM16 音频
        3. 模拟 500ms Gateway 处理+网络延迟后触发 response.start
        4. 验证总延迟仍 < 5 秒 (SC-001)

        此测试确保在较差网络条件下系统仍满足延迟要求。
        """
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=101)
        on_event = await _setup_configured_session(
            communicator, MockGateway, gw
        )

        # 触发 vad.speech_start
        await on_event({
            "type": "vad.speech_start",
            "data": {"timestamp": 2000},
        })
        await _receive_json(communicator)  # vad.speech_start

        # 发送 20 帧 PCM16 音频（模拟 ~600ms 语音）
        pcm_frame = b"\x00\x01" * 480
        for _ in range(20):
            await communicator.send_to(bytes_data=pcm_frame)

        await asyncio.sleep(0.05)

        # 记录最后一帧时间
        last_frame_time = time.monotonic()

        # 模拟 500ms Gateway 处理 + 网络往返延迟
        await asyncio.sleep(0.5)

        # Gateway 触发 response.start
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-netdelay-001"},
        })

        resp = await _receive_json(communicator, timeout=5)
        response_start_time = time.monotonic()

        assert resp["type"] == "response.start"

        latency_ms = (response_start_time - last_frame_time) * 1000
        print(
            f"\n[基准测试] 含网络延迟的语音→响应延迟: {latency_ms:.2f}ms "
            f"(模拟 500ms 网络延迟, SC-001 阈值: 5000ms)"
        )

        # SC-001: 即使有 500ms 网络延迟，总延迟仍应 < 5 秒
        assert latency_ms < 5000, (
            f"含网络延迟的端到端延迟 {latency_ms:.2f}ms 超过 SC-001 "
            f"阈值 5000ms"
        )

        # 验证所有帧都被转发
        assert gw.send_audio.call_count == 20

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_voice_to_response_with_high_network_delay(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """模拟 2000ms 高网络延迟的极端场景

        此测试模拟极端网络条件，验证 SC-001 5 秒阈值的边界。
        """
        mock_get_redis.return_value = _mock_redis_no_rate_limit()
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=102)
        on_event = await _setup_configured_session(
            communicator, MockGateway, gw
        )

        # 触发 vad.speech_start
        await on_event({
            "type": "vad.speech_start",
            "data": {"timestamp": 3000},
        })
        await _receive_json(communicator)

        # 发送 5 帧音频
        pcm_frame = b"\x00\x01" * 480
        for _ in range(5):
            await communicator.send_to(bytes_data=pcm_frame)

        await asyncio.sleep(0.05)
        last_frame_time = time.monotonic()

        # 模拟 2000ms 极端网络延迟
        await asyncio.sleep(2.0)

        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-highdelay-001"},
        })

        resp = await _receive_json(communicator, timeout=5)
        response_start_time = time.monotonic()

        assert resp["type"] == "response.start"

        latency_ms = (response_start_time - last_frame_time) * 1000
        print(
            f"\n[基准测试] 高网络延迟场景: {latency_ms:.2f}ms "
            f"(模拟 2000ms 网络延迟, SC-001 阈值: 5000ms)"
        )

        # SC-001: 2 秒网络延迟 + 处理时间，总延迟仍应 < 5 秒
        assert latency_ms < 5000, (
            f"高网络延迟端到端延迟 {latency_ms:.2f}ms 超过 SC-001 "
            f"阈值 5000ms"
        )

        await communicator.disconnect()


# ========== 3. 完整管道延迟测试 ==========


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestFullPipelineLatency:
    """完整管道延迟：音频发送 → response.start → delta → response.end

    包含 STT 和持久化步骤的完整端到端测试。
    """

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_full_pipeline_latency(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """完整管道延迟测试：音频 → VAD → response.start → delta → end → 持久化

        流程:
        1. session.configure 建立会话
        2. vad.speech_start → 发送多帧音频 → vad.speech_end
        3. Gateway 触发 response.start → response.delta → response.end
        4. 验证持久化被调用，测量全管道延迟

        分别测量:
        - 首 token 延迟（last_frame → response.start）
        - 全管道延迟（last_frame → response.end 后 message.saved）
        """
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.start_stt_transcription = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value={
                "user_message_id": 501,
                "user_message_uuid": "uuid-bench-501",
                "assistant_message_id": 502,
                "assistant_message_uuid": "uuid-bench-502",
            }
        )
        # STT 尚未完成（后台等待）
        mock_session_svc.get_stt_status = AsyncMock(return_value=None)

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=200)
        on_event = await _setup_configured_session(
            communicator, MockGateway, gw
        )

        # === 阶段 1: 语音输入 ===

        # vad.speech_start
        await on_event({
            "type": "vad.speech_start",
            "data": {"timestamp": 5000},
        })
        start_resp = await _receive_json(communicator)
        assert start_resp["type"] == "vad.speech_start"
        segment_id = start_resp["data"]["segment_id"]
        assert len(segment_id) == 8

        # 发送 15 帧 PCM16 音频（模拟 ~450ms 语音）
        pcm_frame = b"\x00\x01" * 480
        for _ in range(15):
            await communicator.send_to(bytes_data=pcm_frame)

        await asyncio.sleep(0.05)

        # vad.speech_end
        await on_event({
            "type": "vad.speech_end",
            "data": {"duration_ms": 450},
        })
        end_resp = await _receive_json(communicator)
        assert end_resp["type"] == "vad.speech_end"
        assert end_resp["data"]["segment_id"] == segment_id

        # 记录最后一帧时间点（以 speech_end 作为参考）
        last_frame_time = time.monotonic()

        # === 阶段 2: Gateway 推理 ===

        # 模拟 Gateway 处理延迟（VAD 检测 + 模型加载）
        await asyncio.sleep(0.1)

        # response.start
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-pipeline-001"},
        })
        resp_start = await _receive_json(communicator, timeout=5)
        first_token_time = time.monotonic()

        assert resp_start["type"] == "response.start"

        # 首 token 延迟
        first_token_latency_ms = (
            (first_token_time - last_frame_time) * 1000
        )

        # 模拟多个 response.delta（流式输出）
        delta_texts = ["你好", "，", "这是", "一段", "测试回复"]
        for text in delta_texts:
            await on_event({
                "type": "response.delta",
                "data": {"delta": {"content": text}},
            })
            resp_delta = await _receive_json(communicator)
            assert resp_delta["type"] == "response.delta"
            # 模拟流式输出间隔
            await asyncio.sleep(0.02)

        # response.end
        await on_event({
            "type": "response.end",
            "data": {
                "response_id": "resp-pipeline-001",
                "usage": {
                    "input_tokens": 150,
                    "output_tokens": 30,
                    "audio_duration_ms": 450,
                },
            },
        })

        # response.end 转发
        resp_end = await _receive_json(communicator, timeout=5)
        assert resp_end["type"] == "response.end"
        assert resp_end["data"]["response_id"] == "resp-pipeline-001"

        # message.saved（持久化完成通知）
        resp_saved = await _receive_json(communicator, timeout=5)
        pipeline_end_time = time.monotonic()

        assert resp_saved["type"] == "message.saved"
        assert resp_saved["data"]["user_message_id"] == 501
        assert resp_saved["data"]["assistant_message_id"] == 502
        assert resp_saved["data"]["response_id"] == "resp-pipeline-001"

        # === 阶段 3: 延迟计算 ===

        full_pipeline_latency_ms = (
            (pipeline_end_time - last_frame_time) * 1000
        )

        print(
            f"\n[基准测试] 完整管道延迟:"
            f"\n  首 token 延迟: {first_token_latency_ms:.2f}ms"
            f"\n  全管道延迟: {full_pipeline_latency_ms:.2f}ms"
            f"\n  (SC-001 阈值: 5000ms)"
        )

        # SC-001: 首 token 延迟 < 5 秒
        assert first_token_latency_ms < 5000, (
            f"首 token 延迟 {first_token_latency_ms:.2f}ms 超过 "
            f"SC-001 阈值 5000ms"
        )

        # 全管道延迟（含 delta 传输和持久化）应在合理范围内
        # 由于包含多次 delta 和持久化，允许稍长但仍应 < 5 秒
        assert full_pipeline_latency_ms < 5000, (
            f"全管道延迟 {full_pipeline_latency_ms:.2f}ms 超过 "
            f"5000ms 阈值"
        )

        # === 阶段 4: 验证 ===

        # 验证持久化被调用
        mock_session_svc.persist_voice_message.assert_called_once()
        call_kwargs = (
            mock_session_svc.persist_voice_message.call_args[1]
        )
        assert call_kwargs["user_id"] == 200
        assert call_kwargs["assistant_content"] == "你好，这是一段测试回复"
        assert call_kwargs["segment_id"] == segment_id

        # 验证 STT 转写已启动
        mock_session_svc.start_stt_transcription.assert_called_once_with(
            200, segment_id
        )

        # 验证音频帧转发
        assert gw.send_audio.call_count == 15

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_full_pipeline_with_stt_complete(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """完整管道延迟测试（含 STT 结果返回）

        在 response.end 时 STT 已完成，验证 transcription.complete
        事件也在延迟阈值内发送。
        """
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.start_stt_transcription = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )
        mock_session_svc.persist_voice_message = AsyncMock(
            return_value={
                "user_message_id": 601,
                "user_message_uuid": "uuid-bench-601",
                "assistant_message_id": 602,
                "assistant_message_uuid": "uuid-bench-602",
            }
        )
        # STT 已完成
        mock_session_svc.get_stt_status = AsyncMock(
            return_value="completed"
        )
        mock_session_svc.get_stt_result = AsyncMock(
            return_value="这是语音转写文本"
        )
        mock_session_svc.update_message_content = AsyncMock()

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=201)
        on_event = await _setup_configured_session(
            communicator, MockGateway, gw
        )

        # vad.speech_start
        await on_event({
            "type": "vad.speech_start",
            "data": {},
        })
        start_resp = await _receive_json(communicator)
        segment_id = start_resp["data"]["segment_id"]

        # 发送 10 帧音频
        pcm_frame = b"\x00\x01" * 480
        for _ in range(10):
            await communicator.send_to(bytes_data=pcm_frame)
        await asyncio.sleep(0.05)

        # vad.speech_end
        await on_event({
            "type": "vad.speech_end",
            "data": {"duration_ms": 300},
        })
        await _receive_json(communicator)  # vad.speech_end

        last_frame_time = time.monotonic()

        # 模拟 Gateway 处理
        await asyncio.sleep(0.1)

        # 完整推理流程
        await on_event({
            "type": "response.start",
            "data": {"response_id": "resp-stt-bench"},
        })
        await _receive_json(communicator)  # response.start

        await on_event({
            "type": "response.delta",
            "data": {"delta": {"content": "回复文本"}},
        })
        await _receive_json(communicator)  # response.delta

        await on_event({
            "type": "response.end",
            "data": {
                "response_id": "resp-stt-bench",
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 15,
                },
            },
        })

        # response.end
        resp_end = await _receive_json(communicator, timeout=5)
        assert resp_end["type"] == "response.end"

        # message.saved
        resp_saved = await _receive_json(communicator, timeout=5)
        assert resp_saved["type"] == "message.saved"

        # transcription.complete（STT 已完成时会立即发送）
        resp_trans = await _receive_json(communicator, timeout=5)
        transcription_time = time.monotonic()

        assert resp_trans["type"] == "transcription.complete"
        assert resp_trans["data"]["text"] == "这是语音转写文本"
        assert resp_trans["data"]["message_id"] == 601

        # 含 STT 结果的全管道延迟
        total_latency_ms = (
            (transcription_time - last_frame_time) * 1000
        )
        print(
            f"\n[基准测试] 含 STT 的完整管道延迟: {total_latency_ms:.2f}ms "
            f"(SC-001 阈值: 5000ms)"
        )

        # SC-001: 含 STT 的完整管道延迟 < 5 秒
        assert total_latency_ms < 5000, (
            f"含 STT 的全管道延迟 {total_latency_ms:.2f}ms 超过 "
            f"5000ms 阈值"
        )

        # 验证消息内容已更新
        mock_session_svc.update_message_content.assert_called_once_with(
            601, "这是语音转写文本"
        )

        await communicator.disconnect()

    @patch(f"{_C}.get_redis")
    @patch(f"{_C}.voice_session_service")
    @patch(f"{_C}.GatewayClient")
    async def test_full_pipeline_multiple_rounds(
        self, MockGateway, mock_session_svc, mock_get_redis
    ):
        """多轮连续语音交互延迟测试

        模拟 3 轮连续语音交互，验证每轮延迟都满足 SC-001。
        确保状态正确重置，不存在累积延迟。
        """
        mock_redis = _mock_redis_no_rate_limit()
        mock_get_redis.return_value = mock_redis
        mock_session_svc.create_session = AsyncMock(return_value=True)
        mock_session_svc.update_session = AsyncMock()
        mock_session_svc.close_session = AsyncMock()
        mock_session_svc.refresh_session = AsyncMock()
        mock_session_svc.cache_audio_chunk = AsyncMock()
        mock_session_svc.set_active_conversation = AsyncMock()
        mock_session_svc.start_stt_transcription = AsyncMock()
        mock_session_svc.check_llm_rate_limit = AsyncMock(
            return_value=False
        )
        mock_session_svc.get_stt_status = AsyncMock(return_value=None)

        # 每轮返回不同的持久化结果
        persist_results = [
            {
                "user_message_id": 701 + i * 2,
                "user_message_uuid": f"uuid-round-{i}-user",
                "assistant_message_id": 702 + i * 2,
                "assistant_message_uuid": f"uuid-round-{i}-asst",
            }
            for i in range(3)
        ]
        mock_session_svc.persist_voice_message = AsyncMock(
            side_effect=persist_results
        )

        gw = _mock_gateway()
        MockGateway.return_value = gw

        communicator = _make_communicator(user_id=202)
        on_event = await _setup_configured_session(
            communicator, MockGateway, gw
        )

        round_latencies = []

        for round_num in range(3):
            # vad.speech_start
            await on_event({
                "type": "vad.speech_start",
                "data": {"timestamp": (round_num + 1) * 10000},
            })
            start_resp = await _receive_json(communicator)
            assert start_resp["type"] == "vad.speech_start"

            # 发送音频帧
            pcm_frame = b"\x00\x01" * 480
            for _ in range(8):
                await communicator.send_to(bytes_data=pcm_frame)
            await asyncio.sleep(0.03)

            # vad.speech_end
            await on_event({
                "type": "vad.speech_end",
                "data": {"duration_ms": 240},
            })
            await _receive_json(communicator)  # vad.speech_end

            last_frame_time = time.monotonic()

            # response.start
            await asyncio.sleep(0.05)
            resp_id = f"resp-round-{round_num}"
            await on_event({
                "type": "response.start",
                "data": {"response_id": resp_id},
            })
            resp = await _receive_json(communicator, timeout=5)
            first_token_time = time.monotonic()
            assert resp["type"] == "response.start"

            latency_ms = (first_token_time - last_frame_time) * 1000
            round_latencies.append(latency_ms)

            # response.delta
            await on_event({
                "type": "response.delta",
                "data": {"delta": {"content": f"第{round_num+1}轮回复"}},
            })
            await _receive_json(communicator)

            # response.end
            await on_event({
                "type": "response.end",
                "data": {
                    "response_id": resp_id,
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 10,
                    },
                },
            })
            await _receive_json(communicator)  # response.end
            await _receive_json(communicator)  # message.saved

        # 输出各轮延迟
        print("\n[基准测试] 多轮语音交互延迟:")
        for i, lat in enumerate(round_latencies):
            print(
                f"  第 {i+1} 轮: {lat:.2f}ms"
            )
        print(f"  SC-001 阈值: 5000ms")

        # 验证每轮延迟都 < 5 秒
        for i, lat in enumerate(round_latencies):
            assert lat < 5000, (
                f"第 {i+1} 轮延迟 {lat:.2f}ms 超过 SC-001 "
                f"阈值 5000ms"
            )

        # 验证没有累积延迟（后续轮次不应显著慢于第一轮）
        if len(round_latencies) >= 2:
            max_degradation = max(round_latencies) / min(round_latencies)
            print(
                f"  延迟退化比: {max_degradation:.2f}x"
            )
            assert max_degradation < 3.0, (
                f"检测到累积延迟退化: "
                f"最大/最小延迟比 = {max_degradation:.2f}x"
            )

        # 验证 3 轮持久化都被调用
        assert mock_session_svc.persist_voice_message.call_count == 3

        await communicator.disconnect()
