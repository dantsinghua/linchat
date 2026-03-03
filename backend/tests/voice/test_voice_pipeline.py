"""VoicePipeline 单元测试

覆盖:
- run_pipeline 正常流程（Agent content+done）
- Agent 错误处理
- TTS 流式集成（send_text_delta + binary PCM 转发）
- TTS WS 连接失败降级纯文字
- TTS 禁用（VOICE_TTS_ENABLED=False）
- response 事件序列验证（start→delta→end）
- StreamChunk 全类型处理（content/done/error/interrupted/context_compacting）
- 管道互斥：barge-in 打断
- 取消机制：cancel() → InferenceService.cancel_task()
- TTS audio.done 超时处理
- T019: 音频持久化 (persist_audio_attachment)
- T022: 持续监听模式 (continuous_listen)
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from apps.chat.services.types import StreamChunk

# 被测模块
_VP = "apps.voice.services.voice_pipeline"


def _make_consumer() -> MagicMock:
    """创建 mock Consumer（实现 ConsumerProtocol）。"""
    consumer = MagicMock()
    consumer._send_json = AsyncMock()
    consumer._send_binary = AsyncMock()
    return consumer


def _content_chunk(text: str, request_id: str = None) -> StreamChunk:
    return StreamChunk(type="content", content=text, request_id=request_id)


def _done_chunk(msg_id: int = 1) -> StreamChunk:
    return StreamChunk(type="done", content="", message_id=msg_id)


def _error_chunk(msg: str = "LLM error", data: dict = None) -> StreamChunk:
    return StreamChunk(type="error", content=msg, data=data)


def _interrupted_chunk() -> StreamChunk:
    return StreamChunk(type="interrupted", content="")


async def _mock_agent_execute_normal(*args, **kwargs):
    """模拟正常 Agent 执行：2 个 content chunk + done。"""
    yield _content_chunk("你好", request_id="req123")
    yield _content_chunk("，世界。")
    yield _done_chunk()


async def _mock_agent_execute_error(*args, **kwargs):
    """模拟 Agent 返回错误 chunk。"""
    yield _content_chunk("部分")
    yield _error_chunk("网关超时", data={"gateway_error": "TIMEOUT"})


async def _mock_agent_execute_interrupted(*args, **kwargs):
    """模拟 Agent 被中断。"""
    yield _content_chunk("你")
    yield _interrupted_chunk()


async def _mock_agent_execute_exception(*args, **kwargs):
    """模拟 Agent 执行抛异常。"""
    yield _content_chunk("部分内容")
    raise RuntimeError("Unexpected agent error")


async def _mock_agent_execute_all_types(*args, **kwargs):
    """模拟所有 chunk 类型。"""
    yield StreamChunk(type="context_compacting", content="")
    yield _content_chunk("hello")
    yield StreamChunk(type="context_compacted", content="")
    yield _done_chunk()


# ──────────────────────────────────────────────
# fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def mock_inference_svc():
    with patch(f"{_VP}.InferenceService") as cls:
        cls.register_task = AsyncMock(return_value=True)
        cls.cancel_task = AsyncMock(return_value=(True, "req123"))
        yield cls


@pytest.fixture
def mock_rate_limit():
    with patch(f"{_VP}.voice_session_service") as svc:
        svc.check_llm_rate_limit = AsyncMock(return_value=True)
        yield svc


@pytest.fixture
def mock_agent():
    with patch(f"{_VP}.AgentService") as cls:
        cls.execute = MagicMock(side_effect=_mock_agent_execute_normal)
        yield cls


@pytest.fixture
def mock_tts():
    with patch(f"{_VP}.TTSStreamClient") as cls:
        tts = AsyncMock()
        tts.connect = AsyncMock(return_value="tts-session-1")
        tts.configure = AsyncMock()
        tts.send_text_delta = AsyncMock()
        tts.send_text_done = AsyncMock()
        tts.wait_for_done = AsyncMock()
        tts.disconnect = AsyncMock()
        tts.connected = True
        cls.return_value = tts
        yield cls, tts


# ──────────────────────────────────────────────
# T015(1): run_pipeline 正常流程
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestRunPipelineNormal:

    async def test_normal_flow_sends_start_delta_end(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """正常流程：response.start → 2x response.delta → response.end"""
        consumer = _make_consumer()
        _, tts = mock_tts

        from apps.voice.services.voice_pipeline import VoicePipeline

        await VoicePipeline.run_pipeline(
            user_id=1, text="你好世界", segment_id="seg-001", consumer=consumer
        )

        # 验证事件序列
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert types[0] == "response.start"
        assert types[1] == "response.delta"
        assert types[2] == "response.delta"
        assert types[-1] == "response.end"

        # 验证 response.start 含 response_id 和 segment_id
        start_data = calls[0].args[0]["data"]
        assert start_data["segment_id"] == "seg-001"
        assert start_data["response_id"].startswith("voice_")

        # 验证 delta 内容
        assert calls[1].args[0]["data"]["delta"]["content"] == "你好"
        assert calls[2].args[0]["data"]["delta"]["content"] == "，世界。"

        # 验证 TTS 调用
        assert tts.send_text_delta.call_count == 2
        tts.send_text_done.assert_called_once()
        tts.wait_for_done.assert_called_once()
        tts.disconnect.assert_called_once()

    async def test_inference_task_registered(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """验证 InferenceService.register_task 被调用。"""
        consumer = _make_consumer()

        from apps.voice.services.voice_pipeline import VoicePipeline

        await VoicePipeline.run_pipeline(
            user_id=42, text="测试", segment_id="seg", consumer=consumer
        )

        mock_inference_svc.register_task.assert_called_once()
        call_args = mock_inference_svc.register_task.call_args
        assert call_args[0][0] == 42  # user_id (positional)
        assert call_args[1]["model"] == "agent"  # model (keyword)


# ──────────────────────────────────────────────
# T015(2): Agent 错误处理
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestAgentError:

    async def test_agent_error_chunk_sends_error_event(
        self, mock_inference_svc, mock_rate_limit, mock_tts
    ):
        """Agent 返回 error chunk → 发送 error 事件。"""
        with patch(f"{_VP}.AgentService") as MockAgent:
            MockAgent.execute = MagicMock(side_effect=_mock_agent_execute_error)
            consumer = _make_consumer()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert "error" in types
        error_call = next(c for c in calls if c.args[0]["type"] == "error")
        assert error_call.args[0]["data"]["code"] == "TIMEOUT"

    async def test_agent_exception_sends_pipeline_error(
        self, mock_inference_svc, mock_rate_limit, mock_tts
    ):
        """Agent 执行抛异常 → 发送 PIPELINE_ERROR 事件。"""
        with patch(f"{_VP}.AgentService") as MockAgent:
            MockAgent.execute = MagicMock(side_effect=_mock_agent_execute_exception)
            consumer = _make_consumer()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert "error" in types
        error_call = next(c for c in calls if c.args[0]["type"] == "error")
        assert error_call.args[0]["data"]["code"] == "PIPELINE_ERROR"
        # 仍然发送 response.end
        assert types[-1] == "response.end"


# ──────────────────────────────────────────────
# T015(3): TTS 流式集成
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestTTSIntegration:

    async def test_tts_send_text_delta_per_content_chunk(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """每个 content chunk 调用一次 send_text_delta。"""
        _, tts = mock_tts
        consumer = _make_consumer()

        from apps.voice.services.voice_pipeline import VoicePipeline

        await VoicePipeline.run_pipeline(
            user_id=1, text="test", segment_id="seg", consumer=consumer
        )

        assert tts.send_text_delta.call_count == 2
        tts.send_text_delta.assert_any_call("你好")
        tts.send_text_delta.assert_any_call("，世界。")

    async def test_tts_send_text_done_after_agent_finishes(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """Agent 完成后调用 send_text_done + wait_for_done。"""
        _, tts = mock_tts
        consumer = _make_consumer()

        from apps.voice.services.voice_pipeline import VoicePipeline

        await VoicePipeline.run_pipeline(
            user_id=1, text="test", segment_id="seg", consumer=consumer
        )

        tts.send_text_done.assert_called_once()
        tts.wait_for_done.assert_called_once()


# ──────────────────────────────────────────────
# T015(4): TTS WS 连接失败降级纯文字
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestTTSFallback:

    async def test_tts_connect_failure_degrades_to_text(
        self, mock_inference_svc, mock_rate_limit, mock_agent
    ):
        """TTS WS 连接失败 → 纯文字回复，无 binary 帧。"""
        with patch(f"{_VP}.TTSStreamClient") as MockTTS:
            tts = AsyncMock()
            tts.connect = AsyncMock(side_effect=ConnectionError("TTS WS down"))
            MockTTS.return_value = tts
            consumer = _make_consumer()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

        # 文字仍正常发送
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert "response.start" in types
        assert "response.delta" in types
        assert "response.end" in types

        # 无 binary 帧
        consumer._send_binary.assert_not_called()


# ──────────────────────────────────────────────
# T015(5): TTS 禁用
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestTTSDisabled:

    @patch(f"{_VP}.settings")
    async def test_tts_disabled_no_tts_client(
        self, mock_settings, mock_inference_svc, mock_rate_limit, mock_agent
    ):
        """VOICE_TTS_ENABLED=False → 不创建 TTSStreamClient。"""
        mock_settings.VOICE_TTS_ENABLED = False
        mock_settings.VOICE_TTS_TIMEOUT = 30
        mock_settings.VOICE_TTS_VOICE = "zf_xiaobei"
        consumer = _make_consumer()

        from apps.voice.services.voice_pipeline import VoicePipeline

        with patch(f"{_VP}.TTSStreamClient") as MockTTS:
            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )
            MockTTS.assert_not_called()

        # 文字仍正常发送
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert "response.delta" in types


# ──────────────────────────────────────────────
# T015(6): response 事件序列验证
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestResponseEventSequence:

    async def test_start_before_delta_before_end(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """事件序列: response.start → response.delta(s) → response.end"""
        consumer = _make_consumer()

        from apps.voice.services.voice_pipeline import VoicePipeline

        await VoicePipeline.run_pipeline(
            user_id=1, text="test", segment_id="seg", consumer=consumer
        )

        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        start_idx = types.index("response.start")
        first_delta_idx = types.index("response.delta")
        end_idx = len(types) - 1 - types[::-1].index("response.end")
        assert start_idx < first_delta_idx < end_idx


# ──────────────────────────────────────────────
# T015(7): StreamChunk 全类型处理
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestStreamChunkTypes:

    async def test_context_compacting_ignored(
        self, mock_inference_svc, mock_rate_limit, mock_tts
    ):
        """context_compacting/context_compacted 类型不产生额外事件。"""
        with patch(f"{_VP}.AgentService") as MockAgent:
            MockAgent.execute = MagicMock(side_effect=_mock_agent_execute_all_types)
            consumer = _make_consumer()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        # 只有 start, delta, end — 无 context_compacting/compacted
        assert "context_compacting" not in types
        assert "context_compacted" not in types
        assert types.count("response.delta") == 1  # 仅 "hello"

    async def test_interrupted_stops_early(
        self, mock_inference_svc, mock_rate_limit, mock_tts
    ):
        """interrupted chunk → 停止输出但仍发 response.end。"""
        with patch(f"{_VP}.AgentService") as MockAgent:
            MockAgent.execute = MagicMock(side_effect=_mock_agent_execute_interrupted)
            consumer = _make_consumer()
            _, tts = mock_tts

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        # 只有 1 个 delta（"你"），interrupted 后停止
        assert types.count("response.delta") == 1
        assert types[-1] == "response.end"


# ──────────────────────────────────────────────
# T015(8): 管道互斥（barge-in）
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestPipelineMutex:

    async def test_barge_in_cancels_old_pipeline(
        self, mock_inference_svc, mock_rate_limit, mock_tts
    ):
        """同一用户新 segment 到达时，旧 pipeline 被取消。"""
        # 模拟慢 Agent（0.5s per chunk）
        async def slow_agent(*args, **kwargs):
            yield _content_chunk("慢")
            await asyncio.sleep(0.5)
            yield _content_chunk("速回复")
            yield _done_chunk()

        async def fast_agent(*args, **kwargs):
            yield _content_chunk("快速回复")
            yield _done_chunk()

        call_count = 0

        def agent_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return slow_agent(*args, **kwargs)
            return fast_agent(*args, **kwargs)

        with patch(f"{_VP}.AgentService") as MockAgent:
            MockAgent.execute = MagicMock(side_effect=agent_side_effect)
            consumer = _make_consumer()

            from apps.voice.services.voice_pipeline import VoicePipeline, _pipeline_locks

            # 清理锁状态
            _pipeline_locks.pop(99, None)

            # 启动第一个 pipeline（慢速）
            task1 = asyncio.create_task(
                VoicePipeline.run_pipeline(
                    user_id=99, text="慢", segment_id="seg-1", consumer=consumer
                )
            )
            await asyncio.sleep(0.05)  # 让 task1 进入 Agent 循环

            # 启动第二个 pipeline（触发 barge-in）
            task2 = asyncio.create_task(
                VoicePipeline.run_pipeline(
                    user_id=99, text="快", segment_id="seg-2", consumer=consumer
                )
            )

            await asyncio.gather(task1, task2, return_exceptions=True)

        # cancel_task 至少被调用 1 次（barge-in 触发）
        assert mock_inference_svc.cancel_task.call_count >= 1


# ──────────────────────────────────────────────
# T015(9): 取消机制
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestCancelMechanism:

    async def test_cancel_calls_inference_service(self):
        """cancel() → InferenceService.cancel_task(user_id)"""
        with patch(f"{_VP}.InferenceService") as MockIS:
            MockIS.cancel_task = AsyncMock(return_value=(True, "req-abc"))

            from apps.voice.services.voice_pipeline import VoicePipeline

            result = await VoicePipeline.cancel(user_id=42)

        assert result is True
        MockIS.cancel_task.assert_called_once_with(42)

    async def test_cancel_returns_false_when_no_task(self):
        """无活跃任务时 cancel 返回 False。"""
        with patch(f"{_VP}.InferenceService") as MockIS:
            MockIS.cancel_task = AsyncMock(return_value=(False, None))

            from apps.voice.services.voice_pipeline import VoicePipeline

            result = await VoicePipeline.cancel(user_id=42)

        assert result is False


# ──────────────────────────────────────────────
# T015(10): TTS audio.done 超时
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestTTSTimeout:

    async def test_tts_wait_for_done_timeout_handled(
        self, mock_inference_svc, mock_rate_limit, mock_agent
    ):
        """TTS audio.done 超时 → 不影响 response.end 发送。"""
        with patch(f"{_VP}.TTSStreamClient") as MockTTS:
            tts = AsyncMock()
            tts.connect = AsyncMock(return_value="tts-1")
            tts.configure = AsyncMock()
            tts.send_text_delta = AsyncMock()
            tts.send_text_done = AsyncMock()
            tts.wait_for_done = AsyncMock(
                side_effect=asyncio.TimeoutError("audio.done timeout")
            )
            tts.disconnect = AsyncMock()
            tts.connected = True
            MockTTS.return_value = tts

            consumer = _make_consumer()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

        # 即使 TTS 超时，仍发送 response.end
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert types[-1] == "response.end"
        # disconnect 被调用
        tts.disconnect.assert_called_once()


# ──────────────────────────────────────────────
# 额外: 频率限制 & 任务冲突
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestPipelineGuards:

    async def test_rate_limit_blocks_pipeline(
        self, mock_inference_svc, mock_tts
    ):
        """频率超限 → 发送 RATE_LIMIT 错误，不调用 Agent。"""
        with patch(f"{_VP}.voice_session_service") as svc:
            svc.check_llm_rate_limit = AsyncMock(return_value=False)
            with patch(f"{_VP}.AgentService") as MockAgent:
                consumer = _make_consumer()

                from apps.voice.services.voice_pipeline import VoicePipeline

                await VoicePipeline.run_pipeline(
                    user_id=1, text="test", segment_id="seg", consumer=consumer
                )

                MockAgent.execute.assert_not_called()

        calls = consumer._send_json.call_args_list
        assert any(
            c.args[0].get("type") == "error"
            and c.args[0]["data"]["code"] == "RATE_LIMIT"
            for c in calls
        )

    async def test_inference_conflict_blocks_pipeline(
        self, mock_rate_limit, mock_tts
    ):
        """推理任务冲突 → 发送 INFERENCE_BUSY 错误。"""
        with patch(f"{_VP}.InferenceService") as MockIS:
            MockIS.register_task = AsyncMock(return_value=False)
            with patch(f"{_VP}.AgentService") as MockAgent:
                consumer = _make_consumer()

                from apps.voice.services.voice_pipeline import VoicePipeline

                await VoicePipeline.run_pipeline(
                    user_id=1, text="test", segment_id="seg", consumer=consumer
                )

                MockAgent.execute.assert_not_called()

        calls = consumer._send_json.call_args_list
        assert any(
            c.args[0].get("type") == "error"
            and c.args[0]["data"]["code"] == "INFERENCE_BUSY"
            for c in calls
        )


# ──────────────────────────────────────────────
# T019: 音频持久化
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestPersistAudioAttachment:

    async def test_persist_normal_flow(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """正常流程后调用持久化：upload_to_minio + _atomic_mark_voice。"""
        consumer = _make_consumer()
        pcm_chunks = [b"\x00\x01" * 160]  # 10ms of PCM

        with (
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch(f"{_VP}.VoicePipeline._atomic_mark_voice", new_callable=AsyncMock) as mock_atomic,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.get_audio_chunks = AsyncMock(return_value=pcm_chunks)
            mock_sess.clear_audio_chunks = AsyncMock()
            mock_persist.merge_pcm_to_wav.return_value = b"RIFF_WAV_DATA"
            mock_persist.calculate_duration.return_value = 0.01
            mock_persist.upload_to_minio = AsyncMock()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg-1", consumer=consumer
            )

            # MinIO 上传被调用
            mock_persist.upload_to_minio.assert_called_once()
            # 事务标记被调用
            mock_atomic.assert_called_once()
            # 音频缓存被清理
            mock_sess.clear_audio_chunks.assert_called_once_with(1, "seg-1")

    async def test_persist_no_audio_chunks_skips(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """无音频缓存时跳过持久化。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.get_audio_chunks = AsyncMock(return_value=[])
            mock_persist.upload_to_minio = AsyncMock()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

            mock_persist.upload_to_minio.assert_not_called()

    async def test_persist_transaction_failure_compensates_minio(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """事务失败时补偿删除 MinIO 文件。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch(f"{_VP}.VoicePipeline._atomic_mark_voice", new_callable=AsyncMock) as mock_atomic,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.get_audio_chunks = AsyncMock(return_value=[b"\x00" * 320])
            mock_sess.clear_audio_chunks = AsyncMock()
            mock_persist.merge_pcm_to_wav.return_value = b"WAV"
            mock_persist.calculate_duration.return_value = 0.01
            mock_persist.upload_to_minio = AsyncMock()
            mock_persist.delete_from_minio = AsyncMock()
            mock_atomic.side_effect = Exception("DB error")

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

            mock_persist.delete_from_minio.assert_called_once()

    async def test_persist_error_does_not_propagate(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """持久化错误不影响 pipeline 正常完成。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.get_audio_chunks = AsyncMock(side_effect=Exception("Redis down"))
            mock_persist.upload_to_minio = AsyncMock()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

        # response.end 仍然发送
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert types[-1] == "response.end"


# ──────────────────────────────────────────────
# T022: 持续监听模式
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestContinuousListenMode:

    async def test_respond_decision_triggers_full_pipeline(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """RESPOND 决策 → 完整 Agent + TTS pipeline。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch("apps.voice.services.response_decision_service.response_decision_service") as mock_rds,
        ):
            from apps.voice.services.response_decision_service import DecisionResult
            mock_rds.decide = AsyncMock(return_value=(DecisionResult.RESPOND, "exact_wake_word"))
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.set_active_conversation = AsyncMock()
            mock_sess.get_audio_chunks = AsyncMock(return_value=[])

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="安琳 今天天气", segment_id="seg",
                consumer=consumer, mode="continuous_listen"
            )

        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert "response.start" in types
        assert "response.delta" in types
        assert "response.end" in types
        mock_sess.set_active_conversation.assert_called_once_with(1)

    async def test_record_only_saves_user_message_only(self):
        """RECORD_ONLY 决策 → 仅保存 user Message，不调用 Agent。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch(f"{_VP}.message_repo") as mock_repo,
            patch("apps.voice.services.response_decision_service.response_decision_service") as mock_rds,
            patch(f"{_VP}.AgentService") as MockAgent,
            patch(f"{_VP}.VoicePipeline._persist_audio_attachment", new_callable=AsyncMock) as mock_pa,
        ):
            from apps.voice.services.response_decision_service import DecisionResult
            mock_rds.decide = AsyncMock(return_value=(DecisionResult.RECORD_ONLY, "default"))
            mock_repo.get_next_sequence = AsyncMock(return_value=10)
            mock_repo.create = AsyncMock()
            mock_sess.get_audio_chunks = AsyncMock(return_value=[])

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="闲聊内容", segment_id="seg",
                consumer=consumer, mode="continuous_listen"
            )

            # message_repo.create 被调用（user Message）
            mock_repo.create.assert_called_once()
            created_msg = mock_repo.create.call_args[0][0]
            assert created_msg.role == "user"
            assert created_msg.is_voice is True
            assert created_msg.content == "闲聊内容"

            # Agent 不被调用
            MockAgent.execute.assert_not_called()

            # 不发送 response 事件
            calls = consumer._send_json.call_args_list
            types = [c.args[0]["type"] for c in calls] if calls else []
            assert "response.start" not in types

    async def test_stop_decision_cancels_pipeline(self):
        """STOP 决策 → 取消正在进行的推理。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.InferenceService") as MockIS,
            patch("apps.voice.services.response_decision_service.response_decision_service") as mock_rds,
            patch(f"{_VP}.AgentService") as MockAgent,
        ):
            from apps.voice.services.response_decision_service import DecisionResult
            mock_rds.decide = AsyncMock(return_value=(DecisionResult.STOP, "emergency_stop"))
            MockIS.cancel_task = AsyncMock(return_value=(True, "req-1"))

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="停", segment_id="seg",
                consumer=consumer, mode="continuous_listen"
            )

            # cancel 被调用
            MockIS.cancel_task.assert_called_once_with(1)
            # Agent 不被调用
            MockAgent.execute.assert_not_called()

    async def test_voice_chat_mode_skips_decision(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """voice_chat 模式不经过决策直接进入 pipeline。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.get_audio_chunks = AsyncMock(return_value=[])

            from apps.voice.services.voice_pipeline import VoicePipeline

            with patch("apps.voice.services.response_decision_service.response_decision_service") as mock_rds:
                await VoicePipeline.run_pipeline(
                    user_id=1, text="test", segment_id="seg",
                    consumer=consumer, mode="voice_chat"
                )
                mock_rds.decide.assert_not_called()
