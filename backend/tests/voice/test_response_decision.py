"""响应决策服务测试 (T060)

覆盖:
- decide() 方法全路径测试（7 条决策链 + 空文本兜底）
- 唤醒词精确匹配 -> RESPOND
- 唤醒词模糊匹配（编辑距离 / 拼音相似度） -> RESPOND
- 紧急命令词 -> STOP
- 活跃对话内无唤醒词 -> RESPOND
- 非活跃对话 + 多 speaker -> RECORD_ONLY
- 非活跃 + 单 speaker + 问句特征 -> RESPOND
- 默认 -> RECORD_ONLY
- 自定义唤醒词加载
- 活跃对话超时行为
- 工具函数：_edit_distance, _pinyin_similarity

Mock 策略:
- voice_settings_repo.get_or_create -> 控制唤醒词
- voice_session_service.is_active_conversation -> 控制活跃对话状态
- core.redis.get_redis -> 控制 recent_speakers 集合

覆盖率目标: >= 95%
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.voice.services.response_decision_service import (
    EMERGENCY_STOP_WORDS,
    QUESTION_PARTICLES,
    QUESTION_WORDS,
    DecisionResult,
    ResponseDecisionService,
    _edit_distance,
    _pinyin_similarity,
)


def run_async(coro):
    """在同步测试中运行异步协程"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============ Fixtures ============


@pytest.fixture
def service():
    """创建 ResponseDecisionService 实例"""
    return ResponseDecisionService()


@pytest.fixture
def mock_voice_settings():
    """Mock VoiceSettings 对象"""
    settings_obj = MagicMock()
    settings_obj.wake_words = ["小鱼"]
    return settings_obj


def _build_redis_mock(speaker_count=0):
    """构建 mock Redis 客户端，控制 recent_speakers 集合大小

    Args:
        speaker_count: scard 返回的集合大小
    """
    mock_redis = AsyncMock()
    mock_redis.scard = AsyncMock(return_value=speaker_count)
    mock_redis.aclose = AsyncMock()
    return mock_redis


def _patch_dependencies(
    wake_words=None,
    is_active=False,
    speaker_count=0,
    settings_exception=False,
    redis_exception=False,
):
    """统一 patch 决策服务的三个外部依赖

    Args:
        wake_words: 自定义唤醒词列表，None 则使用默认 ["小鱼"]
        is_active: 活跃对话状态
        speaker_count: 近期活跃说话人数
        settings_exception: 是否让 voice_settings_repo 抛异常
        redis_exception: 是否让 redis 调用抛异常

    Returns:
        三层 patch 装饰器上下文管理器
    """
    if wake_words is None:
        wake_words = ["小鱼"]

    mock_settings_obj = MagicMock()
    mock_settings_obj.wake_words = wake_words

    if settings_exception:
        mock_get_or_create = AsyncMock(side_effect=Exception("DB error"))
    else:
        mock_get_or_create = AsyncMock(return_value=(mock_settings_obj, False))

    if redis_exception:
        mock_redis = AsyncMock()
        mock_redis.scard = AsyncMock(side_effect=Exception("Redis error"))
        mock_redis.aclose = AsyncMock()
    else:
        mock_redis = _build_redis_mock(speaker_count)

    patches = {
        "repo": patch(
            "apps.voice.services.response_decision_service.voice_settings_repo.get_or_create",
            mock_get_or_create,
        ),
        "active": patch(
            "apps.voice.services.response_decision_service.voice_session_service.is_active_conversation",
            AsyncMock(return_value=is_active),
        ),
        "redis": patch(
            "apps.voice.services.response_decision_service.get_redis",
            AsyncMock(return_value=mock_redis),
        ),
    }
    return patches


# ============ 决策链: 紧急命令词 (优先级 1) ============


class TestEmergencyStop:
    """紧急命令词 -> STOP（决策链第 1 步）"""

    @pytest.mark.parametrize("word", list(EMERGENCY_STOP_WORDS))
    def test_exact_emergency_word(self, service, word):
        """紧急命令词精确匹配 -> STOP"""
        patches = _patch_dependencies()
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide(word, None, 1))
        assert result == DecisionResult.STOP
        assert reason == "emergency_stop"

    def test_emergency_word_as_prefix(self, service):
        """紧急命令词作为文本前缀 -> STOP"""
        patches = _patch_dependencies()
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide("停下来别说了", None, 1))
        assert result == DecisionResult.STOP
        assert reason == "emergency_stop"

    def test_emergency_word_not_prefix(self, service):
        """紧急命令词在中间不触发 STOP"""
        patches = _patch_dependencies(is_active=True)
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide("我想让你停一下", None, 1))
        # "停" 不在开头 -> 不触发 STOP，但含唤醒词或活跃对话可 RESPOND
        assert result != DecisionResult.STOP

    def test_empty_text_not_emergency(self, service):
        """空文本 -> RECORD_ONLY（不是 STOP）"""
        patches = _patch_dependencies()
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide("", None, 1))
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "empty_text"

    def test_whitespace_only_text(self, service):
        """纯空白文本 -> RECORD_ONLY"""
        patches = _patch_dependencies()
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide("   ", None, 1))
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "empty_text"


# ============ 决策链: 唤醒词精确匹配 (优先级 2) ============


class TestExactWakeWord:
    """唤醒词精确匹配 -> RESPOND（决策链第 2 步）"""

    def test_exact_wake_word_alone(self, service):
        """文本为唤醒词本身 -> RESPOND"""
        patches = _patch_dependencies(wake_words=["小鱼"])
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide("小鱼", None, 1))
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"

    def test_wake_word_in_sentence(self, service):
        """唤醒词在句中 -> RESPOND"""
        patches = _patch_dependencies(wake_words=["小鱼"])
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("小鱼帮我查一下天气", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"

    def test_wake_word_at_end(self, service):
        """唤醒词在句尾 -> RESPOND"""
        patches = _patch_dependencies(wake_words=["小鱼"])
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("你好啊小鱼", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"

    def test_no_wake_word_in_text(self, service):
        """文本中无唤醒词 -> 不会精确匹配"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("今天天气真好", None, 1)
            )
        # 无唤醒词、非活跃、单 speaker、无问句 -> RECORD_ONLY
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"

    def test_multiple_wake_words(self, service):
        """多个自定义唤醒词 -> 任一匹配即 RESPOND"""
        patches = _patch_dependencies(wake_words=["小鱼", "助手", "小林"])
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("助手你好", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"

    def test_emergency_takes_priority_over_wake_word(self, service):
        """紧急命令词优先于唤醒词"""
        patches = _patch_dependencies(wake_words=["小鱼"])
        with patches["repo"], patches["active"], patches["redis"]:
            # "停" 开头优先触发 STOP
            result, reason = run_async(
                service.decide("停小鱼别说了", None, 1)
            )
        assert result == DecisionResult.STOP
        assert reason == "emergency_stop"


# ============ 决策链: 唤醒词模糊匹配 (优先级 3) ============


class TestFuzzyWakeWord:
    """唤醒词模糊匹配 -> RESPOND（决策链第 3 步）"""

    def test_edit_distance_one(self, service):
        """编辑距离为 1 -> RESPOND（例如：小渔 vs 小鱼）"""
        # "小渔" 与 "小鱼" 拼音完全一致（xiao yu），所以拼音相似度 >= 0.8
        patches = _patch_dependencies(wake_words=["小鱼"])
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide("小渔", None, 1))
        assert result == DecisionResult.RESPOND
        assert reason == "fuzzy_wake_word"

    def test_pinyin_similarity_match(self, service):
        """拼音相似度 >= 0.8 -> RESPOND"""
        # "小余" 拼音 ["xiao", "yu"] 与 "小鱼" 拼音相同
        patches = _patch_dependencies(wake_words=["小鱼"])
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide("小余", None, 1))
        assert result == DecisionResult.RESPOND
        assert reason == "fuzzy_wake_word"

    def test_fuzzy_in_longer_text(self, service):
        """模糊匹配在较长文本中（滑动窗口）"""
        # "你好小余帮我看看" 中 "小余" 拼音与 "小鱼" 相同
        patches = _patch_dependencies(wake_words=["小鱼"])
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("你好小余帮我看看", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "fuzzy_wake_word"

    def test_no_fuzzy_match(self, service):
        """编辑距离和拼音相似度都不满足 -> 不模糊匹配"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("大象跑得快", None, 1)
            )
        # 无精确/模糊匹配、非活跃、单 speaker、无问句 -> default
        assert result == DecisionResult.RECORD_ONLY

    def test_empty_wake_word_skip(self, service):
        """空唤醒词应被跳过"""
        patches = _patch_dependencies(
            wake_words=["", "小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("小鱼你好", None, 1)
            )
        assert result == DecisionResult.RESPOND
        # 精确匹配在先
        assert reason == "exact_wake_word"


# ============ 决策链: 活跃对话 (优先级 4) ============


class TestActiveConversation:
    """活跃对话内无唤醒词 -> RESPOND（决策链第 4 步）"""

    def test_active_conversation_respond(self, service):
        """活跃对话 + 无唤醒词 -> RESPOND"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("今天天气怎么样", None, 1)
            )
        # 虽然包含问号词，但活跃对话先命中
        # 实际上 "怎么" 会导致精确匹配问句，但"今天天气怎么样"无唤醒词
        # 活跃对话在模糊匹配之后，问句检测之前
        assert result == DecisionResult.RESPOND
        assert reason == "active_conversation"

    def test_active_conversation_plain_statement(self, service):
        """活跃对话 + 陈述句 -> RESPOND"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("好的我知道了", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "active_conversation"

    def test_not_active_falls_through(self, service):
        """非活跃对话 -> 继续后续判断"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("好的我知道了", None, 1)
            )
        # 无唤醒词、非活跃、单 speaker、无问句特征 -> default
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"


# ============ 决策链: 多 speaker (优先级 5) ============


class TestMultiSpeaker:
    """非活跃 + 多 speaker -> RECORD_ONLY（决策链第 5 步）"""

    def test_multi_speaker_record_only(self, service):
        """2 个活跃说话人 -> RECORD_ONLY"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=2
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("今天吃什么", None, 1)
            )
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "multi_speaker"

    def test_many_speakers_record_only(self, service):
        """5 个活跃说话人 -> RECORD_ONLY"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=5
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("大家好", None, 1)
            )
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "multi_speaker"

    def test_single_speaker_falls_through(self, service):
        """1 个说话人 -> 继续后续判断"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=1
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("今天天气真好", None, 1)
            )
        # 单 speaker、无问句特征 -> default
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"

    def test_zero_speakers_falls_through(self, service):
        """0 个说话人 -> 继续后续判断"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("今天天气真好", None, 1)
            )
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"


# ============ 决策链: 问句特征 (优先级 6) ============


class TestQuestionFeatures:
    """非活跃 + 单 speaker + 问句特征 -> RESPOND（决策链第 6 步）"""

    def test_question_mark_zh(self, service):
        """中文问号 -> RESPOND"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("这是什么东西？", None, 1)
            )
        # "什么" 疑问词先命中
        assert result == DecisionResult.RESPOND
        assert reason == "question_detected"

    def test_question_mark_en(self, service):
        """英文问号 -> RESPOND"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("what is this?", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "question_detected"

    @pytest.mark.parametrize("question_word", list(QUESTION_WORDS))
    def test_question_words(self, service, question_word):
        """所有疑问词 -> RESPOND"""
        text = f"你知道{question_word}东西好用"
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide(text, None, 1))
        assert result == DecisionResult.RESPOND
        assert reason == "question_detected"

    @pytest.mark.parametrize("particle", list(QUESTION_PARTICLES))
    def test_sentence_end_particles(self, service, particle):
        """句尾语气词 -> RESPOND"""
        text = f"你说的对{particle}"
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide(text, None, 1))
        assert result == DecisionResult.RESPOND
        assert reason == "question_detected"

    def test_particle_not_at_end(self, service):
        """语气词不在句尾 -> 不触发问句检测"""
        # "吗你好" - "吗" 不在句尾，且无其他问句特征
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("吗你好啊朋友", None, 1)
            )
        # "吗" 在句首，且末尾不是语气词 -> default
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"

    def test_no_question_features(self, service):
        """无问句特征 -> RECORD_ONLY (default)"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("今天天气真好", None, 1)
            )
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"


# ============ 决策链: 默认行为 (优先级 7) ============


class TestDefaultBehavior:
    """默认 -> RECORD_ONLY（决策链第 7 步）"""

    def test_plain_statement_default(self, service):
        """普通陈述句 -> RECORD_ONLY"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("我去买个东西", None, 1)
            )
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"

    def test_unrelated_text(self, service):
        """无关文本 -> RECORD_ONLY"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("树上有一只鸟", None, 1)
            )
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"


# ============ 自定义唤醒词加载 ============


class TestWakeWordLoading:
    """自定义唤醒词加载测试"""

    def test_custom_wake_words_loaded(self, service):
        """用户配置的自定义唤醒词被正确加载"""
        patches = _patch_dependencies(
            wake_words=["琳琳", "小助手"], is_active=False
        )
        with patches["repo"], patches["active"], patches["redis"]:
            # 使用自定义唤醒词
            result, reason = run_async(service.decide("琳琳你好", None, 1))
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"

    def test_custom_wake_word_second_word(self, service):
        """第二个自定义唤醒词也能匹配"""
        patches = _patch_dependencies(
            wake_words=["琳琳", "小助手"], is_active=False
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("小助手帮我查一下", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"

    def test_empty_wake_words_fallback_to_default(self, service):
        """唤醒词列表为空 -> 回退到 settings 默认值"""
        mock_settings_obj = MagicMock()
        mock_settings_obj.wake_words = []  # 空列表

        with patch(
            "apps.voice.services.response_decision_service.voice_settings_repo.get_or_create",
            AsyncMock(return_value=(mock_settings_obj, False)),
        ), patch(
            "apps.voice.services.response_decision_service.voice_session_service.is_active_conversation",
            AsyncMock(return_value=False),
        ), patch(
            "apps.voice.services.response_decision_service.get_redis",
            AsyncMock(return_value=_build_redis_mock(0)),
        ), patch(
            "django.conf.settings.VOICE_DEFAULT_WAKE_WORDS",
            ["小鱼"],
        ):
            result, reason = run_async(
                service.decide("小鱼你好", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"

    def test_none_wake_words_fallback(self, service):
        """唤醒词为 None -> 回退到 settings 默认值"""
        mock_settings_obj = MagicMock()
        mock_settings_obj.wake_words = None

        with patch(
            "apps.voice.services.response_decision_service.voice_settings_repo.get_or_create",
            AsyncMock(return_value=(mock_settings_obj, False)),
        ), patch(
            "apps.voice.services.response_decision_service.voice_session_service.is_active_conversation",
            AsyncMock(return_value=False),
        ), patch(
            "apps.voice.services.response_decision_service.get_redis",
            AsyncMock(return_value=_build_redis_mock(0)),
        ), patch(
            "django.conf.settings.VOICE_DEFAULT_WAKE_WORDS",
            ["小鱼"],
        ):
            result, reason = run_async(
                service.decide("小鱼你好", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"

    def test_db_error_fallback_to_default(self, service):
        """数据库异常 -> 回退到 settings 默认值"""
        with patch(
            "apps.voice.services.response_decision_service.voice_settings_repo.get_or_create",
            AsyncMock(side_effect=Exception("DB connection failed")),
        ), patch(
            "apps.voice.services.response_decision_service.voice_session_service.is_active_conversation",
            AsyncMock(return_value=False),
        ), patch(
            "apps.voice.services.response_decision_service.get_redis",
            AsyncMock(return_value=_build_redis_mock(0)),
        ), patch(
            "django.conf.settings.VOICE_DEFAULT_WAKE_WORDS",
            ["小鱼"],
        ):
            result, reason = run_async(
                service.decide("小鱼你好", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"


# ============ 活跃对话超时行为 ============


class TestActiveConversationTimeout:
    """活跃对话超时行为测试"""

    def test_active_conv_expired_falls_to_default(self, service):
        """活跃对话 TTL 过期 (is_active=False) -> 走后续判断"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("好的谢谢", None, 1)
            )
        # 无唤醒词、非活跃、单 speaker、无问句 -> default
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"

    def test_active_conv_expired_question_respond(self, service):
        """活跃对话过期后，问句仍能触发 RESPOND"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("接下来怎么办呢", None, 1)
            )
        # "怎么" 疑问词 -> question_detected
        assert result == DecisionResult.RESPOND
        assert reason == "question_detected"

    def test_active_conv_expired_multi_speaker_record(self, service):
        """活跃对话过期 + 多说话人 -> RECORD_ONLY"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=3
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("今天干什么", None, 1)
            )
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "multi_speaker"


# ============ Redis 异常处理 ============


class TestRedisErrorHandling:
    """Redis 异常时的降级行为"""

    def test_redis_error_speaker_count_zero(self, service):
        """Redis 连接异常 -> speaker_count 降级为 0"""
        with patch(
            "apps.voice.services.response_decision_service.voice_settings_repo.get_or_create",
            AsyncMock(
                return_value=(MagicMock(wake_words=["小鱼"]), False)
            ),
        ), patch(
            "apps.voice.services.response_decision_service.voice_session_service.is_active_conversation",
            AsyncMock(return_value=False),
        ), patch(
            "apps.voice.services.response_decision_service.get_redis",
            AsyncMock(side_effect=Exception("Redis unavailable")),
        ):
            # 无唤醒词匹配、非活跃、Redis 异常 -> speaker_count 降级为 0
            # 然后检查问句特征
            result, reason = run_async(
                service.decide("这是什么东西", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "question_detected"

    def test_redis_scard_error_returns_zero(self, service):
        """Redis scard 命令异常 -> speaker_count 降级为 0"""
        mock_redis = AsyncMock()
        mock_redis.scard = AsyncMock(side_effect=Exception("scard failed"))
        mock_redis.aclose = AsyncMock()

        with patch(
            "apps.voice.services.response_decision_service.voice_settings_repo.get_or_create",
            AsyncMock(
                return_value=(MagicMock(wake_words=["小鱼"]), False)
            ),
        ), patch(
            "apps.voice.services.response_decision_service.voice_session_service.is_active_conversation",
            AsyncMock(return_value=False),
        ), patch(
            "apps.voice.services.response_decision_service.get_redis",
            AsyncMock(return_value=mock_redis),
        ):
            result, reason = run_async(
                service.decide("今天天气不错", None, 1)
            )
        # Redis 异常 -> speaker_count=0, 无问句 -> default
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"


# ============ 工具函数: _edit_distance ============


class TestEditDistance:
    """编辑距离计算测试"""

    def test_identical_strings(self):
        """相同字符串 -> 距离 0"""
        assert _edit_distance("小鱼", "小鱼") == 0

    def test_one_substitution(self):
        """替换一个字符 -> 距离 1"""
        assert _edit_distance("小鱼", "小渔") == 1

    def test_one_insertion(self):
        """插入一个字符 -> 距离 1"""
        assert _edit_distance("小鱼", "小鱼儿") == 1

    def test_one_deletion(self):
        """删除一个字符 -> 距离 1"""
        assert _edit_distance("小鱼儿", "小鱼") == 1

    def test_completely_different(self):
        """完全不同的字符串"""
        result = _edit_distance("abc", "xyz")
        assert result == 3

    def test_empty_first(self):
        """第一个字符串为空"""
        assert _edit_distance("", "abc") == 3

    def test_empty_second(self):
        """第二个字符串为空"""
        assert _edit_distance("abc", "") == 3

    def test_both_empty(self):
        """两个空字符串"""
        assert _edit_distance("", "") == 0

    def test_single_char_same(self):
        """单字符相同"""
        assert _edit_distance("a", "a") == 0

    def test_single_char_different(self):
        """单字符不同"""
        assert _edit_distance("a", "b") == 1

    def test_longer_strings(self):
        """较长字符串"""
        assert _edit_distance("kitten", "sitting") == 3


# ============ 工具函数: _pinyin_similarity ============


class TestPinyinSimilarity:
    """拼音相似度计算测试"""

    def test_identical_pinyin(self):
        """相同拼音 -> 相似度 1.0"""
        result = _pinyin_similarity("小鱼", "小鱼")
        assert result == 1.0

    def test_same_pinyin_different_char(self):
        """不同汉字相同拼音 -> 相似度 1.0"""
        # "小余" 和 "小鱼" 都是 ["xiao", "yu"]
        result = _pinyin_similarity("小余", "小鱼")
        assert result == 1.0

    def test_partial_match(self):
        """部分拼音匹配"""
        # "小猫" = ["xiao", "mao"], "小鱼" = ["xiao", "yu"]
        # 1/2 = 0.5
        result = _pinyin_similarity("小猫", "小鱼")
        assert result == 0.5

    def test_no_match(self):
        """完全不匹配"""
        # "大象" = ["da", "xiang"], "小鱼" = ["xiao", "yu"]
        result = _pinyin_similarity("大象", "小鱼")
        assert result == 0.0

    def test_different_lengths(self):
        """不同长度的字符串"""
        # "小鱼儿" = ["xiao", "yu", "er"], "小鱼" = ["xiao", "yu"]
        # 2 matches / 3 max_len = 0.666...
        result = _pinyin_similarity("小鱼儿", "小鱼")
        assert abs(result - 2 / 3) < 0.01

    def test_empty_first(self):
        """第一个字符串为空"""
        result = _pinyin_similarity("", "小鱼")
        assert result == 0.0

    def test_empty_second(self):
        """第二个字符串为空"""
        result = _pinyin_similarity("小鱼", "")
        assert result == 0.0

    def test_english_letters(self):
        """英文字母（拼音库原样返回）"""
        # "abc" 拼音为 ["abc"] 整体，不拆分
        result = _pinyin_similarity("abc", "abc")
        assert result == 1.0


# ============ 静态方法直接测试 ============


class TestCheckMethods:
    """直接测试 _check_* 静态方法"""

    def test_check_emergency_stop_all_words(self):
        """所有紧急命令词"""
        for word in EMERGENCY_STOP_WORDS:
            assert ResponseDecisionService._check_emergency_stop(word) is True

    def test_check_emergency_stop_prefix(self):
        """紧急词作为前缀"""
        assert (
            ResponseDecisionService._check_emergency_stop("闭嘴我不想听")
            is True
        )

    def test_check_emergency_stop_not_match(self):
        """非紧急命令词"""
        assert (
            ResponseDecisionService._check_emergency_stop("你好啊") is False
        )

    def test_check_exact_wake_word_found(self):
        """精确匹配命中"""
        assert (
            ResponseDecisionService._check_exact_wake_word(
                "小鱼你好", ["小鱼"]
            )
            is True
        )

    def test_check_exact_wake_word_not_found(self):
        """精确匹配未命中"""
        assert (
            ResponseDecisionService._check_exact_wake_word(
                "你好啊", ["小鱼"]
            )
            is False
        )

    def test_check_exact_wake_word_empty_list(self):
        """空唤醒词列表"""
        assert (
            ResponseDecisionService._check_exact_wake_word("小鱼", [])
            is False
        )

    def test_check_fuzzy_wake_word_edit_distance(self):
        """模糊匹配 - 编辑距离"""
        # "小渔" 与 "小鱼" 编辑距离 1
        assert (
            ResponseDecisionService._check_fuzzy_wake_word(
                "小渔", ["小鱼"]
            )
            is True
        )

    def test_check_fuzzy_wake_word_pinyin(self):
        """模糊匹配 - 拼音"""
        # "小余" 与 "小鱼" 拼音相同
        assert (
            ResponseDecisionService._check_fuzzy_wake_word(
                "小余", ["小鱼"]
            )
            is True
        )

    def test_check_fuzzy_wake_word_no_match(self):
        """模糊匹配不命中"""
        assert (
            ResponseDecisionService._check_fuzzy_wake_word(
                "大象", ["小鱼"]
            )
            is False
        )

    def test_check_fuzzy_wake_word_empty_word(self):
        """空唤醒词被跳过"""
        assert (
            ResponseDecisionService._check_fuzzy_wake_word(
                "test", [""]
            )
            is False
        )

    def test_check_question_features_zh_mark(self):
        """中文问号"""
        assert (
            ResponseDecisionService._check_question_features("真的？")
            is True
        )

    def test_check_question_features_en_mark(self):
        """英文问号"""
        assert (
            ResponseDecisionService._check_question_features("really?")
            is True
        )

    def test_check_question_features_word(self):
        """疑问词"""
        assert (
            ResponseDecisionService._check_question_features("为什么不行")
            is True
        )

    def test_check_question_features_particle_end(self):
        """句尾语气词"""
        assert (
            ResponseDecisionService._check_question_features("你去吗")
            is True
        )

    def test_check_question_features_none(self):
        """无问句特征"""
        assert (
            ResponseDecisionService._check_question_features("我去买东西")
            is False
        )

    def test_check_question_features_empty_text(self):
        """空文本"""
        assert (
            ResponseDecisionService._check_question_features("") is False
        )


# ============ DecisionResult 枚举 ============


class TestDecisionResult:
    """DecisionResult 枚举测试"""

    def test_values(self):
        """枚举值正确"""
        assert DecisionResult.RESPOND.value == "RESPOND"
        assert DecisionResult.RECORD_ONLY.value == "RECORD_ONLY"
        assert DecisionResult.STOP.value == "STOP"

    def test_is_string(self):
        """DecisionResult 枚举值为字符串"""
        assert isinstance(DecisionResult.RESPOND.value, str)
        assert isinstance(DecisionResult.RECORD_ONLY.value, str)
        assert isinstance(DecisionResult.STOP.value, str)


# ============ 集成场景: 全决策链覆盖 ============


class TestDecisionChainIntegration:
    """集成测试：验证决策链的优先级顺序"""

    def test_emergency_beats_everything(self, service):
        """紧急命令词优先于所有（含唤醒词 + 活跃对话）"""
        patches = _patch_dependencies(
            wake_words=["停"], is_active=True, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(service.decide("停", None, 1))
        assert result == DecisionResult.STOP
        assert reason == "emergency_stop"

    def test_exact_wake_beats_fuzzy(self, service):
        """精确唤醒词优先于模糊匹配"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            # "小鱼" 同时满足精确和模糊，精确先命中
            result, reason = run_async(service.decide("小鱼", None, 1))
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"

    def test_fuzzy_wake_beats_active_conv(self, service):
        """模糊唤醒词优先于活跃对话"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            # "小余" 拼音匹配 "小鱼"
            result, reason = run_async(service.decide("小余你好", None, 1))
        assert result == DecisionResult.RESPOND
        assert reason == "fuzzy_wake_word"

    def test_active_conv_beats_multi_speaker(self, service):
        """活跃对话优先于多 speaker 判断"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=5
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("今天天气不错", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "active_conversation"

    def test_multi_speaker_beats_question(self, service):
        """多 speaker 优先于问句特征"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=3
        )
        with patches["repo"], patches["active"], patches["redis"]:
            # 有问句特征（"什么"），但多 speaker 先命中
            result, reason = run_async(
                service.decide("今天吃什么", None, 1)
            )
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "multi_speaker"

    def test_question_beats_default(self, service):
        """问句特征优先于默认行为"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("你说的对吧", None, 1)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "question_detected"

    def test_speaker_id_passed_through(self, service):
        """speaker_id 参数正确传递（不影响决策）"""
        patches = _patch_dependencies(
            wake_words=["小鱼"], is_active=True
        )
        with patches["repo"], patches["active"], patches["redis"]:
            result, reason = run_async(
                service.decide("你好", "speaker_abc_123", 42)
            )
        assert result == DecisionResult.RESPOND
        assert reason == "active_conversation"

    def test_different_user_ids(self, service):
        """不同 user_id 正确传递"""
        for uid in [1, 100, 9999]:
            patches = _patch_dependencies(
                wake_words=["小鱼"], is_active=False, speaker_count=0
            )
            with patches["repo"], patches["active"], patches["redis"]:
                result, reason = run_async(
                    service.decide("你好", None, uid)
                )
            assert result == DecisionResult.RECORD_ONLY
            assert reason == "default"
