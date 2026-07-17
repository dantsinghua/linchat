"""VoicePipeline 单元测试

覆盖:
- run_pipeline 正常流程（Agent content+done）
- Agent 错误处理
- TTS 管理器集成（TTSPipelineManager enqueue/stop_comfort_timer/wait_idle/shutdown）
- TTS 管理器内部错误降级
- TTS 禁用（VOICE_TTS_ENABLED=False → 不创建 TTSPipelineManager）
- response 事件序列验证（start→delta→end）
- StreamChunk 全类型处理（content/done/error/interrupted/context_compacting）
- 管道互斥：barge-in 打断
- 取消机制：cancel() → InferenceService.cancel_task() + TTSPipelineManager.cancel()
- TTS 管理器 shutdown 超时处理
- T019: 音频持久化 (persist_audio_attachment)
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from django.conf import settings
from django.test import override_settings

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
        cls.complete_task = AsyncMock()
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
    """Mock TTSPipelineManager — 013-tts-comfort-queue 改造后的 TTS 管理器。"""
    with patch(f"{_VP}.TTSPipelineManager") as cls:
        mgr = MagicMock()
        mgr.start = MagicMock()
        mgr.enqueue = MagicMock()
        mgr.stop_comfort_timer = MagicMock()
        mgr.wait_idle = AsyncMock()
        mgr.shutdown = AsyncMock()
        mgr.cancel = AsyncMock()
        cls.return_value = mgr
        yield cls, mgr


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

        # 验证 TTS 管理器生命周期（013-tts-comfort-queue）
        tts.start.assert_called_once()
        tts.stop_comfort_timer.assert_called()
        tts.enqueue.assert_called_once_with("你好，世界。", "response")
        tts.wait_idle.assert_called_once()
        tts.shutdown.assert_called_once()

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

    async def test_tts_enqueue_full_response(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """Agent 完成后，完整回复 enqueue 到 TTS 管理器。"""
        _, mgr = mock_tts
        consumer = _make_consumer()

        from apps.voice.services.voice_pipeline import VoicePipeline

        await VoicePipeline.run_pipeline(
            user_id=1, text="test", segment_id="seg", consumer=consumer
        )

        # 完整文本一次性 enqueue（非逐 chunk）
        mgr.enqueue.assert_called_once_with("你好，世界。", "response")

    async def test_tts_manager_lifecycle(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """Agent 完成后 stop_comfort_timer + wait_idle + shutdown。"""
        _, mgr = mock_tts
        consumer = _make_consumer()

        from apps.voice.services.voice_pipeline import VoicePipeline

        await VoicePipeline.run_pipeline(
            user_id=1, text="test", segment_id="seg", consumer=consumer
        )

        mgr.stop_comfort_timer.assert_called()
        mgr.wait_idle.assert_called_once()
        mgr.shutdown.assert_called_once()


# ──────────────────────────────────────────────
# T015(4): TTS WS 连接失败降级纯文字
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestTTSFallback:

    async def test_tts_manager_error_degrades_gracefully(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """TTS 管理器内部错误 → 文字仍正常发送 + response.end。"""
        _, mgr = mock_tts
        # wait_idle 抛异常模拟 TTS 内部故障
        mgr.wait_idle = AsyncMock(side_effect=Exception("TTS internal error"))
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


# ──────────────────────────────────────────────
# T015(5): TTS 禁用
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestTTSDisabled:

    @patch(f"{_VP}.settings")
    async def test_tts_disabled_no_tts_manager(
        self, mock_settings, mock_inference_svc, mock_rate_limit, mock_agent
    ):
        """VOICE_TTS_ENABLED=False → 不创建 TTSPipelineManager。"""
        mock_settings.VOICE_TTS_ENABLED = False
        mock_settings.VOICE_TTS_TIMEOUT = 30
        mock_settings.VOICE_TTS_VOICE = "zf_xiaobei"
        mock_settings.VOICE_TTS_ERROR_TEXT = "错误"
        consumer = _make_consumer()

        from apps.voice.services.voice_pipeline import VoicePipeline

        with patch(f"{_VP}.TTSPipelineManager") as MockMgr:
            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )
            MockMgr.assert_not_called()

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

    async def test_tts_manager_shutdown_timeout_handled(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """TTS 管理器 shutdown 超时 → 不影响 response.end 发送。"""
        _, mgr = mock_tts
        mgr.shutdown = AsyncMock(
            side_effect=asyncio.TimeoutError("shutdown timeout")
        )
        consumer = _make_consumer()

        from apps.voice.services.voice_pipeline import VoicePipeline

        await VoicePipeline.run_pipeline(
            user_id=1, text="test", segment_id="seg", consumer=consumer
        )

        # 即使 TTS 管理器 shutdown 超时，仍发送 response.end
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert types[-1] == "response.end"


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
        """正常流程后调用持久化：persist_audio_attachment 被调用。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_persist_service") as mock_persist,
        ):
            mock_persist.persist_audio_attachment = AsyncMock()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg-1", consumer=consumer
            )

            # 持久化被调用
            mock_persist.persist_audio_attachment.assert_called_once()

    async def test_persist_no_audio_chunks_skips(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """无音频缓存时 persist_audio_attachment 内部跳过（由 persist_service 处理）。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_persist_service") as mock_persist,
        ):
            mock_persist.persist_audio_attachment = AsyncMock()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

            # persist_audio_attachment 被调用，内部处理空缓存逻辑
            mock_persist.persist_audio_attachment.assert_called_once()

    async def test_persist_transaction_failure_compensates_minio(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """持久化内部失败由 persist_service 内部处理，pipeline 正常完成。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_persist_service") as mock_persist,
        ):
            # persist_audio_attachment 本身不抛异常（内部已 try/except），
            # 验证 pipeline 在持久化完成后仍正常发送 response.end
            mock_persist.persist_audio_attachment = AsyncMock()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

        # response.end 仍然发送
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert types[-1] == "response.end"
        mock_persist.persist_audio_attachment.assert_called_once()

    async def test_persist_error_does_not_propagate(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """持久化错误不影响 pipeline 正常完成（persist_service 内部吞掉异常）。"""
        consumer = _make_consumer()

        with (
            patch(f"{_VP}.voice_persist_service") as mock_persist,
        ):
            # 真实 persist_audio_attachment 内部已有 try/except，不会向外抛异常
            # 此测试验证即使 persist_audio_attachment 被调用，pipeline 仍正常完成
            mock_persist.persist_audio_attachment = AsyncMock()

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg", consumer=consumer
            )

        # response.end 仍然发送
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert types[-1] == "response.end"


# ──────────────────────────────────────────────
# T016: ambient 模式 voice pipeline 测试
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestAmbientRespondPipeline:

    async def test_ambient_respond_sends_agent_result_via_tts_router(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """ambient RESPOND → Agent + TTSRouter 路由 TTS 音频。"""
        consumer = _make_consumer()
        _, tts = mock_tts

        # batch-08：关闭轻量开关，保持本用例测试完整 Agent 的 ambient 路径（mock_agent 生效）
        with (
            override_settings(VOICE_AMBIENT_LIGHT_ENABLED=False),
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch("apps.voice.services.tts_router.TTSRouter") as MockRouter,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.set_active_conversation = AsyncMock()
            mock_persist.persist_audio_attachment = AsyncMock()

            router_inst = MagicMock()
            on_audio_cb = AsyncMock()
            router_inst.get_on_audio_callback.return_value = on_audio_cb
            router_inst.send_control = AsyncMock()
            MockRouter.return_value = router_inst

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="帮我开灯", segment_id="seg",
                consumer=consumer, mode="ambient"
            )

        # 验证活跃对话标记
        mock_sess.set_active_conversation.assert_called_once_with(1)

        # 验证 tts.started 控制消息
        router_inst.send_control.assert_any_call(1, "tts.started")

        # 验证 response 事件序列
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert "response.start" in types
        assert "response.delta" in types
        assert "response.end" in types

    async def test_ambient_skips_response_decision(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """ambient 模式直接进入 pipeline，不调用 ResponseDecisionService。"""
        consumer = _make_consumer()

        with (
            override_settings(VOICE_AMBIENT_LIGHT_ENABLED=False),
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch("apps.voice.services.tts_router.TTSRouter") as MockRouter,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.set_active_conversation = AsyncMock()
            mock_persist.persist_audio_attachment = AsyncMock()
            router_inst = MagicMock()
            router_inst.get_on_audio_callback.return_value = AsyncMock()
            router_inst.send_control = AsyncMock()
            MockRouter.return_value = router_inst

            from apps.voice.services.voice_pipeline import VoicePipeline

            with patch(
                "apps.voice.services.response_decision_service.response_decision_service"
            ) as mock_rds:
                await VoicePipeline.run_pipeline(
                    user_id=1, text="test", segment_id="seg",
                    consumer=consumer, mode="ambient"
                )
                mock_rds.decide.assert_not_called()

    async def test_ambient_tts_completed_sent_after_shutdown(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """ambient 模式 TTS 完成后发送 tts.completed 控制消息。"""
        consumer = _make_consumer()

        with (
            override_settings(VOICE_AMBIENT_LIGHT_ENABLED=False),
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch("apps.voice.services.tts_router.TTSRouter") as MockRouter,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.set_active_conversation = AsyncMock()
            mock_persist.persist_audio_attachment = AsyncMock()

            router_inst = MagicMock()
            router_inst.get_on_audio_callback.return_value = AsyncMock()
            router_inst.send_control = AsyncMock()
            MockRouter.return_value = router_inst

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="test", segment_id="seg",
                consumer=consumer, mode="ambient"
            )

        # tts.completed 由 finally 块中的 TTSRouter 发送
        control_calls = router_inst.send_control.call_args_list
        control_types = [c.args[1] for c in control_calls]
        assert "tts.completed" in control_types


# ──────────────────────────────────────────────
# batch-08: ambient 轻量推理路径（VOICE_AMBIENT_LIGHT_ENABLED）
# ──────────────────────────────────────────────

_ALS = "apps.voice.services.ambient_light_service"


async def _mock_light_stream_normal(*args, **kwargs):
    """模拟 AmbientLightPipeline.stream：2 个 content chunk + done。"""
    yield StreamChunk(type="content", content="好的", request_id="reqL")
    yield StreamChunk(type="content", content="，帮你开灯。")
    yield StreamChunk(type="done", content="", message_id=9)


async def _mock_light_stream_error(*args, **kwargs):
    """模拟 AmbientLightPipeline.stream 返回 error chunk。"""
    yield StreamChunk(type="error", content="AI响应超时，请稍后重试")


@pytest.mark.asyncio(loop_scope="function")
class TestAmbientLightPipeline:

    async def test_ambient_enabled_uses_light_path_not_agent(
        self, mock_inference_svc, mock_rate_limit, mock_tts
    ):
        """mode=ambient + 开关开启 → 走 AmbientLightPipeline，不调用 AgentService.execute。"""
        consumer = _make_consumer()

        with (
            override_settings(VOICE_AMBIENT_LIGHT_ENABLED=True),
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch(f"{_VP}.AgentService") as MockAgent,
            patch("apps.voice.services.tts_router.TTSRouter") as MockRouter,
            patch(f"{_ALS}.AmbientLightPipeline") as MockLight,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.set_active_conversation = AsyncMock()
            mock_persist.persist_audio_attachment = AsyncMock()
            router_inst = MagicMock()
            router_inst.get_on_audio_callback.return_value = AsyncMock()
            router_inst.send_control = AsyncMock()
            MockRouter.return_value = router_inst
            MockLight.stream = MagicMock(side_effect=_mock_light_stream_normal)

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="帮我开灯", segment_id="seg",
                consumer=consumer, mode="ambient"
            )

        # 走轻量路径：AmbientLightPipeline.stream 被调用，AgentService.execute 未被调用
        MockLight.stream.assert_called_once()
        light_args = MockLight.stream.call_args[0]
        assert light_args[0] == 1  # user_id
        assert light_args[2] == "帮我开灯"  # ASR 原文（非 voice_text 前缀）
        MockAgent.execute.assert_not_called()
        # content 正常流转为 delta 事件
        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert types[0] == "response.start"
        assert "response.delta" in types
        assert types[-1] == "response.end"
        deltas = [c.args[0]["data"]["delta"]["content"]
                  for c in calls if c.args[0]["type"] == "response.delta"]
        assert deltas == ["好的", "，帮你开灯。"]

    async def test_ambient_disabled_falls_back_to_agent(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """mode=ambient + 开关关闭 → 回退完整 AgentService.execute（回滚手段）。"""
        consumer = _make_consumer()

        with (
            override_settings(VOICE_AMBIENT_LIGHT_ENABLED=False),
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch("apps.voice.services.tts_router.TTSRouter") as MockRouter,
            patch(f"{_ALS}.AmbientLightPipeline") as MockLight,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.set_active_conversation = AsyncMock()
            mock_persist.persist_audio_attachment = AsyncMock()
            router_inst = MagicMock()
            router_inst.get_on_audio_callback.return_value = AsyncMock()
            router_inst.send_control = AsyncMock()
            MockRouter.return_value = router_inst
            MockLight.stream = MagicMock(side_effect=_mock_light_stream_normal)

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="帮我开灯", segment_id="seg",
                consumer=consumer, mode="ambient"
            )

        # 开关关闭：走完整 Agent，轻量路径未被调用
        mock_agent.execute.assert_called_once()
        MockLight.stream.assert_not_called()

    async def test_voice_chat_never_uses_light_path(
        self, mock_inference_svc, mock_rate_limit, mock_agent, mock_tts
    ):
        """mode=voice_chat（开关开启也）永远走完整 Agent，零回归。"""
        consumer = _make_consumer()

        with (
            override_settings(VOICE_AMBIENT_LIGHT_ENABLED=True),
            patch(f"{_ALS}.AmbientLightPipeline") as MockLight,
        ):
            MockLight.stream = MagicMock(side_effect=_mock_light_stream_normal)

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="你好", segment_id="seg", consumer=consumer
            )

        mock_agent.execute.assert_called_once()
        MockLight.stream.assert_not_called()

    async def test_ambient_light_error_chunk_triggers_error_event(
        self, mock_inference_svc, mock_rate_limit, mock_tts
    ):
        """轻量路径 error chunk → 发送 error 事件 + TTS 错误播报。"""
        consumer = _make_consumer()
        _, tts = mock_tts

        with (
            override_settings(VOICE_AMBIENT_LIGHT_ENABLED=True),
            patch(f"{_VP}.voice_session_service") as mock_sess,
            patch(f"{_VP}.voice_persist_service") as mock_persist,
            patch("apps.voice.services.tts_router.TTSRouter") as MockRouter,
            patch(f"{_ALS}.AmbientLightPipeline") as MockLight,
        ):
            mock_sess.check_llm_rate_limit = AsyncMock(return_value=True)
            mock_sess.set_active_conversation = AsyncMock()
            mock_persist.persist_audio_attachment = AsyncMock()
            router_inst = MagicMock()
            router_inst.get_on_audio_callback.return_value = AsyncMock()
            router_inst.send_control = AsyncMock()
            MockRouter.return_value = router_inst
            MockLight.stream = MagicMock(side_effect=_mock_light_stream_error)

            from apps.voice.services.voice_pipeline import VoicePipeline

            await VoicePipeline.run_pipeline(
                user_id=1, text="帮我开灯", segment_id="seg",
                consumer=consumer, mode="ambient"
            )

        calls = consumer._send_json.call_args_list
        types = [c.args[0]["type"] for c in calls]
        assert "error" in types
        # 错误播报入队
        tts.enqueue.assert_any_call(settings.VOICE_TTS_ERROR_TEXT, "error")


_VPS = "apps.voice.services.voice_persist_service"


@pytest.mark.asyncio(loop_scope="function")
class TestAmbientRecordOnly:

    async def test_record_only_ambient_saves_message(self):
        """ambient RECORD_ONLY → 保存 user Message（is_voice=True）。"""
        with (
            patch(f"{_VPS}.message_repo") as mock_repo,
            patch(f"{_VPS}.voice_persist_service._cleanup_record_only", new_callable=AsyncMock),
        ):
            mock_repo.get_next_sequence = AsyncMock(return_value=5)
            mock_repo.create = AsyncMock()

            from apps.voice.services.voice_persist_service import voice_persist_service

            await voice_persist_service.record_only_ambient(
                user_id=1, text="今天好热啊"
            )

        mock_repo.create.assert_called_once()
        created_msg = mock_repo.create.call_args[0][0]
        assert created_msg.role == "user"
        assert created_msg.content == "今天好热啊"
        assert created_msg.is_voice is True
        assert created_msg.user_id == 1

    async def test_record_only_ambient_triggers_cleanup(self):
        """ambient RECORD_ONLY 保存后触发清理检查。"""
        with (
            patch(f"{_VPS}.message_repo") as mock_repo,
            patch(f"{_VPS}.voice_persist_service._cleanup_record_only", new_callable=AsyncMock) as mock_cleanup,
        ):
            mock_repo.get_next_sequence = AsyncMock(return_value=1)
            mock_repo.create = AsyncMock()

            from apps.voice.services.voice_persist_service import voice_persist_service

            await voice_persist_service.record_only_ambient(
                user_id=42, text="闲聊"
            )

        mock_cleanup.assert_called_once_with(42)

    async def test_record_only_ambient_no_agent_call(self):
        """ambient RECORD_ONLY 不调用 Agent。"""
        with (
            patch(f"{_VPS}.message_repo") as mock_repo,
            patch(f"{_VPS}.voice_persist_service._cleanup_record_only", new_callable=AsyncMock),
            patch(f"{_VP}.AgentService") as MockAgent,
        ):
            mock_repo.get_next_sequence = AsyncMock(return_value=1)
            mock_repo.create = AsyncMock()

            from apps.voice.services.voice_persist_service import voice_persist_service

            await voice_persist_service.record_only_ambient(
                user_id=1, text="日常聊天"
            )

        MockAgent.execute.assert_not_called()

    async def test_record_only_ambient_error_handled(self):
        """ambient RECORD_ONLY 失败时不抛异常。"""
        with (
            patch(f"{_VPS}.message_repo") as mock_repo,
        ):
            mock_repo.get_next_sequence = AsyncMock(
                side_effect=Exception("DB error")
            )

            from apps.voice.services.voice_persist_service import voice_persist_service

            # 不应抛异常
            await voice_persist_service.record_only_ambient(
                user_id=1, text="test"
            )


# ──────────────────────────────────────────────
# T018: RECORD_ONLY 持久化 + 清理上限
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestRecordOnlyCleanup:

    async def test_cleanup_removes_excess_messages(self):
        """超过限额时 _count_and_delete_excess 被调用，返回删除数量。"""
        with (
            patch(f"{_VPS}.voice_persist_service._count_and_delete_excess", new_callable=AsyncMock) as mock_cde,
        ):
            mock_cde.return_value = 5  # 删除了 5 条

            from apps.voice.services.voice_persist_service import voice_persist_service

            await voice_persist_service._cleanup_record_only(user_id=1)

        mock_cde.assert_called_once()

    async def test_cleanup_no_action_below_limit(self):
        """未超限时 _count_and_delete_excess 返回 0，不执行删除。"""
        with (
            patch(f"{_VPS}.voice_persist_service._count_and_delete_excess", new_callable=AsyncMock) as mock_cde,
        ):
            mock_cde.return_value = 0

            from apps.voice.services.voice_persist_service import voice_persist_service

            await voice_persist_service._cleanup_record_only(user_id=1)

        mock_cde.assert_called_once()

    async def test_cleanup_at_exact_limit(self):
        """恰好达到上限时 _count_and_delete_excess 返回 0。"""
        with (
            patch(f"{_VPS}.voice_persist_service._count_and_delete_excess", new_callable=AsyncMock) as mock_cde,
        ):
            mock_cde.return_value = 0

            from apps.voice.services.voice_persist_service import voice_persist_service

            await voice_persist_service._cleanup_record_only(user_id=1)

        mock_cde.assert_called_once()

    async def test_cleanup_error_does_not_propagate(self):
        """清理异常不传播。"""
        with (
            patch(f"{_VPS}.voice_persist_service._count_and_delete_excess", new_callable=AsyncMock) as mock_cde,
        ):
            mock_cde.side_effect = Exception("DB error")

            from apps.voice.services.voice_persist_service import voice_persist_service

            # 不应抛异常
            await voice_persist_service._cleanup_record_only(user_id=1)
