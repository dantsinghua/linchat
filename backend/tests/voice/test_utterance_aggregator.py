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
    adaptive_flush: bool = False,
    sentence_end_chars: str = "。！？!?…",
) -> MagicMock:
    """创建 mock settings 对象。

    batch-32：默认 adaptive_flush=False → 守护 flag off 行为与旧版逐字节一致。
    """
    mock = MagicMock()
    mock.VOICE_AMBIENT_AGGREGATE_TIMEOUT = timeout
    mock.VOICE_AMBIENT_MAX_BUFFER_SIZE = max_buffer_size
    mock.VOICE_AMBIENT_ADAPTIVE_FLUSH_ENABLED = adaptive_flush
    mock.VOICE_AMBIENT_SENTENCE_END_CHARS = sentence_end_chars
    return mock


def _make_aggregator(
    callback: AsyncMock | None = None,
    timeout: float | None = None,
    max_buffer_size: int | None = None,
    adaptive_flush: bool | None = None,
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
            adaptive_flush=adaptive_flush,
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


# ========================================================================
# batch-32：聚合窗口自适应即时 flush（句末标点即时聚合，超时降为兜底上限）
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestAdaptiveFlush:
    """句末标点即时 flush + flag off 守护 + 过早 flush 拆句矩阵。"""

    async def test_m1_sentence_end_immediate_flush(self):
        """M1：句末标点结尾 → 不等 timeout 立即聚合，flush_reason=sentence_end。"""
        agg, collected = _make_aggregator(
            timeout=1.0, max_buffer_size=10, adaptive_flush=True
        )

        await agg.add("今天天气怎么样？")

        # 无需 sleep：即时 flush 走 await _do_aggregate 同步完成
        assert len(collected) == 1
        assert collected[0].text == "今天天气怎么样？"
        assert agg._last_flush_reason == "sentence_end"
        assert agg.buffer_count == 0
        assert agg.state == AggregatorState.IDLE
        assert agg._timer_task is None

    async def test_m2_question_particle_no_immediate_flush(self):
        """M2：语气助词（吗/无标点）不触发即时 flush，仅按标点判定，防误判。"""
        agg, collected = _make_aggregator(
            timeout=1.0, max_buffer_size=10, adaptive_flush=True
        )

        await agg.add("你在吗")

        assert len(collected) == 0, "无句末标点不应即时 flush"
        assert agg.buffer_count == 1
        assert agg._timer_task is not None

    async def test_m3_no_punctuation_falls_back_to_timeout(self):
        """M3：无标点走超时兜底，flush_reason=timeout。"""
        agg, collected = _make_aggregator(
            timeout=0.05, max_buffer_size=10, adaptive_flush=True
        )

        await agg.add("我想想")
        assert len(collected) == 0, "无标点不即时 flush"
        assert agg._timer_task is not None

        await asyncio.sleep(0.1)
        assert len(collected) == 1
        assert agg._last_flush_reason == "timeout"
        assert collected[0].text == "我想想"

    async def test_m4_mid_sentence_comma_not_split(self):
        """M4：句中逗号非句末，不即时 flush；等后续句末标点合并成一条（不拆句）。"""
        agg, collected = _make_aggregator(
            timeout=1.0, max_buffer_size=10, adaptive_flush=True
        )

        await agg.add("我今天，")  # 逗号结尾 → 句中停顿
        assert len(collected) == 0, "逗号非句末不应即时 flush"
        assert agg.buffer_count == 1

        await agg.add("然后去公园。")  # 句末 → 即时 flush，合并
        assert len(collected) == 1, "两段合并为 1 次回调，不拆句"
        assert collected[0].text == "我今天， 然后去公园。"
        assert collected[0].utterance_count == 2
        assert agg._last_flush_reason == "sentence_end"

    async def test_m5_multi_segment_last_with_punctuation(self):
        """M5：多段，末段带句末标点触发即时 flush，合并全部段。"""
        agg, collected = _make_aggregator(
            timeout=1.0, max_buffer_size=10, adaptive_flush=True
        )

        await agg.add("嗯")  # 无标点 → 起 timer
        assert len(collected) == 0

        await agg.add("好的。")  # 句末 → 即时 flush
        assert len(collected) == 1
        assert collected[0].text == "嗯 好的。"
        assert collected[0].utterance_count == 2
        assert agg._last_flush_reason == "sentence_end"

    async def test_m6_flag_off_byte_identical_old_behavior(self):
        """M6：flag off → 句末标点也不即时 flush，仍走 1.5s 超时（回归旧行为，守护）。"""
        agg, collected = _make_aggregator(
            timeout=0.1, max_buffer_size=10, adaptive_flush=False
        )

        await agg.add("你好？")
        assert len(collected) == 0, "flag off 时句末标点不应即时 flush"
        assert agg._timer_task is not None

        await asyncio.sleep(0.15)
        assert len(collected) == 1
        assert agg._last_flush_reason == "timeout"

    async def test_m7_max_buffer_priority_over_immediate_flush(self):
        """M7：达 max_buffer_size 时 max_buffer 分支优先于句末即时 flush。"""
        agg, collected = _make_aggregator(
            timeout=1.0, max_buffer_size=2, adaptive_flush=True
        )

        await agg.add("A")
        await agg.add("B。")  # 达上限 2 → max_buffer 分支先触发

        assert len(collected) == 1
        assert collected[0].utterance_count == 2
        assert agg._last_flush_reason == "max_buffer"

    async def test_m8_immediate_flush_then_new_round(self):
        """M8：句末 flush 后状态回 IDLE，可继续独立的新一轮即时 flush。"""
        agg, collected = _make_aggregator(
            timeout=1.0, max_buffer_size=10, adaptive_flush=True
        )

        await agg.add("第一句。")
        assert len(collected) == 1
        assert agg.state == AggregatorState.IDLE

        await agg.add("再来一句。")
        assert len(collected) == 2
        assert collected[1].text == "再来一句。"
        assert collected[1].utterance_count == 1

    async def test_m9_immediate_flush_cancels_old_timer_no_double(self):
        """M9：无标点起 timer 后句末即时 flush，旧 timer 被 cancel，无二次回调。"""
        agg, collected = _make_aggregator(
            timeout=0.05, max_buffer_size=10, adaptive_flush=True
        )

        await agg.add("无标点")  # 起 timer
        await agg.add("补一句。")  # 即时 flush + cancel 旧 timer
        assert len(collected) == 1

        # 等超过原 timeout，确认旧 timer 未二次触发
        await asyncio.sleep(0.15)
        assert len(collected) == 1, "旧 timer 应被 cancel，无双触发"
