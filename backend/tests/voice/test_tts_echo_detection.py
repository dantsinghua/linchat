"""TTS Echo Detection 测试 (US2)

覆盖:
1. _is_tts_echo 在 Redis voice:tts_playing:{uid} 存在时返回 True
2. 文本相似度 > 0.7 时返回 True
3. TTS 结束后（无 Redis key）返回 False
4. Redis key 不存在且无匹配历史时返回 False
5. tts_router 正确设置 SETEX 标记
6. tts_router 正确清除 DEL 标记
7. TTS 历史文本存储正确（LPUSH + LTRIM）
8. DISCARD 决策结果在 echo 时返回
9. 非 echo 文本进入后续决策层
10. 文本相似度边界测试（0.7 阈值）

Mock 策略:
- core.redis.get_redis -> 控制 Redis 操作
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from apps.voice.services.response_decision_service import (
    DecisionResult,
    ResponseDecisionService,
)

_MODULE = "apps.voice.services.response_decision_service"
_TTS_ROUTER_MODULE = "apps.voice.services.tts_router"


# ============ Fixtures ============


@pytest.fixture
def service():
    """创建 ResponseDecisionService 实例"""
    return ResponseDecisionService()


def _build_redis_mock(
    tts_playing: bool = False,
    tts_history: list[str] | None = None,
):
    """构建 mock Redis，控制 TTS 状态键和历史。

    Args:
        tts_playing: voice:tts_playing:{uid} 是否存在
        tts_history: voice:tts_history:{uid} 的内容列表
    """
    mock_redis = AsyncMock()
    mock_redis.exists = AsyncMock(return_value=1 if tts_playing else 0)
    mock_redis.lrange = AsyncMock(return_value=tts_history or [])
    mock_redis.setex = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)
    mock_redis.lpush = AsyncMock(return_value=1)
    mock_redis.ltrim = AsyncMock(return_value=True)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.scard = AsyncMock(return_value=0)
    mock_redis.aclose = AsyncMock()
    return mock_redis


def _patch_for_echo_only(tts_playing: bool = False, tts_history=None):
    """只 patch echo 检测相关的 Redis 依赖（_is_tts_echo 调用）。"""
    mock_redis = _build_redis_mock(tts_playing=tts_playing, tts_history=tts_history)
    return patch("apps.voice.services.response_decision_service.get_redis",
                 return_value=AsyncMock(return_value=mock_redis)), mock_redis


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ========================================================================
# T1: _is_tts_echo 在 Redis voice:tts_playing:{uid} 存在时返回 True
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestTtsPlayingKeyExists:
    """Redis tts_playing 键存在时应返回 True。"""

    async def test_returns_true_when_tts_playing_key_exists(self, service):
        """Redis voice:tts_playing:{uid} 存在 → 立即返回 True，不查历史。"""
        mock_redis = _build_redis_mock(tts_playing=True)
        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            result = await service._is_tts_echo("你好小鱼", user_id=1)

        assert result is True
        # exists 被调用一次检查 tts_playing key
        mock_redis.exists.assert_called_once()
        called_key = mock_redis.exists.call_args[0][0]
        assert "tts_playing" in called_key
        assert "1" in called_key


# ========================================================================
# T2: 文本相似度 > 0.7 时返回 True
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestTextSimilarityAboveThreshold:
    """文本相似度 > 0.7 时应检测为 TTS echo。"""

    async def test_returns_true_when_similarity_above_threshold(self, service):
        """历史中有相似文本（ratio > 0.7）→ 返回 True。"""
        tts_history = ["今天天气真不错，阳光明媚"]
        mock_redis = _build_redis_mock(tts_playing=False, tts_history=tts_history)

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            # 完全相同的文本，相似度 = 1.0
            result = await service._is_tts_echo("今天天气真不错，阳光明媚", user_id=2)

        assert result is True

    async def test_returns_true_when_similarity_just_above_threshold(self, service):
        """相似度刚好超过 0.7 阈值时返回 True。"""
        # "ABCDEFGHIJ" vs "ABCDEFGHIX" → 9/10 = 0.9
        tts_history = ["ABCDEFGHIJ"]
        mock_redis = _build_redis_mock(tts_playing=False, tts_history=tts_history)

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            result = await service._is_tts_echo("ABCDEFGHIX", user_id=3)

        assert result is True


# ========================================================================
# T3: TTS 结束后（无 Redis key），不同文本返回 False
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestDifferentTextAfterTtsEnds:
    """TTS 结束后（无 playing key），完全不同的文本应返回 False。"""

    async def test_returns_false_when_no_key_and_different_text(self, service):
        """TTS 已结束，无 playing key，历史中无匹配文本 → False。"""
        tts_history = ["AI 说的内容"]
        mock_redis = _build_redis_mock(tts_playing=False, tts_history=tts_history)

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            # 完全不同的文本
            result = await service._is_tts_echo("帮我查一下今天的新闻", user_id=4)

        assert result is False


# ========================================================================
# T4: Redis key 不存在且无匹配历史时返回 False
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestNoKeyNoHistory:
    """既无 playing key 也无匹配历史时返回 False。"""

    async def test_returns_false_when_no_key_no_history(self, service):
        """无 playing key，历史为空 → False。"""
        mock_redis = _build_redis_mock(tts_playing=False, tts_history=[])

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            result = await service._is_tts_echo("小鱼你好", user_id=5)

        assert result is False

    async def test_returns_false_after_tts_state_expires(self, service):
        """TTS 状态过期（key 不存在，history 也为空）→ False。"""
        mock_redis = _build_redis_mock(tts_playing=False, tts_history=None)

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            result = await service._is_tts_echo("随便说点什么", user_id=6)

        assert result is False


# ========================================================================
# T5: tts_router 正确设置 SETEX 标记
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestTtsRouterSetexMarker:
    """mark_tts_start 应设置 SETEX 标记和历史记录。"""

    async def test_mark_tts_start_sets_setex(self):
        """mark_tts_start 应调用 setex 设置 voice:tts_playing:{uid}。"""
        from apps.voice.services.tts_router import TTSRouter

        mock_redis = _build_redis_mock()
        with patch(_TTS_ROUTER_MODULE + ".get_channel_layer", return_value=AsyncMock()):
            router = TTSRouter()

        with patch("apps.voice.services.tts_router.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            await router.mark_tts_start(user_id=10, text="今天天气不错")

        mock_redis.setex.assert_called_once()
        args = mock_redis.setex.call_args[0]
        assert "tts_playing" in args[0]
        assert "10" in args[0]
        # TTL 应该是 30 秒
        assert args[1] == 30

    async def test_mark_tts_start_records_history(self):
        """mark_tts_start 应调用 lpush + ltrim + expire 记录 TTS 历史。"""
        from apps.voice.services.tts_router import TTSRouter

        mock_redis = _build_redis_mock()
        with patch(_TTS_ROUTER_MODULE + ".get_channel_layer", return_value=AsyncMock()):
            router = TTSRouter()

        with patch("apps.voice.services.tts_router.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            await router.mark_tts_start(user_id=10, text="今天天气不错")

        mock_redis.lpush.assert_called_once()
        lpush_args = mock_redis.lpush.call_args[0]
        assert "tts_history" in lpush_args[0]
        assert "10" in lpush_args[0]
        assert lpush_args[1] == "今天天气不错"

        mock_redis.ltrim.assert_called_once()
        ltrim_args = mock_redis.ltrim.call_args[0]
        assert ltrim_args[1] == 0
        assert ltrim_args[2] == 9

        mock_redis.expire.assert_called_once()


# ========================================================================
# T6: tts_router 正确清除 DEL 标记
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestTtsRouterDelMarker:
    """mark_tts_end 应删除 voice:tts_playing:{uid} 键。"""

    async def test_mark_tts_end_deletes_key(self):
        """mark_tts_end 应调用 delete 删除 voice:tts_playing:{uid}。"""
        from apps.voice.services.tts_router import TTSRouter

        mock_redis = _build_redis_mock()
        with patch(_TTS_ROUTER_MODULE + ".get_channel_layer", return_value=AsyncMock()):
            router = TTSRouter()

        with patch("apps.voice.services.tts_router.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            await router.mark_tts_end(user_id=10)

        mock_redis.delete.assert_called_once()
        args = mock_redis.delete.call_args[0]
        assert "tts_playing" in args[0]
        assert "10" in args[0]


# ========================================================================
# T7: TTS 历史文本存储正确（LPUSH + LTRIM）
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestTtsHistoryStorage:
    """TTS 历史存储：LPUSH + LTRIM 保持最多 10 条，EXPIRE 300s。"""

    async def test_history_lpush_ltrim_expire(self):
        """mark_tts_start 中历史记录使用 LPUSH + LTRIM(0,9) + EXPIRE(300)。"""
        from apps.voice.services.tts_router import TTSRouter

        mock_redis = _build_redis_mock()
        with patch(_TTS_ROUTER_MODULE + ".get_channel_layer", return_value=AsyncMock()):
            router = TTSRouter()

        text = "这是一段很长的 TTS 播报文本"
        with patch("apps.voice.services.tts_router.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            await router.mark_tts_start(user_id=20, text=text)

        # LPUSH key text
        mock_redis.lpush.assert_called_once_with(
            "voice:tts_history:20", text
        )
        # LTRIM key 0 9
        mock_redis.ltrim.assert_called_once_with(
            "voice:tts_history:20", 0, 9
        )
        # EXPIRE key 300
        mock_redis.expire.assert_called_once_with(
            "voice:tts_history:20", 300
        )


# ========================================================================
# T8: DISCARD 决策结果在 echo 时返回
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestDiscardDecisionForEcho:
    """当检测到 TTS echo 时，decide() 应返回 DISCARD 结果。"""

    async def test_decide_returns_discard_for_tts_echo(self, service):
        """TTS 正在播放时，decide() 返回 DISCARD。"""
        mock_redis = _build_redis_mock(tts_playing=True)

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            with patch("apps.voice.services.response_decision_service.voice_settings_repo"
                       ".get_or_create", new=AsyncMock(return_value=(MagicMock(wake_words=[]), True))):
                with patch("apps.voice.services.response_decision_service.voice_session_service"
                           ".is_active_conversation", new=AsyncMock(return_value=False)):
                    decision, reason = await service.decide(
                        "AI 说的内容被麦克风采集到了",
                        speaker_id=None, user_id=10, mode="ambient"
                    )

        assert decision == DecisionResult.DISCARD
        assert "tts_echo" in reason

    async def test_discard_reason_includes_detected(self, service):
        """DISCARD 的 reason 应包含 'tts_echo_detected'。"""
        mock_redis = _build_redis_mock(tts_playing=True)

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            with patch("apps.voice.services.response_decision_service.voice_settings_repo"
                       ".get_or_create", new=AsyncMock(return_value=(MagicMock(wake_words=[]), True))):
                with patch("apps.voice.services.response_decision_service.voice_session_service"
                           ".is_active_conversation", new=AsyncMock(return_value=False)):
                    decision, reason = await service.decide(
                        "TTS 回声内容", speaker_id=None, user_id=10, mode="ambient"
                    )

        assert decision == DecisionResult.DISCARD
        assert reason == "tts_echo_detected"


# ========================================================================
# T9: 非 echo 文本进入后续决策层
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestNonEchoPassesThrough:
    """非 echo 文本应通过 Level 0，进入后续决策链。"""

    async def test_non_echo_text_passes_to_next_level(self, service):
        """无 TTS playing key 且无匹配历史 → 不返回 DISCARD，进入后续层。"""
        mock_redis = _build_redis_mock(tts_playing=False, tts_history=[])

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            with patch("apps.voice.services.response_decision_service.voice_settings_repo"
                       ".get_or_create", new=AsyncMock(return_value=(MagicMock(wake_words=["小鱼"]), True))):
                with patch("apps.voice.services.response_decision_service.voice_session_service"
                           ".is_active_conversation", new=AsyncMock(return_value=False)):
                    decision, reason = await service.decide(
                        "小鱼你好",
                        speaker_id=None, user_id=11, mode="ambient"
                    )

        # 应通过 echo 检测，进入唤醒词层 → RESPOND
        assert decision != DecisionResult.DISCARD
        assert decision == DecisionResult.RESPOND

    async def test_non_echo_does_not_return_discard(self, service):
        """TTS 未播放，历史为空，任意文本不返回 DISCARD。"""
        mock_redis = _build_redis_mock(tts_playing=False, tts_history=None)

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            with patch("apps.voice.services.response_decision_service.voice_settings_repo"
                       ".get_or_create", new=AsyncMock(return_value=(MagicMock(wake_words=[]), True))):
                with patch("apps.voice.services.response_decision_service.voice_session_service"
                           ".is_active_conversation", new=AsyncMock(return_value=False)):
                    decision, _ = await service.decide(
                        "普通对话内容",
                        speaker_id=None, user_id=12, mode="ambient"
                    )

        assert decision != DecisionResult.DISCARD


# ========================================================================
# T10: 文本相似度边界测试（0.7 阈值）
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestSimilarityBoundary:
    """相似度边界：> 0.7 返回 True，<= 0.7 返回 False。"""

    async def test_similarity_exactly_at_threshold_returns_false(self, service):
        """相似度恰好等于 0.7 时不触发 echo（阈值条件为 > 0.7）。"""
        # 构造一对恰好相似度 = 0.7 的字符串
        # "AAAAAAA___" vs "AAAAAAA+++": 7/10 common → ratio ≈ 0.7 (SequenceMatcher)
        # 使用足够简单的字符串便于计算
        # "abcdefg" vs "abcdefxyz" → common=7, len=7+9=16 → ratio=14/16=0.875 (不对)
        # 精确方法：SequenceMatcher(None, "1234567890", "1234560000").ratio()
        # = 2*7/20 = 0.7
        tts_text = "1234567890"
        test_text = "1234560000"
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, test_text, tts_text).ratio()
        # 验证测试数据构造正确
        assert abs(ratio - 0.7) < 0.01, f"Expected ~0.7, got {ratio}"

        mock_redis = _build_redis_mock(tts_playing=False, tts_history=[tts_text])

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            result = await service._is_tts_echo(test_text, user_id=99)

        # ratio == 0.7, 条件是 > 0.7 → 应返回 False
        assert result is False

    async def test_similarity_just_above_threshold_returns_true(self, service):
        """相似度略高于 0.7 时触发 echo。"""
        # "12345678" vs "12345670": 7/8 common chars → 较高相似度
        tts_text = "AAAAAAAAAB"   # 10 chars
        test_text = "AAAAAAAAB"   # 9 chars → 大量公共字符
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, test_text, tts_text).ratio()
        assert ratio > 0.7, f"Expected > 0.7, got {ratio}"

        mock_redis = _build_redis_mock(tts_playing=False, tts_history=[tts_text])

        with patch("apps.voice.services.response_decision_service.get_redis",
                   new=AsyncMock(return_value=mock_redis)):
            result = await service._is_tts_echo(test_text, user_id=98)

        assert result is True
