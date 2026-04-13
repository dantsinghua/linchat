"""UtteranceAggregator 单元测试

覆盖:
- 单段话语 → 超时 → 聚合回调（正确文本、utterance_count、时间戳）
- 多段话语 → 超时 → 空格拼接聚合
- 新话语重置计时器（部分超时后追加 → 重新计时）
- max_buffer_size 自动 flush（达到上限立即回调，无需等超时）
- flush() 立即聚合（不等超时）
- reset() 清空缓冲区（不触发回调）
- 空缓冲区超时 → 无回调
- add() 空字符串/纯空白 → 忽略
- 状态流转验证（IDLE → COLLECTING → AGGREGATED → IDLE）
- destroy() 清理

Mock 策略:
- django.conf.settings — VOICE_AMBIENT_AGGREGATE_TIMEOUT=0.1, VOICE_AMBIENT_MAX_BUFFER_SIZE=3

覆盖率目标: >= 95%
"""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from apps.voice.services.utterance_aggregator import (
    AggregatedMessage,
    UtteranceAggregator,
)


# AggregatorState 枚举已重构为字符串常量，兼容旧测试断言
class AggregatorState:
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    AGGREGATED = "AGGREGATED"

_SETTINGS = "apps.voice.services.utterance_aggregator.settings"


def _mock_settings(
    timeout: float = 0.1,
    max_buffer_size: int = 3,
) -> MagicMock:
    """创建 mock settings 对象。"""
    mock = MagicMock()
    mock.VOICE_AMBIENT_AGGREGATE_TIMEOUT = timeout
    mock.VOICE_AMBIENT_MAX_BUFFER_SIZE = max_buffer_size
    return mock


def _make_aggregator(
    callback: AsyncMock | None = None,
    timeout: float | None = None,
    max_buffer_size: int | None = None,
) -> tuple[UtteranceAggregator, list[AggregatedMessage]]:
    """创建聚合器 + 收集回调结果的列表。

    Returns:
        (aggregator, collected_results)
    """
    collected: list[AggregatedMessage] = []

    async def _on_aggregated(msg: AggregatedMessage) -> None:
        collected.append(msg)
        if callback:
            await callback(msg)

    with patch(_SETTINGS, _mock_settings()):
        agg = UtteranceAggregator(
            on_aggregated=_on_aggregated,
            timeout=timeout,
            max_buffer_size=max_buffer_size,
        )
    return agg, collected


# ========================================================================
# 单段话语 → 超时 → 聚合回调
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestSingleUtteranceTimeout:
    """单段话语超时后触发聚合。"""

    async def test_single_utterance_aggregated_on_timeout(self):
        """添加 1 段文本 → 等待超时 → 回调触发，text/count/时间戳正确。"""
        agg, collected = _make_aggregator(timeout=0.05)

        await agg.add("你好")
        assert agg.state == AggregatorState.COLLECTING
        assert agg.buffer_count == 1

        # 等待超时触发聚合
        await asyncio.sleep(0.1)

        assert len(collected) == 1
        msg = collected[0]
        assert msg.text == "你好"
        assert msg.utterance_count == 1
        assert msg.first_ts == msg.last_ts
        assert msg.first_ts > 0
        assert agg.state == AggregatorState.IDLE
        assert agg.buffer_count == 0


# ========================================================================
# 多段话语 → 超时 → 空格拼接聚合
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestMultipleUtterancesTimeout:
    """多段话语超时后空格拼接聚合。"""

    async def test_multiple_utterances_space_joined(self):
        """添加 2 段文本 → 超时 → 空格拼接。"""
        agg, collected = _make_aggregator(timeout=0.08, max_buffer_size=10)

        await agg.add("今天")
        await agg.add("天气不错")
        assert agg.buffer_count == 2

        await asyncio.sleep(0.15)

        assert len(collected) == 1
        msg = collected[0]
        assert msg.text == "今天 天气不错"
        assert msg.utterance_count == 2
        assert msg.first_ts <= msg.last_ts

    async def test_three_utterances_joined(self):
        """添加 3 段（未达 max_buffer_size=10） → 超时拼接。"""
        agg, collected = _make_aggregator(timeout=0.08, max_buffer_size=10)

        await agg.add("我想")
        await agg.add("问一个")
        await agg.add("问题")
        assert agg.buffer_count == 3

        await asyncio.sleep(0.15)

        assert len(collected) == 1
        assert collected[0].text == "我想 问一个 问题"
        assert collected[0].utterance_count == 3


# ========================================================================
# 新话语重置计时器
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestTimerReset:
    """新话语追加时重置超时计时器。"""

    async def test_new_utterance_resets_timer(self):
        """添加文本 → 等待部分超时 → 再添加 → 重新计时。"""
        agg, collected = _make_aggregator(timeout=0.1, max_buffer_size=10)

        await agg.add("第一段")

        # 等待 70% 超时（尚未触发）
        await asyncio.sleep(0.07)
        assert len(collected) == 0, "超时前不应触发回调"

        # 追加第二段 → 重置计时器
        await agg.add("第二段")
        assert agg.buffer_count == 2

        # 再等 70%（此时若未重置，原始计时器早已超时）
        await asyncio.sleep(0.07)
        assert len(collected) == 0, "计时器重置后不应提前触发"

        # 等待新计时器超时
        await asyncio.sleep(0.06)
        assert len(collected) == 1
        assert collected[0].text == "第一段 第二段"
        assert collected[0].utterance_count == 2


# ========================================================================
# max_buffer_size 自动 flush
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestMaxBufferAutoFlush:
    """达到 max_buffer_size 时自动立即 flush。"""

    async def test_auto_flush_at_max_buffer(self):
        """添加 max_buffer_size 段 → 立即触发回调，无需等超时。"""
        agg, collected = _make_aggregator(timeout=1.0, max_buffer_size=3)

        await agg.add("A")
        await agg.add("B")
        assert len(collected) == 0, "未达上限不应触发"

        await agg.add("C")  # 第 3 段 → 触发

        # 不需要 sleep，auto flush 是同步（await）完成的
        assert len(collected) == 1
        msg = collected[0]
        assert msg.text == "A B C"
        assert msg.utterance_count == 3
        assert agg.buffer_count == 0
        assert agg.state == AggregatorState.IDLE

    async def test_auto_flush_does_not_start_timer(self):
        """auto flush 后不应有残留 timer。"""
        agg, collected = _make_aggregator(timeout=0.05, max_buffer_size=2)

        await agg.add("X")
        await agg.add("Y")  # 触发 auto flush

        assert len(collected) == 1

        # 等超时期间不应再触发
        await asyncio.sleep(0.1)
        assert len(collected) == 1, "auto flush 后不应有多余回调"

    async def test_continue_after_auto_flush(self):
        """auto flush 后可继续添加新话语。"""
        agg, collected = _make_aggregator(timeout=0.05, max_buffer_size=2)

        await agg.add("A")
        await agg.add("B")  # 第一次 auto flush
        assert len(collected) == 1

        await agg.add("C")  # 新轮次
        assert agg.buffer_count == 1

        await asyncio.sleep(0.1)
        assert len(collected) == 2
        assert collected[1].text == "C"


# ========================================================================
# flush() 立即触发聚合
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestFlush:
    """flush() 立即聚合，不等超时。"""

    async def test_flush_immediate_aggregation(self):
        """添加文本 → flush → 立即回调。"""
        agg, collected = _make_aggregator(timeout=10.0, max_buffer_size=100)

        await agg.add("停止词触发")
        assert len(collected) == 0

        await agg.flush()

        assert len(collected) == 1
        assert collected[0].text == "停止词触发"
        assert collected[0].utterance_count == 1
        assert agg.buffer_count == 0
        assert agg.state == AggregatorState.IDLE

    async def test_flush_multiple_utterances(self):
        """多段文本 → flush → 空格拼接。"""
        agg, collected = _make_aggregator(timeout=10.0, max_buffer_size=100)

        await agg.add("你好")
        await agg.add("世界")
        await agg.flush()

        assert len(collected) == 1
        assert collected[0].text == "你好 世界"
        assert collected[0].utterance_count == 2

    async def test_flush_empty_buffer_no_callback(self):
        """空缓冲区 flush → 不触发回调。"""
        agg, collected = _make_aggregator(timeout=10.0)

        await agg.flush()

        assert len(collected) == 0
        assert agg.state == AggregatorState.IDLE

    async def test_flush_cancels_timer(self):
        """flush 后不再有超时回调。"""
        agg, collected = _make_aggregator(timeout=0.05, max_buffer_size=100)

        await agg.add("文本")
        await agg.flush()
        assert len(collected) == 1

        # 等超时期间不应再触发
        await asyncio.sleep(0.1)
        assert len(collected) == 1, "flush 后 timer 应已取消"


# ========================================================================
# reset() 清空缓冲区不触发回调
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestReset:
    """reset() 清空缓冲区，不触发回调。"""

    async def test_reset_clears_buffer(self):
        """reset 后 buffer_count=0，状态 IDLE。"""
        agg, collected = _make_aggregator(timeout=10.0)

        await agg.add("即将清除")
        assert agg.buffer_count == 1
        assert agg.state == AggregatorState.COLLECTING

        agg.reset()

        assert agg.buffer_count == 0
        assert agg.state == AggregatorState.IDLE
        assert len(collected) == 0

    async def test_reset_cancels_timer(self):
        """reset 后不再有超时回调。"""
        agg, collected = _make_aggregator(timeout=0.05)

        await agg.add("文本")
        agg.reset()

        await asyncio.sleep(0.1)
        assert len(collected) == 0, "reset 后 timer 应已取消"

    async def test_reset_then_add_new(self):
        """reset 后可重新添加话语并正常触发。"""
        agg, collected = _make_aggregator(timeout=0.05)

        await agg.add("旧文本")
        agg.reset()

        await agg.add("新文本")
        await asyncio.sleep(0.1)

        assert len(collected) == 1
        assert collected[0].text == "新文本"


# ========================================================================
# 空缓冲区超时 → 无回调
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestEmptyBufferTimeout:
    """空缓冲区不会产生无效回调。"""

    async def test_no_add_no_callback(self):
        """不添加任何文本 → 无回调。"""
        agg, collected = _make_aggregator(timeout=0.05)

        await asyncio.sleep(0.1)
        assert len(collected) == 0

    async def test_add_then_flush_then_timeout_no_double(self):
        """add → flush → 超时期间不重复回调。"""
        agg, collected = _make_aggregator(timeout=0.05)

        await agg.add("文本")
        await agg.flush()
        assert len(collected) == 1

        await asyncio.sleep(0.1)
        assert len(collected) == 1, "已 flush 的缓冲区不应再次触发"


# ========================================================================
# add() 空字符串/纯空白 → 忽略
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestIgnoreEmptyText:
    """空字符串或纯空白文本被忽略。"""

    async def test_empty_string_ignored(self):
        """空字符串不进入缓冲区。"""
        agg, collected = _make_aggregator(timeout=0.05)

        await agg.add("")
        assert agg.buffer_count == 0
        assert agg.state == AggregatorState.IDLE

    async def test_whitespace_only_ignored(self):
        """纯空白不进入缓冲区。"""
        agg, collected = _make_aggregator(timeout=0.05)

        await agg.add("   ")
        await agg.add("\t\n")
        assert agg.buffer_count == 0

    async def test_mixed_empty_and_valid(self):
        """混合空白和有效文本 → 只保留有效。"""
        agg, collected = _make_aggregator(timeout=0.05)

        await agg.add("")
        await agg.add("有效")
        await agg.add("  ")
        assert agg.buffer_count == 1

        await asyncio.sleep(0.1)
        assert len(collected) == 1
        assert collected[0].text == "有效"
        assert collected[0].utterance_count == 1

    async def test_empty_add_does_not_reset_timer(self):
        """空文本不应重置已有 timer。"""
        agg, collected = _make_aggregator(timeout=0.08, max_buffer_size=10)

        await agg.add("真实文本")
        await asyncio.sleep(0.05)

        # 空文本不应影响 timer
        await agg.add("")
        await agg.add("   ")

        # 原始 timer 应在 0.08s 后触发
        await asyncio.sleep(0.05)
        assert len(collected) == 1
        assert collected[0].text == "真实文本"


# ========================================================================
# 状态流转验证
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestStateTransitions:
    """验证 state 属性在各阶段的正确值。"""

    async def test_idle_collecting_aggregated_idle(self):
        """IDLE → add → COLLECTING → timeout → (AGGREGATED during callback) → IDLE。"""
        states_during_callback: list[AggregatorState] = []

        async def _capture_state(msg: AggregatedMessage) -> None:
            # 回调执行期间状态应为 AGGREGATED
            states_during_callback.append(agg.state)

        with patch(_SETTINGS, _mock_settings()):
            agg = UtteranceAggregator(
                on_aggregated=_capture_state,
                timeout=0.05,
            )

        assert agg.state == AggregatorState.IDLE

        await agg.add("测试")
        assert agg.state == AggregatorState.COLLECTING

        await asyncio.sleep(0.1)

        assert len(states_during_callback) == 1
        assert states_during_callback[0] == AggregatorState.AGGREGATED
        assert agg.state == AggregatorState.IDLE


# ========================================================================
# destroy() 清理
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestDestroy:
    """destroy() 取消所有任务并清理状态。"""

    async def test_destroy_clears_state(self):
        """destroy 后 buffer 清空、状态 IDLE。"""
        agg, collected = _make_aggregator(timeout=10.0)

        await agg.add("待销毁")
        assert agg.buffer_count == 1

        agg.destroy()

        assert agg.buffer_count == 0
        assert agg.state == AggregatorState.IDLE

    async def test_destroy_cancels_timer(self):
        """destroy 后不再有超时回调。"""
        agg, collected = _make_aggregator(timeout=0.05)

        await agg.add("文本")
        agg.destroy()

        await asyncio.sleep(0.1)
        assert len(collected) == 0


# ========================================================================
# Properties 验证
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestProperties:
    """buffer_count / timeout_remaining 属性。"""

    async def test_buffer_count(self):
        """buffer_count 反映缓冲区话语数。"""
        agg, _ = _make_aggregator(timeout=10.0, max_buffer_size=10)

        assert agg.buffer_count == 0
        await agg.add("A")
        assert agg.buffer_count == 1
        await agg.add("B")
        assert agg.buffer_count == 2

    async def test_timeout_remaining_with_timer(self):
        """有活跃 timer 时 timeout_remaining > 0。"""
        agg, _ = _make_aggregator(timeout=0.5, max_buffer_size=10)

        assert agg.timeout_remaining == 0.0
        await agg.add("文本")
        assert agg.timeout_remaining > 0.0

    async def test_timeout_remaining_no_timer(self):
        """无 timer 时 timeout_remaining == 0。"""
        agg, _ = _make_aggregator(timeout=10.0)

        assert agg.timeout_remaining == 0.0


# ========================================================================
# 回调异常安全
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestCallbackErrorSafety:
    """回调抛异常不影响聚合器状态恢复。"""

    async def test_callback_exception_recovers_to_idle(self):
        """回调异常后状态恢复到 IDLE。"""

        async def _bad_callback(msg: AggregatedMessage) -> None:
            raise RuntimeError("回调出错")

        with patch(_SETTINGS, _mock_settings()):
            agg = UtteranceAggregator(
                on_aggregated=_bad_callback,
                timeout=0.05,
            )

        await agg.add("触发回调")
        await asyncio.sleep(0.1)

        # 即使回调抛异常，状态应恢复 IDLE
        assert agg.state == AggregatorState.IDLE
        assert agg.buffer_count == 0


# ========================================================================
# 默认参数从 settings 获取
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestDefaultSettings:
    """不传 timeout/max_buffer_size 时从 settings 获取。"""

    async def test_defaults_from_settings(self):
        """验证默认值来自 django.conf.settings。"""
        mock = _mock_settings(timeout=0.07, max_buffer_size=5)
        with patch(_SETTINGS, mock):
            agg = UtteranceAggregator(on_aggregated=AsyncMock())

        assert agg._timeout == 0.07
        assert agg._max_buffer_size == 5

    async def test_explicit_params_override_settings(self):
        """显式传参覆盖 settings。"""
        mock = _mock_settings(timeout=999, max_buffer_size=999)
        with patch(_SETTINGS, mock):
            agg = UtteranceAggregator(
                on_aggregated=AsyncMock(),
                timeout=0.1,
                max_buffer_size=3,
            )

        assert agg._timeout == 0.1
        assert agg._max_buffer_size == 3
