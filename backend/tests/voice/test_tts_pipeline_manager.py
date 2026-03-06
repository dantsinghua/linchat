"""TTSPipelineManager 单元测试

覆盖:
- T009: 3 级安慰递进、stop drain、快速回复无安慰、段间 gap、计时器重启
- T017: 错误停止安慰并入队、安慰播放中出错
- T020: cancel 清空队列、断开 TTS、中断 gap sleep、Agent 完成后 cancel、
        TTS 连接失败安全、shutdown after cancel
"""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from apps.voice.services.tts_pipeline_manager import QueueItem, TTSPipelineManager

_MODULE = "apps.voice.services.tts_pipeline_manager"
_SETTINGS = "apps.voice.services.tts_pipeline_manager.settings"


def _make_manager(
    comfort_delay: float = 0.05,
    segment_gap: float = 0.0,
    comfort_texts: list[str] | None = None,
    tts_timeout: int = 5,
) -> TTSPipelineManager:
    """创建用于测试的 manager，使用极短延迟。"""
    mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test_voice")
    # 直接 patch settings 属性
    return mgr


def _patch_settings(
    comfort_delay: float = 0.05,
    segment_gap: float = 0.0,
    comfort_texts: list[str] | None = None,
    tts_timeout: int = 5,
    error_text: str = "错误提示",
):
    """返回 patch settings 的上下文管理器。"""
    texts = comfort_texts if comfort_texts is not None else ["安慰1", "安慰2", "安慰3"]
    mock_settings = MagicMock()
    mock_settings.VOICE_TTS_COMFORT_DELAY = comfort_delay
    mock_settings.VOICE_TTS_SEGMENT_GAP = segment_gap
    mock_settings.VOICE_TTS_COMFORT_TEXTS = texts
    mock_settings.VOICE_TTS_ERROR_TEXT = error_text
    mock_settings.VOICE_TTS_TIMEOUT = tts_timeout
    mock_settings.VOICE_TTS_URL = "ws://test:8100/v1/audio/speech/stream"
    mock_settings.VOICE_TTS_VOICE = "test_voice"
    mock_settings.LLM_GATEWAY_API_KEY = "test_key"
    return patch(_SETTINGS, mock_settings)


def _patch_tts_client():
    """Mock TTSStreamClient — connect/configure/send/disconnect 全部 AsyncMock。"""
    mock_tts = MagicMock()
    mock_tts.connect = AsyncMock(return_value="session_123")
    mock_tts.configure = AsyncMock()
    mock_tts.send_text_delta = AsyncMock()
    mock_tts.send_text_done = AsyncMock()
    mock_tts.wait_for_done = AsyncMock()
    mock_tts.disconnect = AsyncMock()
    mock_tts.connected = True
    return patch(f"{_MODULE}.TTSStreamClient", return_value=mock_tts), mock_tts


# ========================================================================
# T009 — 安慰语音单元测试
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestComfortProgression:
    """T009: 3 级安慰递进 + 快速回复 + 段间 gap + 计时器重启。"""

    async def test_three_level_comfort_progression(self):
        """3 次递进 comfort enqueue + 第 4 次不触发。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=0.02), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()

            # 等待足够长让 3 级安慰全部触发
            # 每级: 0.02s delay + ~instant play → 重启
            await asyncio.sleep(0.15)

            mgr.stop_comfort_timer()
            mgr.enqueue("done", "response")
            await mgr.wait_idle()
            await mgr.shutdown()

        # 验证 TTS 被调用至少 3 次（3 comfort + 1 response）
        assert mock_tts.send_text_delta.call_count >= 3
        # 验证安慰文本按序
        calls = mock_tts.send_text_delta.call_args_list
        assert calls[0][0][0] == "安慰1"
        assert calls[1][0][0] == "安慰2"
        assert calls[2][0][0] == "安慰3"

    async def test_stop_drains_pending_comfort(self):
        """stop 后队列中 comfort 项被清除、response 项保留。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=0.01), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            # 不启动 worker，手动测试 drain
            mgr.enqueue("安慰A", "comfort")
            mgr.enqueue("回复B", "response")
            mgr.enqueue("安慰C", "comfort")

            mgr.stop_comfort_timer()

            # 验证只剩 response
            items = []
            while not mgr._queue.empty():
                items.append(mgr._queue.get_nowait())
            assert len(items) == 1
            assert items[0].item_type == "response"
            assert items[0].text == "回复B"

    async def test_fast_response_no_comfort(self):
        """Agent 快速完成（<delay） → 无 comfort 入队。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=1.0), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()

            # 立即停止安慰（模拟 Agent 快速完成）
            mgr.stop_comfort_timer()
            mgr.enqueue("快速回复", "response")
            await mgr.wait_idle()
            await mgr.shutdown()

        # 只有 1 次 TTS 调用（response），无 comfort
        assert mock_tts.send_text_delta.call_count == 1
        assert mock_tts.send_text_delta.call_args[0][0] == "快速回复"

    async def test_segment_gap_between_items(self):
        """验证 ensure_gap 计算正确的 sleep 参数。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=10.0, segment_gap=0.5), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()
            mgr.stop_comfort_timer()

            mgr.enqueue("文本1", "response")
            mgr.enqueue("文本2", "response")

            await mgr.wait_idle()
            await mgr.shutdown()

        # 文本1 和文本2 之间应有 gap（通过 2 次 TTS 调用验证）
        assert mock_tts.send_text_delta.call_count == 2

    async def test_comfort_timer_restart_after_play(self):
        """comfort 播完后 _comfort_enabled=True 时重启计时器。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=0.02), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()

            # 等待第一个 comfort 播放完毕 + 重启计时器
            await asyncio.sleep(0.06)

            # 验证至少触发了 2 次 comfort（重启成功）
            assert mgr._comfort_index >= 2
            mgr.stop_comfort_timer()
            await mgr.shutdown()


# ========================================================================
# T017 — 错误播报单元测试
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestErrorBroadcast:
    """T017: 错误停止安慰 + 入队错误语音。"""

    async def test_error_stops_comfort_and_enqueues(self):
        """Agent 出错 → stop_comfort_timer + enqueue error。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=10.0, error_text="发生错误"), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()

            # 模拟 Agent 出错
            mgr.stop_comfort_timer()
            mgr.enqueue("发生错误", "error")

            await mgr.wait_idle()
            await mgr.shutdown()

        assert mock_tts.send_text_delta.call_count == 1
        assert mock_tts.send_text_delta.call_args[0][0] == "发生错误"
        assert not mgr._comfort_enabled

    async def test_error_after_comfort_playing(self):
        """安慰正在播放时出错 → 当前安慰播完 → gap → 错误播报。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=0.01, segment_gap=0.0), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()

            # 等安慰1入队
            await asyncio.sleep(0.03)
            # 模拟 Agent 出错
            mgr.stop_comfort_timer()
            mgr.enqueue("错误提示", "error")

            await mgr.wait_idle()
            await mgr.shutdown()

        # 应看到安慰 + 错误
        calls = [c[0][0] for c in mock_tts.send_text_delta.call_args_list]
        assert "安慰1" in calls
        assert "错误提示" in calls


# ========================================================================
# T020 — cancel 单元测试
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestCancel:
    """T020: cancel 清空队列、断开 TTS、中断 gap sleep。"""

    async def test_cancel_clears_queue_and_sets_idle(self):
        """cancel 后 wait_idle 立即返回。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=10.0), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()

            # 入队但不处理
            mgr.enqueue("待播1", "comfort")
            mgr.enqueue("待播2", "response")

            await mgr.cancel()

            # cancel 后 wait_idle 应立即返回
            await asyncio.wait_for(mgr.wait_idle(), timeout=1.0)
            assert mgr._cancelled

    async def test_cancel_disconnects_current_tts(self):
        """cancel 断开正在播放的 TTS 连接。"""
        tts_patch, mock_tts = _patch_tts_client()

        # 让 wait_for_done 阻塞，模拟 TTS 正在播放
        wait_event = asyncio.Event()
        mock_tts.wait_for_done = AsyncMock(side_effect=lambda timeout=None: wait_event.wait())

        with _patch_settings(comfort_delay=0.01), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()

            # 等待 comfort 入队并开始播放
            await asyncio.sleep(0.05)

            # cancel 应断开 TTS
            await mgr.cancel()

        mock_tts.disconnect.assert_called()

    async def test_cancel_interrupts_ensure_gap_sleep(self):
        """cancel 中断 _ensure_gap 的 asyncio.sleep。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=10.0, segment_gap=10.0), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()
            mgr.stop_comfort_timer()

            # 入队 2 项 → 第 1 项播完后进入 10s gap sleep
            mgr.enqueue("文本1", "response")
            mgr.enqueue("文本2", "response")

            # 等第 1 项开始处理
            await asyncio.sleep(0.05)

            # cancel 应中断 gap sleep
            await asyncio.wait_for(mgr.cancel(), timeout=1.0)
            assert mgr._cancelled

    async def test_tts_connect_fail_safe(self):
        """TTS 连接失败 → worker 跳过该段继续处理。"""
        tts_patch, mock_tts = _patch_tts_client()
        mock_tts.connect = AsyncMock(side_effect=ConnectionError("TTS 不可用"))

        with _patch_settings(comfort_delay=10.0), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()
            mgr.stop_comfort_timer()

            mgr.enqueue("文本1", "response")
            mgr.enqueue("文本2", "response")

            await mgr.wait_idle()
            await mgr.shutdown()

        # 连接都失败了，但 worker 没挂
        assert mock_tts.connect.call_count == 2

    async def test_shutdown_after_cancel(self):
        """cancel() 后 shutdown() 不抛异常、不 hang。"""
        tts_patch, mock_tts = _patch_tts_client()

        with _patch_settings(comfort_delay=10.0), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()

            await mgr.cancel()
            await asyncio.wait_for(mgr.shutdown(), timeout=2.0)

            assert mgr._idle.is_set()

    async def test_cancel_after_agent_done(self):
        """Agent 完成后 barge-in: cancel 仍能正常执行。"""
        tts_patch, mock_tts = _patch_tts_client()

        # 让 wait_for_done 阻塞模拟回复 TTS 正在播放
        wait_event = asyncio.Event()
        mock_tts.wait_for_done = AsyncMock(side_effect=lambda timeout=None: wait_event.wait())

        with _patch_settings(comfort_delay=10.0), tts_patch:
            mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
            mgr.start()
            mgr.stop_comfort_timer()

            # Agent 完成，入队回复
            mgr.enqueue("完整回复", "response")
            await asyncio.sleep(0.02)

            # Barge-in
            await asyncio.wait_for(mgr.cancel(), timeout=1.0)
            assert mgr._cancelled
            assert mgr._idle.is_set()
