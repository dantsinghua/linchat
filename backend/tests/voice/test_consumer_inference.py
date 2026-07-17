"""InferenceMixin（apps.voice.consumer_inference）接线冒烟单测 — batch-21。

本批仅做 Protocol 接线所需的最小验证（plan 第 4/7 节，完整覆盖留给 batch-25）：
  1. import 冒烟 + 运行时基类未变（__mro__ 含 object，未真正继承 Protocol）
  2. _is_pipeline_busy 三态（无 task / task 未完成 / task 已完成）
  3. _reset_response_state 四字段重置

测试方式：以 InferenceMixin.<method>(host, ...) 的 unbound 姿势调用，host 为
预置共享属性的轻量宿主对象（运行时 InferenceMixin 不真正继承 Protocol，
__bases__ == (object,)），全程无真实 IO。
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings

from apps.voice.consumer_inference import InferenceMixin


# =====================================================================
# import 冒烟 + 运行时基类
# =====================================================================
class TestImportSmoke:
    def test_importable_and_runtime_base_is_object(self):
        """接线后运行时基类仍为 object（else: object 分支），未真正继承 Protocol。"""
        assert InferenceMixin.__bases__ == (object,)
        assert object in InferenceMixin.__mro__


# =====================================================================
# _is_pipeline_busy 三态
# =====================================================================
class TestIsPipelineBusy:
    def test_no_task_returns_false(self):
        """无 pipeline task → 非忙。"""
        c = SimpleNamespace(_pipeline_task=None)
        assert InferenceMixin._is_pipeline_busy(c) is False

    def test_running_task_returns_true(self):
        """task 存在且未完成 → 忙。"""
        task = MagicMock()
        task.done.return_value = False
        c = SimpleNamespace(_pipeline_task=task)
        assert InferenceMixin._is_pipeline_busy(c) is True

    def test_done_task_returns_false(self):
        """task 已完成 → 非忙。"""
        task = MagicMock()
        task.done.return_value = True
        c = SimpleNamespace(_pipeline_task=task)
        assert InferenceMixin._is_pipeline_busy(c) is False


# =====================================================================
# _reset_response_state 四字段重置
# =====================================================================
class TestResetResponseState:
    def test_resets_all_four_fields(self):
        """调用后 4 个响应状态字段被正确重置。"""
        c = SimpleNamespace(
            _current_response_id="resp-1",
            _response_start_time=123.4,
            _accumulated_content="partial",
            _response_cancelled=True,
        )
        InferenceMixin._reset_response_state(c)
        assert c._current_response_id is None
        assert c._response_start_time is None
        assert c._accumulated_content == ""
        assert c._response_cancelled is False


# =====================================================================
# 分组 A — _start_voice_pipeline 的 _wrapped 闭包分支（line 35-42）
# =====================================================================
class TestStartVoicePipelineWrapped:
    @pytest.mark.asyncio
    async def test_ambient_mode_calls_on_pipeline_done(self):
        """mode=ambient → finally 分支调用 _on_pipeline_done（命中 41-42）。"""
        host = SimpleNamespace(
            user_id=1, _mode="ambient", _trace_id=None,
            _run_pipeline_task=AsyncMock(), _on_pipeline_done=AsyncMock(),
        )
        await InferenceMixin._start_voice_pipeline(host, "seg1", "hello")
        await host._pipeline_task
        host._run_pipeline_task.assert_awaited_once()
        host._on_pipeline_done.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_voice_chat_mode_skips_on_pipeline_done(self):
        """mode=voice_chat → 不调用 _on_pipeline_done（覆盖 41 的 False 分支）。"""
        host = SimpleNamespace(
            user_id=1, _mode="voice_chat", _trace_id=None,
            _run_pipeline_task=AsyncMock(), _on_pipeline_done=AsyncMock(),
        )
        await InferenceMixin._start_voice_pipeline(host, "seg1", "hello")
        await host._pipeline_task
        host._run_pipeline_task.assert_awaited_once()
        host._on_pipeline_done.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trace_id_propagated_into_task(self):
        """_trace_id 非空 → trace_id_var.set(tid)（覆盖 35-36 真分支）。"""
        host = SimpleNamespace(
            user_id=1, _mode="ambient", _trace_id="t-1",
            _run_pipeline_task=AsyncMock(), _on_pipeline_done=AsyncMock(),
        )
        with patch("apps.voice.consumer_inference.trace_id_var") as tvar:
            await InferenceMixin._start_voice_pipeline(host, "seg1", "hello")
            await host._pipeline_task
            tvar.set.assert_called_once_with("t-1")

    @pytest.mark.asyncio
    async def test_trace_id_none_skips_set(self):
        """_trace_id 为空 → 不调用 trace_id_var.set（覆盖 35 假分支）。"""
        host = SimpleNamespace(
            user_id=1, _mode="ambient", _trace_id=None,
            _run_pipeline_task=AsyncMock(), _on_pipeline_done=AsyncMock(),
        )
        with patch("apps.voice.consumer_inference.trace_id_var") as tvar:
            await InferenceMixin._start_voice_pipeline(host, "seg1", "hello")
            await host._pipeline_task
            tvar.set.assert_not_called()


# =====================================================================
# 分组 B — _run_pipeline_task 正常 + 异常（line 50-61）
# =====================================================================
class TestRunPipelineTask:
    @pytest.mark.asyncio
    async def test_run_pipeline_happy_path(self):
        """pipeline_user_id=None → target=self.user_id，connection_user_id=None。"""
        host = SimpleNamespace(user_id=5)
        with patch(
            "apps.voice.services.voice_pipeline.VoicePipeline.run_pipeline",
            new=AsyncMock(),
        ) as rp:
            await InferenceMixin._run_pipeline_task(
                host, "seg1", "txt", "ambient", speaker_id="spk",
                pipeline_user_id=None,
            )
        rp.assert_awaited_once()
        kwargs = rp.await_args.kwargs
        assert kwargs["user_id"] == 5
        assert kwargs["text"] == "txt"
        assert kwargs["segment_id"] == "seg1"
        assert kwargs["mode"] == "ambient"
        assert kwargs["speaker_id"] == "spk"
        assert kwargs["connection_user_id"] is None

    @pytest.mark.asyncio
    async def test_run_pipeline_cross_user_sets_connection_user_id(self):
        """pipeline_user_id != self.user_id → connection_user_id=self.user_id（覆盖 56）。"""
        host = SimpleNamespace(user_id=1)
        with patch(
            "apps.voice.services.voice_pipeline.VoicePipeline.run_pipeline",
            new=AsyncMock(),
        ) as rp:
            await InferenceMixin._run_pipeline_task(
                host, "seg1", "txt", "ambient", pipeline_user_id=99,
            )
        kwargs = rp.await_args.kwargs
        assert kwargs["user_id"] == 99
        assert kwargs["connection_user_id"] == 1

    @pytest.mark.asyncio
    async def test_run_pipeline_exception_sends_pipeline_error(self):
        """run_pipeline 抛异常 → _send_json 发 PIPELINE_ERROR、recoverable=True（覆盖 58-61）。"""
        host = SimpleNamespace(user_id=1, _send_json=AsyncMock())
        with patch(
            "apps.voice.services.voice_pipeline.VoicePipeline.run_pipeline",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await InferenceMixin._run_pipeline_task(
                host, "seg1", "txt", "ambient",
            )
        host._send_json.assert_awaited_once()
        payload = host._send_json.await_args.args[0]
        assert payload["type"] == "error"
        assert payload["data"]["code"] == "PIPELINE_ERROR"
        assert payload["data"]["recoverable"] is True


# =====================================================================
# 分组 C — _on_pipeline_done pending 分支（line 69-94）
# =====================================================================
class TestOnPipelineDone:
    @pytest.mark.asyncio
    async def test_no_pending_returns_early(self):
        """_pending_text 为空 → 提前返回，不启动新管道（覆盖 69-71）。"""
        host = SimpleNamespace(_pending_text=None, _start_voice_pipeline=AsyncMock())
        await InferenceMixin._on_pipeline_done(host)
        host._start_voice_pipeline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pending_fed_to_per_speaker_aggregator(self):
        """per-speaker 命中 + is_speaking → agg.add(pending)（覆盖 72-79、82-87）。"""
        agg = MagicMock()
        agg.add = AsyncMock()
        agg.state = "IDLE"
        host = SimpleNamespace(
            user_id=1, _pending_text="hi", _pending_speaker_user_id=7,
            _speaker_aggregators={7: agg}, _is_speaking=True,
            _start_voice_pipeline=AsyncMock(),
        )
        await InferenceMixin._on_pipeline_done(host)
        agg.add.assert_awaited_once_with("hi")
        host._start_voice_pipeline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pending_fed_to_legacy_aggregator_when_collecting(self):
        """无 per-speaker，legacy aggregator COLLECTING → legacy.add（覆盖 80-82）。"""
        agg = MagicMock()
        agg.add = AsyncMock()
        agg.state = "COLLECTING"
        host = SimpleNamespace(
            user_id=1, _pending_text="hi", _pending_speaker_user_id=None,
            _speaker_aggregators={}, _aggregator=agg, _is_speaking=False,
            _start_voice_pipeline=AsyncMock(),
        )
        await InferenceMixin._on_pipeline_done(host)
        agg.add.assert_awaited_once_with("hi")

    @pytest.mark.asyncio
    async def test_pending_flushed_starts_new_pipeline(self):
        """未在说话且非 COLLECTING → flush 启动新管道（覆盖 88-94）。"""
        host = SimpleNamespace(
            user_id=1, _pending_text="hi", _pending_speaker_user_id=7,
            _speaker_aggregators={}, _aggregator=None, _is_speaking=False,
            _current_segment_id="segX", _start_voice_pipeline=AsyncMock(),
        )
        await InferenceMixin._on_pipeline_done(host)
        host._start_voice_pipeline.assert_awaited_once_with(
            "segX", "hi", pipeline_user_id=7,
        )


# =====================================================================
# 分组 D — _idle_timeout_loop 超时路径（line 102-112）
# =====================================================================
class TestIdleTimeoutLoop:
    @pytest.mark.asyncio
    async def test_ambient_mode_returns_immediately(self):
        """mode=ambient → 立即返回，不发消息不关连接（覆盖 102-103）。"""
        host = SimpleNamespace(
            _mode="ambient", _send_json=AsyncMock(), close=AsyncMock(),
        )
        await InferenceMixin._idle_timeout_loop(host)
        host._send_json.assert_not_awaited()
        host.close.assert_not_awaited()

    @pytest.mark.asyncio
    @override_settings(VOICE_IDLE_TIMEOUT=1)
    async def test_idle_timeout_closes_connection(self):
        """voice_chat 且超时 → 发 session.closed + close(4003)（覆盖 104-112）。"""
        host = SimpleNamespace(
            user_id=1, _mode="voice_chat", _last_activity=time.time() - 3600,
            _send_json=AsyncMock(), close=AsyncMock(),
        )
        with patch(
            "apps.voice.consumer_inference.asyncio.sleep", new=AsyncMock(),
        ):
            await InferenceMixin._idle_timeout_loop(host)
        host._send_json.assert_awaited_once()
        payload = host._send_json.await_args.args[0]
        assert payload["type"] == "session.closed"
        assert payload["data"]["status"] == "idle_timeout"
        host.close.assert_awaited_once_with(code=4003)
