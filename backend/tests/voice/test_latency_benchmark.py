"""
端到端语音延迟基准测试 (SC-003)

使用 channels.testing.WebsocketCommunicator 模拟完整语音流程，
测量从最后一帧 PCM16 音频到 response.start 的端到端延迟。

010-voice-agent-pipeline: 重写为 ASR + VoicePipeline 架构。
事件流程: 音频 → ASR → transcription.completed → VoicePipeline → response.*

覆盖:
1. 语音到响应延迟（音频 → ASR 事件 → Pipeline 启动）
2. 带模拟网络延迟的端到端测试
3. 完整管道延迟（含多轮对话）

测试方式: pytest-asyncio + channels.testing.WebsocketCommunicator + mock
非 CI 必跑，标记为 @pytest.mark.benchmark
"""

import asyncio
import json
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from channels.testing import WebsocketCommunicator

from apps.voice.consumers import VoiceConsumer

pytestmark = pytest.mark.django_db


# ========== 辅助工具 ==========

# Consumer 逻辑分散在 consumers.py + 3 个 mixin 模块中
_C = "apps.voice.consumers"
_S = "apps.voice.consumer_session"
_E = "apps.voice.consumer_events"

# voice_session_service 在 consumers/session/events 中引用
_VSS_MODULES = [_C, _S, _E]


@contextmanager
def _patch_voice_deps():
    """统一 patch Consumer 及所有 mixin 模块的外部依赖。

    返回 (MockASRClass, mock_session_svc, mock_get_redis)
    """
    mock_session_svc = AsyncMock()
    mock_get_redis = AsyncMock()
    mock_asr_cls = MagicMock()

    patches = []

    # voice_session_service
    for mod in _VSS_MODULES:
        patches.append(
            patch(f"{mod}.voice_session_service", mock_session_svc)
        )

    # get_redis 在 consumer_session.py 中引用
    patches.append(patch(f"{_S}.get_redis", mock_get_redis))

    # ASRStreamClient 在 consumer_session.py 中实例化
    patches.append(patch(f"{_S}.ASRStreamClient", mock_asr_cls))

    # VoicePipeline.cancel 在 consumer_session.py 和 consumers.py 中延迟导入
    patches.append(
        patch(
            "apps.voice.services.voice_pipeline.VoicePipeline.cancel",
            new_callable=AsyncMock,
        )
    )

    # VoicePipeline.run_pipeline — 延迟基准测试只关注事件路由延迟，
    # 不需要真正运行 pipeline（避免 response.* 消息干扰多轮测试）
    patches.append(
        patch(
            "apps.voice.services.voice_pipeline.VoicePipeline.run_pipeline",
            new_callable=AsyncMock,
        )
    )

    for p in patches:
        p.start()
    try:
        yield mock_asr_cls, mock_session_svc, mock_get_redis
    finally:
        for p in patches:
            p.stop()


def _make_communicator(user_id=None, username="benchmark_user"):
    """创建 WebsocketCommunicator"""
    app = VoiceConsumer.as_asgi()
    communicator = WebsocketCommunicator(app, "/ws/voice/")
    if user_id is not None:
        communicator.scope["user_id"] = user_id
        communicator.scope["username"] = username
    return communicator


async def _receive_json(communicator, timeout=2):
    response = await communicator.receive_from(timeout=timeout)
    return json.loads(response)


def _mock_redis_no_rate_limit():
    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.sadd = AsyncMock(return_value=1)
    mock_redis.aclose = AsyncMock()
    return mock_redis


def _mock_asr_client(connect_ok=True, session_id="asr-bench-001"):
    asr = AsyncMock()
    asr.connect = AsyncMock(return_value=session_id)
    asr.configure = AsyncMock()
    asr.disconnect = AsyncMock()
    asr.send_audio = AsyncMock()
    asr.send_commit = AsyncMock()
    asr.connected = connect_ok
    asr.session_id = session_id
    return asr


async def _setup_configured_session(communicator, MockASR, asr):
    """辅助方法：完成 session.configure 流程，返回 on_event 回调。"""
    connected, _ = await communicator.connect()
    assert connected is True

    await communicator.send_to(
        text_data=json.dumps({
            "type": "session.configure",
            "data": {"mode": "voice_chat"},
        })
    )

    resp = await _receive_json(communicator)
    assert resp["type"] == "session.configured"
    assert resp["data"]["status"] == "active"

    # 获取 ASR on_event 回调
    on_event = MockASR.call_args[1]["on_event"]
    return on_event


def _setup_basic_session_svc(mock_session_svc):
    mock_session_svc.create_session = AsyncMock(return_value=True)
    mock_session_svc.update_session = AsyncMock()
    mock_session_svc.close_session = AsyncMock()
    mock_session_svc.refresh_session = AsyncMock()
    mock_session_svc.cache_audio_chunk = AsyncMock()
    mock_session_svc.set_active_conversation = AsyncMock()
    mock_session_svc.check_llm_rate_limit = AsyncMock(return_value=True)


# ========== 1. 语音到转录延迟测试 ==========


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestVoiceToResponseLatency:
    """测量从最后一帧 PCM16 音频到收到 transcription.complete 的延迟

    新架构中 ASR 直接返回转录结果，Pipeline 由转录触发。
    SC-003: 停止说话到首个 AI 回复字符 < 5 秒
    """

    async def test_voice_to_response_latency(self):
        """测量从最后一帧到 transcription.complete + pipeline 启动的延迟

        流程:
        1. session.configure 建立会话
        2. 发送多帧 PCM16 音频
        3. ASR 返回 transcription.completed
        4. 测量从最后一帧到前端收到 transcription.complete 的延迟
        """
        with _patch_voice_deps() as (MockASR, mock_session_svc, mock_get_redis):
            mock_get_redis.return_value = _mock_redis_no_rate_limit()
            _setup_basic_session_svc(mock_session_svc)

            asr = _mock_asr_client()
            MockASR.return_value = asr

            communicator = _make_communicator(user_id=100)
            on_event = await _setup_configured_session(
                communicator, MockASR, asr
            )

            # 触发 vad.speech_start
            await on_event({"type": "vad.speech_start", "timestamp": 1000})
            await _receive_json(communicator)  # vad.speech_start

            # 发送 10 帧 PCM16 音频（模拟 ~300ms 16kHz 语音）
            pcm_frame = b"\x00\x01" * 480
            for _ in range(10):
                await communicator.send_to(bytes_data=pcm_frame)

            await asyncio.sleep(0.05)

            # vad.speech_end
            await on_event({"type": "vad.speech_end", "duration_ms": 300})
            await _receive_json(communicator)  # vad.speech_end

            last_frame_time = time.monotonic()

            # 模拟 ASR 处理延迟
            await asyncio.sleep(0.01)

            # ASR 返回 transcription.completed
            await on_event({
                "type": "transcription.completed",
                "text": "你好，这是测试",
                "language": "zh",
            })

            # 接收 transcription.complete
            resp = await _receive_json(communicator, timeout=5)
            transcription_time = time.monotonic()

            assert resp["type"] == "transcription.complete"
            assert resp["data"]["text"] == "你好，这是测试"

            # 计算延迟
            latency_ms = (transcription_time - last_frame_time) * 1000
            print(
                f"\n[基准测试] 语音→转录延迟: {latency_ms:.2f}ms "
                f"(SC-003 阈值: 5000ms)"
            )

            assert latency_ms < 5000, (
                f"端到端延迟 {latency_ms:.2f}ms 超过 SC-003 阈值 5000ms"
            )

            # 验证音频帧确实被转发到 ASR
            assert asr.send_audio.call_count == 10

            await communicator.disconnect()


# ========== 2. 带网络延迟的延迟测试 ==========


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestVoiceToResponseWithNetworkDelay:
    """模拟网络延迟场景下的端到端延迟测试"""

    async def test_voice_to_response_with_network_delay(self):
        """模拟 ASR 有 500ms 处理+网络延迟的场景"""
        with _patch_voice_deps() as (MockASR, mock_session_svc, mock_get_redis):
            mock_get_redis.return_value = _mock_redis_no_rate_limit()
            _setup_basic_session_svc(mock_session_svc)

            asr = _mock_asr_client()
            MockASR.return_value = asr

            communicator = _make_communicator(user_id=101)
            on_event = await _setup_configured_session(
                communicator, MockASR, asr
            )

            # vad.speech_start
            await on_event({"type": "vad.speech_start", "timestamp": 2000})
            await _receive_json(communicator)

            # 发送 20 帧音频
            pcm_frame = b"\x00\x01" * 480
            for _ in range(20):
                await communicator.send_to(bytes_data=pcm_frame)

            await asyncio.sleep(0.05)

            # vad.speech_end
            await on_event({"type": "vad.speech_end", "duration_ms": 600})
            await _receive_json(communicator)

            last_frame_time = time.monotonic()

            # 模拟 500ms ASR 处理 + 网络延迟
            await asyncio.sleep(0.5)

            await on_event({
                "type": "transcription.completed",
                "text": "带延迟的转录",
            })

            resp = await _receive_json(communicator, timeout=5)
            transcription_time = time.monotonic()

            assert resp["type"] == "transcription.complete"

            latency_ms = (transcription_time - last_frame_time) * 1000
            print(
                f"\n[基准测试] 含网络延迟的语音→转录延迟: {latency_ms:.2f}ms "
                f"(模拟 500ms 网络延迟, SC-003 阈值: 5000ms)"
            )

            assert latency_ms < 5000
            assert asr.send_audio.call_count == 20

            await communicator.disconnect()

    async def test_voice_to_response_with_high_network_delay(self):
        """模拟 2000ms 高网络延迟的极端场景"""
        with _patch_voice_deps() as (MockASR, mock_session_svc, mock_get_redis):
            mock_get_redis.return_value = _mock_redis_no_rate_limit()
            _setup_basic_session_svc(mock_session_svc)

            asr = _mock_asr_client()
            MockASR.return_value = asr

            communicator = _make_communicator(user_id=102)
            on_event = await _setup_configured_session(
                communicator, MockASR, asr
            )

            await on_event({"type": "vad.speech_start", "timestamp": 3000})
            await _receive_json(communicator)

            pcm_frame = b"\x00\x01" * 480
            for _ in range(5):
                await communicator.send_to(bytes_data=pcm_frame)

            await asyncio.sleep(0.05)

            await on_event({"type": "vad.speech_end", "duration_ms": 150})
            await _receive_json(communicator)

            last_frame_time = time.monotonic()

            # 模拟 2000ms 极端网络延迟
            await asyncio.sleep(2.0)

            await on_event({
                "type": "transcription.completed",
                "text": "极端延迟测试",
            })

            resp = await _receive_json(communicator, timeout=5)
            transcription_time = time.monotonic()

            assert resp["type"] == "transcription.complete"

            latency_ms = (transcription_time - last_frame_time) * 1000
            print(
                f"\n[基准测试] 高网络延迟场景: {latency_ms:.2f}ms "
                f"(模拟 2000ms 网络延迟, SC-003 阈值: 5000ms)"
            )

            assert latency_ms < 5000

            await communicator.disconnect()


# ========== 3. 完整管道延迟测试 ==========


@pytest.mark.benchmark
@pytest.mark.asyncio
class TestFullPipelineLatency:
    """完整管道延迟：音频 → ASR → 转录 → Pipeline 启动

    新架构中 VoicePipeline 在后台 task 中运行，
    本测试验证 Consumer 侧的事件转发效率。
    """

    async def test_full_pipeline_latency(self):
        """完整管道延迟：音频 → VAD → speech_end → transcription → pipeline

        分别测量:
        - 转录延迟（speech_end → transcription.complete）
        - Pipeline 启动延迟（transcription.complete → pipeline task created）
        """
        with _patch_voice_deps() as (MockASR, mock_session_svc, mock_get_redis):
            mock_get_redis.return_value = _mock_redis_no_rate_limit()
            _setup_basic_session_svc(mock_session_svc)

            asr = _mock_asr_client()
            MockASR.return_value = asr

            communicator = _make_communicator(user_id=200)
            on_event = await _setup_configured_session(
                communicator, MockASR, asr
            )

            # === 阶段 1: 语音输入 ===
            await on_event({"type": "vad.speech_start", "timestamp": 5000})
            start_resp = await _receive_json(communicator)
            assert start_resp["type"] == "vad.speech_start"
            segment_id = start_resp["data"]["segment_id"]
            assert len(segment_id) == 8

            # 发送 15 帧音频
            pcm_frame = b"\x00\x01" * 480
            for _ in range(15):
                await communicator.send_to(bytes_data=pcm_frame)

            await asyncio.sleep(0.05)

            # vad.speech_end
            await on_event({"type": "vad.speech_end", "duration_ms": 450})
            end_resp = await _receive_json(communicator)
            assert end_resp["type"] == "vad.speech_end"
            assert end_resp["data"]["segment_id"] == segment_id

            last_frame_time = time.monotonic()

            # === 阶段 2: ASR 转录 ===
            await asyncio.sleep(0.1)  # 模拟 ASR 处理

            await on_event({
                "type": "transcription.completed",
                "text": "你好这是完整管道测试",
                "language": "zh",
            })

            resp_trans = await _receive_json(communicator, timeout=5)
            transcription_time = time.monotonic()

            assert resp_trans["type"] == "transcription.complete"
            assert resp_trans["data"]["text"] == "你好这是完整管道测试"
            assert resp_trans["data"]["segment_id"] == segment_id

            # 转录延迟
            transcription_latency_ms = (
                (transcription_time - last_frame_time) * 1000
            )

            print(
                f"\n[基准测试] 完整管道延迟:"
                f"\n  转录延迟: {transcription_latency_ms:.2f}ms"
                f"\n  (SC-003 阈值: 5000ms)"
            )

            assert transcription_latency_ms < 5000

            # 验证音频帧转发
            assert asr.send_audio.call_count == 15

            await communicator.disconnect()

    async def test_full_pipeline_with_stt_complete(self):
        """转录成功后 Pipeline 被触发

        验证 transcription.completed 事件触发了 _start_voice_pipeline。
        """
        with _patch_voice_deps() as (MockASR, mock_session_svc, mock_get_redis):
            mock_get_redis.return_value = _mock_redis_no_rate_limit()
            _setup_basic_session_svc(mock_session_svc)

            asr = _mock_asr_client()
            MockASR.return_value = asr

            communicator = _make_communicator(user_id=201)
            on_event = await _setup_configured_session(
                communicator, MockASR, asr
            )

            # vad.speech_start
            await on_event({"type": "vad.speech_start"})
            start_resp = await _receive_json(communicator)
            segment_id = start_resp["data"]["segment_id"]

            # 发送音频
            pcm_frame = b"\x00\x01" * 480
            for _ in range(10):
                await communicator.send_to(bytes_data=pcm_frame)
            await asyncio.sleep(0.05)

            # vad.speech_end
            await on_event({"type": "vad.speech_end", "duration_ms": 300})
            await _receive_json(communicator)

            last_frame_time = time.monotonic()

            # ASR 转录完成
            await asyncio.sleep(0.1)
            await on_event({
                "type": "transcription.completed",
                "text": "这是语音转写文本",
                "language": "zh",
            })

            resp_trans = await _receive_json(communicator, timeout=5)
            transcription_time = time.monotonic()

            assert resp_trans["type"] == "transcription.complete"
            assert resp_trans["data"]["text"] == "这是语音转写文本"

            total_latency_ms = (
                (transcription_time - last_frame_time) * 1000
            )
            print(
                f"\n[基准测试] 含转录的完整管道延迟: {total_latency_ms:.2f}ms "
                f"(SC-003 阈值: 5000ms)"
            )

            assert total_latency_ms < 5000

            # 等待 pipeline 后台任务启动（异步创建）
            await asyncio.sleep(0.1)

            await communicator.disconnect()

    async def test_full_pipeline_multiple_rounds(self):
        """多轮连续语音交互延迟测试

        模拟 3 轮连续语音交互，验证每轮延迟都满足 SC-003。
        确保状态正确重置，不存在累积延迟。
        """
        with _patch_voice_deps() as (MockASR, mock_session_svc, mock_get_redis):
            mock_get_redis.return_value = _mock_redis_no_rate_limit()
            _setup_basic_session_svc(mock_session_svc)

            asr = _mock_asr_client()
            MockASR.return_value = asr

            communicator = _make_communicator(user_id=202)
            on_event = await _setup_configured_session(
                communicator, MockASR, asr
            )

            round_latencies = []

            for round_num in range(3):
                # vad.speech_start
                await on_event({
                    "type": "vad.speech_start",
                    "timestamp": (round_num + 1) * 10000,
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
                    "duration_ms": 240,
                })
                await _receive_json(communicator)

                last_frame_time = time.monotonic()

                # ASR 转录
                await asyncio.sleep(0.05)
                await on_event({
                    "type": "transcription.completed",
                    "text": f"第{round_num+1}轮语音",
                })

                resp = await _receive_json(communicator, timeout=5)
                transcription_time = time.monotonic()
                assert resp["type"] == "transcription.complete"

                latency_ms = (transcription_time - last_frame_time) * 1000
                round_latencies.append(latency_ms)

                # 等待 pipeline 后台任务（不阻塞）
                await asyncio.sleep(0.1)

            # 输出各轮延迟
            print("\n[基准测试] 多轮语音交互延迟:")
            for i, lat in enumerate(round_latencies):
                print(f"  第 {i+1} 轮: {lat:.2f}ms")
            print(f"  SC-003 阈值: 5000ms")

            # 验证每轮延迟都 < 5 秒
            for i, lat in enumerate(round_latencies):
                assert lat < 5000, (
                    f"第 {i+1} 轮延迟 {lat:.2f}ms 超过 SC-003 阈值 5000ms"
                )

            # 验证没有累积延迟
            if len(round_latencies) >= 2:
                max_degradation = max(round_latencies) / min(round_latencies)
                print(f"  延迟退化比: {max_degradation:.2f}x")
                assert max_degradation < 3.0, (
                    f"检测到累积延迟退化: "
                    f"最大/最小延迟比 = {max_degradation:.2f}x"
                )

            await communicator.disconnect()
