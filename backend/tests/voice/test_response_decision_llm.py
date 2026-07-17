"""LLM 意图分类增强测试 — ResponseDecisionService._classify_intent_llm()

覆盖:
- LLM 高置信度 RESPOND（mock httpx 返回 decision=RESPOND, confidence=0.9）
- LLM 高置信度 RECORD_ONLY
- LLM 低置信度穿透（confidence < threshold=0.7，回退到规则引擎）
- LLM 超时穿透（mock httpx.TimeoutException）
- LLM 未启用（VOICE_DECISION_USE_LLM=False）→ 跳过 LLM
- httpx 连接异常 → 穿透
- 非 ambient 模式 → 即使启用 LLM 也跳过
- LLM 无可用 tool 模型 → 穿透
- LLM 返回非法 JSON → 穿透
- LLM HTTP 错误状态码 → 穿透

Mock 策略:
- httpx.AsyncClient → 控制 LLM HTTP 调用
- apps.models.services.model_service.get_active_model → mock 模型配置
- apps.voice.repositories.voice_settings_repo.get_or_create → 唤醒词
- apps.voice.services.voice_session_service → 活跃对话状态
- django.conf.settings → VOICE_DECISION_USE_LLM / THRESHOLD / TIMEOUT
- core.redis.get_redis → 控制 recent_speakers

覆盖率目标: >= 95%
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.voice.services.response_decision_service import (
    DecisionResult,
    ResponseDecisionService,
)


# ============ Fixtures ============


@pytest.fixture
def service():
    """创建 ResponseDecisionService 实例"""
    return ResponseDecisionService()


@pytest.fixture
def mock_model_config():
    """Mock 模型配置 — model_service.get_active_model() 返回 dict"""
    return {
        "url": "https://api.example.com/v1",
        "api_key": "sk-test-key-1234567890",
        "name": "deepseek-v3-test",
    }


def _build_redis_mock(speaker_count=0):
    """构建 mock Redis 客户端"""
    mock_redis = AsyncMock()
    mock_redis.scard = AsyncMock(return_value=speaker_count)
    mock_redis.aclose = AsyncMock()
    # TTS echo 检测所需：默认无 TTS 播放状态和历史，不影响原有决策链
    mock_redis.exists = AsyncMock(return_value=0)
    mock_redis.lrange = AsyncMock(return_value=[])
    return mock_redis


def _build_llm_response(decision: str, confidence: float, reason: str) -> httpx.Response:
    """构造 mock httpx.Response，模拟 LLM chat/completions 响应

    Args:
        decision: "RESPOND" 或 "RECORD_ONLY"
        confidence: 0.0-1.0
        reason: 简短原因
    """
    content = json.dumps({
        "decision": decision,
        "confidence": confidence,
        "reason": reason,
    })
    response_data = {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.json.return_value = response_data
    response.raise_for_status = MagicMock()
    return response


def _patch_base_dependencies(
    wake_words=None,
    is_active=False,
    speaker_count=0,
    recent_messages=None,
    memory_summary=None,
):
    """Patch 基础依赖（repo / session / redis / intent context），不涉及 LLM 相关

    Returns:
        dict of patch context managers
    """
    if wake_words is None:
        wake_words = ["小鱼"]

    mock_settings_obj = MagicMock()
    mock_settings_obj.wake_words = wake_words

    return {
        "repo": patch(
            "apps.voice.services.response_decision_service.voice_settings_repo.get_or_create",
            AsyncMock(return_value=(mock_settings_obj, False)),
        ),
        "active": patch(
            "apps.voice.services.response_decision_service.voice_session_service.is_active_conversation",
            AsyncMock(return_value=is_active),
        ),
        "redis": patch(
            "apps.voice.services.response_decision_service.get_redis",
            AsyncMock(return_value=_build_redis_mock(speaker_count)),
        ),
        "context": patch(
            "apps.voice.services.response_decision_service.ResponseDecisionService._fetch_intent_context",
            AsyncMock(return_value=(recent_messages or [], memory_summary)),
        ),
    }


# ============ LLM 高置信度 RESPOND ============


class TestLLMHighConfidenceRespond:
    """LLM 返回 RESPOND + 高置信度 (>= 0.7) → 直接采纳"""

    @pytest.mark.asyncio
    async def test_llm_respond_confidence_09(self, service, mock_model_config):
        """LLM 返回 decision=RESPOND, confidence=0.9 → RESPOND + llm_前缀原因"""
        mock_response = _build_llm_response("RESPOND", 0.9, "用户在下指令")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "帮我打开客厅灯", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RESPOND
        assert reason == "llm_用户在下指令"

    @pytest.mark.asyncio
    async def test_llm_respond_at_threshold_boundary(self, service, mock_model_config):
        """LLM confidence == threshold (0.7) → 仍采纳"""
        mock_response = _build_llm_response("RESPOND", 0.7, "边界指令")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(wake_words=["小鱼"])
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "关灯", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RESPOND
        assert reason == "llm_边界指令"


# ============ LLM 高置信度 RECORD_ONLY ============


class TestLLMHighConfidenceRecordOnly:
    """LLM 返回 RECORD_ONLY + 高置信度 → RECORD_ONLY"""

    @pytest.mark.asyncio
    async def test_llm_record_only_confidence_09(self, service, mock_model_config):
        """LLM 返回 decision=RECORD_ONLY, confidence=0.9 → RECORD_ONLY"""
        mock_response = _build_llm_response("RECORD_ONLY", 0.9, "自言自语")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "今天天气真不错啊", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "llm_自言自语"

    @pytest.mark.asyncio
    async def test_llm_record_only_high_confidence_skips_rule_engine(
        self, service, mock_model_config
    ):
        """LLM RECORD_ONLY 高置信度 → 不再走活跃对话 / 问句检测等后续规则"""
        # 文本含问号 → 规则引擎会判 RESPOND，但 LLM 高置信 RECORD_ONLY 优先
        mock_response = _build_llm_response("RECORD_ONLY", 0.95, "与他人交谈")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "你吃了吗？", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "llm_与他人交谈"


# ============ LLM 低置信度穿透 ============


class TestLLMLowConfidenceFallthrough:
    """LLM confidence < threshold (0.7) → 穿透到规则引擎"""

    @pytest.mark.asyncio
    async def test_low_confidence_fallthrough_to_active_conv(
        self, service, mock_model_config
    ):
        """LLM confidence=0.5 < 0.7 → 穿透，活跃对话命中 RESPOND"""
        mock_response = _build_llm_response("RECORD_ONLY", 0.5, "不确定")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "今天天气不错", None, 1, mode="ambient"
            )

        # 穿透到规则引擎步骤 5：活跃对话 → RESPOND
        assert result == DecisionResult.RESPOND
        assert reason == "active_conversation"

    @pytest.mark.asyncio
    async def test_low_confidence_fallthrough_to_default(
        self, service, mock_model_config
    ):
        """LLM confidence=0.3 < 0.7 → 穿透，无活跃对话 → RECORD_ONLY (default)"""
        mock_response = _build_llm_response("RESPOND", 0.3, "可能是指令")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "今天天气不错", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"

    @pytest.mark.asyncio
    async def test_low_confidence_just_below_threshold(
        self, service, mock_model_config
    ):
        """LLM confidence=0.69 < 0.7（刚好低于阈值） → 穿透"""
        mock_response = _build_llm_response("RESPOND", 0.69, "不太确定")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "去买个东西", None, 1, mode="ambient"
            )

        # 穿透到规则引擎默认 RECORD_ONLY
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"


# ============ LLM 超时穿透 ============


class TestLLMTimeoutSafeDefault:
    """LLM 超时 (httpx.TimeoutException) → 安全降级 RECORD_ONLY（不穿透规则链）"""

    @pytest.mark.asyncio
    async def test_timeout_returns_record_only_even_with_active_conv(
        self, service, mock_model_config
    ):
        """LLM 超时 → RECORD_ONLY（即使有活跃对话也不穿透）"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=httpx.TimeoutException("Connection timed out")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "打开空调", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "llm_llm_timeout"

    @pytest.mark.asyncio
    async def test_timeout_returns_record_only_default(
        self, service, mock_model_config
    ):
        """LLM 超时 → RECORD_ONLY + llm_llm_timeout 原因"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=httpx.TimeoutException("Read timed out")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "今天天气不错", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "llm_llm_timeout"


# ============ LLM 未启用 ============


class TestLLMDisabled:
    """VOICE_DECISION_USE_LLM=False → 完全跳过 LLM 分类"""

    @pytest.mark.asyncio
    async def test_llm_disabled_skips_llm(self, service):
        """LLM 未启用时，ambient 模式直接走规则引擎"""
        mock_get_active = AsyncMock(return_value=MagicMock())

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", False),
            patch(
                "apps.models.services.model_service.get_active_model",
                mock_get_active,
            ),
        ):
            result, reason = await service.decide(
                "今天天气不错", None, 1, mode="ambient"
            )

        # LLM 未调用，走规则引擎：活跃对话 → RESPOND
        assert result == DecisionResult.RESPOND
        assert reason == "active_conversation"
        # 确认 get_active_model 未被调用
        mock_get_active.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_disabled_ambient_question_respond(self, service):
        """LLM 未启用 + ambient + 问句 → 走规则引擎问句检测"""
        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", False),
        ):
            result, reason = await service.decide(
                "现在几点了？", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RESPOND
        assert reason == "question_detected"

    @pytest.mark.asyncio
    async def test_llm_disabled_ambient_default_record(self, service):
        """LLM 未启用 + ambient + 无特征 → RECORD_ONLY"""
        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", False),
        ):
            result, reason = await service.decide(
                "嗯嗯好的", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"


# ============ httpx 连接异常 → 穿透 ============


class TestHTTPConnectionError:
    """httpx 连接异常（非超时） → _classify_intent_llm 返回 None → 穿透"""

    @pytest.mark.asyncio
    async def test_connection_error_fallthrough(
        self, service, mock_model_config
    ):
        """httpx.ConnectError → 穿透到规则引擎"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "打开卧室灯", None, 1, mode="ambient"
            )

        # 连接异常 → 穿透 → 活跃对话 → RESPOND
        assert result == DecisionResult.RESPOND
        assert reason == "active_conversation"

    @pytest.mark.asyncio
    async def test_generic_exception_fallthrough(
        self, service, mock_model_config
    ):
        """通用异常 (Exception) → 穿透到规则引擎"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=RuntimeError("Unexpected error in HTTP layer")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "嗯好的", None, 1, mode="ambient"
            )

        # 异常穿透 → 规则引擎默认
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"

    @pytest.mark.asyncio
    async def test_http_status_error_fallthrough(
        self, service, mock_model_config
    ):
        """HTTP 500 错误 (raise_for_status) → 穿透"""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "Server error",
                request=MagicMock(),
                response=mock_response,
            )
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "好的", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"


# ============ 非 ambient 模式 → 跳过 LLM ============


class TestNonAmbientModeSkipsLLM:
    """非 ambient 模式 → 即使 VOICE_DECISION_USE_LLM=True 也跳过 LLM"""

    @pytest.mark.asyncio
    async def test_voice_chat_mode_skips_llm(self, service):
        """voice_chat 模式 → 跳过 LLM，走规则引擎"""
        mock_get_active = AsyncMock(return_value=MagicMock())

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch(
                "apps.models.services.model_service.get_active_model",
                mock_get_active,
            ),
        ):
            result, reason = await service.decide(
                "好的谢谢", None, 1, mode="voice_chat"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"
        mock_get_active.assert_not_called()



# ============ LLM 无可用 tool 模型 ============


class TestLLMNoActiveModel:
    """get_active_model 返回 None → _classify_intent_llm 返回 None → 穿透"""

    @pytest.mark.asyncio
    async def test_no_tool_model_fallthrough(self, service):
        """无可用 tool 模型 → 穿透到规则引擎"""
        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=True, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                AsyncMock(return_value=None),
            ),
        ):
            result, reason = await service.decide(
                "打开灯", None, 1, mode="ambient"
            )

        # 无模型 → 穿透 → 活跃对话 → RESPOND
        assert result == DecisionResult.RESPOND
        assert reason == "active_conversation"


# ============ LLM 返回非法 JSON ============


class TestLLMInvalidResponse:
    """LLM 返回格式不正确 → 异常被捕获 → 穿透"""

    @pytest.mark.asyncio
    async def test_invalid_json_content_fallthrough(
        self, service, mock_model_config
    ):
        """LLM 返回非法 JSON 内容 → 穿透"""
        response_data = {
            "choices": [
                {
                    "message": {
                        "content": "这不是 JSON 格式",
                    }
                }
            ]
        }
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "测试文本", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"

    @pytest.mark.asyncio
    async def test_missing_choices_key_fallthrough(
        self, service, mock_model_config
    ):
        """LLM 返回缺少 choices 键 → KeyError → 穿透"""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "something"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "测试", None, 1, mode="ambient"
            )

        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"

    @pytest.mark.asyncio
    async def test_missing_decision_defaults_to_record_only(
        self, service, mock_model_config
    ):
        """LLM JSON 缺少 decision 字段 → 默认 RECORD_ONLY"""
        content = json.dumps({"confidence": 0.9, "reason": "无 decision 字段"})
        response_data = {
            "choices": [{"message": {"content": content}}]
        }
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "测试缺失字段", None, 1, mode="ambient"
            )

        # decision 默认 "RECORD_ONLY", confidence=0.9 >= 0.7 → 采纳
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "llm_无 decision 字段"

    @pytest.mark.asyncio
    async def test_missing_confidence_defaults_to_zero(
        self, service, mock_model_config
    ):
        """LLM JSON 缺少 confidence 字段 → 默认 0.0 → 穿透"""
        content = json.dumps({"decision": "RESPOND", "reason": "无 confidence"})
        response_data = {
            "choices": [{"message": {"content": content}}]
        }
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "缺失置信度", None, 1, mode="ambient"
            )

        # confidence 默认 0.0 < 0.7 → 穿透 → RECORD_ONLY (default)
        assert result == DecisionResult.RECORD_ONLY
        assert reason == "default"


# ============ LLM 与唤醒词优先级交互 ============


class TestLLMPriorityInteraction:
    """唤醒词 / 紧急停止词优先于 LLM 分类"""

    @pytest.mark.asyncio
    async def test_wake_word_takes_priority_over_llm(
        self, service, mock_model_config
    ):
        """唤醒词精确匹配优先于 LLM（步骤 2 > 步骤 4）"""
        mock_get_active = AsyncMock(return_value=mock_model_config)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch(
                "apps.models.services.model_service.get_active_model",
                mock_get_active,
            ),
        ):
            result, reason = await service.decide(
                "小鱼打开灯", None, 1, mode="ambient"
            )

        # 唤醒词精确匹配在 LLM 之前
        assert result == DecisionResult.RESPOND
        assert reason == "exact_wake_word"
        # LLM 未被调用
        mock_get_active.assert_not_called()

    @pytest.mark.asyncio
    async def test_emergency_stop_takes_priority_over_llm(
        self, service, mock_model_config
    ):
        """紧急停止词优先于 LLM（步骤 1 > 步骤 4）"""
        mock_get_active = AsyncMock(return_value=mock_model_config)

        base = _patch_base_dependencies(
            wake_words=["小鱼"], is_active=False, speaker_count=0,
        )
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch(
                "apps.models.services.model_service.get_active_model",
                mock_get_active,
            ),
        ):
            result, reason = await service.decide(
                "停", None, 1, mode="ambient"
            )

        assert result == DecisionResult.STOP
        assert reason == "emergency_stop"
        mock_get_active.assert_not_called()


# ============ _classify_intent_llm 直接测试 ============


class TestClassifyIntentLLMDirect:
    """直接测试 _classify_intent_llm 内部方法"""

    @pytest.mark.asyncio
    async def test_classify_returns_respond_tuple(
        self, service, mock_model_config
    ):
        """RESPOND 结果返回正确的三元组"""
        mock_response = _build_llm_response("RESPOND", 0.85, "明确指令")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await service._classify_intent_llm("帮我开灯")

        assert result is not None
        decision, reason, confidence = result
        assert decision == DecisionResult.RESPOND
        assert reason == "明确指令"
        assert confidence == 0.85

    @pytest.mark.asyncio
    async def test_classify_returns_record_only_tuple(
        self, service, mock_model_config
    ):
        """RECORD_ONLY 结果返回正确的三元组"""
        mock_response = _build_llm_response("RECORD_ONLY", 0.92, "闲聊")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await service._classify_intent_llm("嗯嗯好的")

        assert result is not None
        decision, reason, confidence = result
        assert decision == DecisionResult.RECORD_ONLY
        assert reason == "闲聊"
        assert confidence == 0.92

    @pytest.mark.asyncio
    async def test_classify_timeout_returns_record_only(
        self, service, mock_model_config
    ):
        """超时返回 (RECORD_ONLY, 'llm_timeout', 1.0) 安全降级"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=httpx.TimeoutException("timeout")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await service._classify_intent_llm("测试超时")

        assert result is not None
        decision, reason, confidence = result
        assert decision == DecisionResult.RECORD_ONLY
        assert reason == "llm_timeout"
        assert confidence == 1.0

    @pytest.mark.asyncio
    async def test_classify_no_model_returns_none(self, service):
        """无可用模型返回 None"""
        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                AsyncMock(return_value=None),
            ),
        ):
            result = await service._classify_intent_llm("测试无模型")

        assert result is None

    @pytest.mark.asyncio
    async def test_classify_unknown_decision_defaults_record_only(
        self, service, mock_model_config
    ):
        """LLM 返回未知 decision 值 → 默认 RECORD_ONLY"""
        content = json.dumps({
            "decision": "UNKNOWN_VALUE",
            "confidence": 0.8,
            "reason": "未知类型",
        })
        response_data = {
            "choices": [{"message": {"content": content}}]
        }
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await service._classify_intent_llm("测试未知值")

        assert result is not None
        decision, reason, confidence = result
        # decision_str != "RESPOND" → RECORD_ONLY
        assert decision == DecisionResult.RECORD_ONLY
        assert reason == "未知类型"
        assert confidence == 0.8

    @pytest.mark.asyncio
    async def test_classify_sends_correct_request_payload(
        self, service, mock_model_config
    ):
        """验证发送给 LLM 的请求载荷格式正确"""
        mock_response = _build_llm_response("RESPOND", 0.9, "指令")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            await service._classify_intent_llm("帮我查天气")

        # 验证 post 调用参数
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args

        # URL 包含 url + /chat/completions
        assert call_args[0][0] == "https://api.example.com/v1/chat/completions"

        # Headers 包含 Authorization
        headers = call_args[1]["headers"]
        assert "Bearer" in headers["Authorization"]

        # JSON body 结构
        body = call_args[1]["json"]
        assert body["model"] == "deepseek-v3-test"
        assert body["temperature"] == 0.1
        assert body["max_tokens"] == 100
        assert body["response_format"] == {"type": "json_object"}
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"
        assert "帮我查天气" in body["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_classify_uses_configured_timeout(
        self, service, mock_model_config
    ):
        """验证 httpx.AsyncClient 使用配置的超时时间"""
        mock_response = _build_llm_response("RESPOND", 0.9, "test")

        original_async_client = httpx.AsyncClient
        captured_timeout = []

        class MockAsyncClient:
            def __init__(self, **kwargs):
                captured_timeout.append(kwargs.get("timeout"))
                self._client = AsyncMock()
                self._client.post = AsyncMock(return_value=mock_response)

            async def __aenter__(self):
                return self._client

            async def __aexit__(self, *args):
                pass

        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 2.5),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", MockAsyncClient),
        ):
            await service._classify_intent_llm("测试超时配置")

        assert len(captured_timeout) == 1
        assert captured_timeout[0] == 2.5


# ============ _fetch_intent_context 直接测试 ============


class TestFetchIntentContext:
    """直接测试 _fetch_intent_context 上下文获取"""

    @pytest.fixture
    def service(self):
        return ResponseDecisionService()

    @pytest.mark.asyncio
    async def test_fetches_recent_messages(self, service):
        """正确获取最近消息并转换为 role/content 字典"""
        mock_msg1 = MagicMock()
        mock_msg1.role = "user"
        mock_msg1.content = "你好"
        mock_msg2 = MagicMock()
        mock_msg2.role = "assistant"
        mock_msg2.content = "你好！有什么可以帮你的？"

        with (
            patch(
                "apps.chat.repositories.message_repo.find_latest_by_user",
                AsyncMock(return_value=[mock_msg2, mock_msg1]),  # -created_time 排序
            ),
            patch(
                "apps.memory.services.MemoryService.retrieve_relevant_memories",
                AsyncMock(return_value=None),
            ),
        ):
            messages, memory = await service._fetch_intent_context(1, "帮我开灯")

        # 反转后应为时间正序
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "你好"}
        assert messages[1] == {"role": "assistant", "content": "你好！有什么可以帮你的？"}
        assert memory is None

    @pytest.mark.asyncio
    async def test_fetches_memory_summary(self, service):
        """正确获取用户记忆摘要"""
        memory_text = "[用户记忆]\n1. 用户住在深圳\n2. 喜欢编程"

        with (
            patch(
                "apps.chat.repositories.message_repo.find_latest_by_user",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.memory.services.MemoryService.retrieve_relevant_memories",
                AsyncMock(return_value=memory_text),
            ),
        ):
            messages, memory = await service._fetch_intent_context(1, "我住哪里")

        assert messages == []
        assert memory == memory_text

    @pytest.mark.asyncio
    async def test_message_content_truncated(self, service):
        """超长消息内容被截断到 200 字符"""
        mock_msg = MagicMock()
        mock_msg.role = "user"
        mock_msg.content = "x" * 500

        with (
            patch(
                "apps.chat.repositories.message_repo.find_latest_by_user",
                AsyncMock(return_value=[mock_msg]),
            ),
            patch(
                "apps.memory.services.MemoryService.retrieve_relevant_memories",
                AsyncMock(return_value=None),
            ),
        ):
            messages, _ = await service._fetch_intent_context(1, "test")

        assert len(messages) == 1
        assert len(messages[0]["content"]) == 200

    @pytest.mark.asyncio
    async def test_empty_content_messages_skipped(self, service):
        """空内容消息被跳过"""
        mock_msg = MagicMock()
        mock_msg.role = "user"
        mock_msg.content = ""

        with (
            patch(
                "apps.chat.repositories.message_repo.find_latest_by_user",
                AsyncMock(return_value=[mock_msg]),
            ),
            patch(
                "apps.memory.services.MemoryService.retrieve_relevant_memories",
                AsyncMock(return_value=None),
            ),
        ):
            messages, _ = await service._fetch_intent_context(1, "test")

        assert messages == []

    @pytest.mark.asyncio
    async def test_message_fetch_failure_graceful(self, service):
        """消息获取失败时优雅降级，不影响记忆获取"""
        memory_text = "[用户记忆]\n1. 用户名安琳"

        with (
            patch(
                "apps.chat.repositories.message_repo.find_latest_by_user",
                AsyncMock(side_effect=Exception("DB error")),
            ),
            patch(
                "apps.memory.services.MemoryService.retrieve_relevant_memories",
                AsyncMock(return_value=memory_text),
            ),
        ):
            messages, memory = await service._fetch_intent_context(1, "test")

        assert messages == []
        assert memory == memory_text

    @pytest.mark.asyncio
    async def test_memory_fetch_failure_graceful(self, service):
        """记忆获取失败时优雅降级，不影响消息获取"""
        mock_msg = MagicMock()
        mock_msg.role = "user"
        mock_msg.content = "你好"

        with (
            patch(
                "apps.chat.repositories.message_repo.find_latest_by_user",
                AsyncMock(return_value=[mock_msg]),
            ),
            patch(
                "apps.memory.services.MemoryService.retrieve_relevant_memories",
                AsyncMock(side_effect=Exception("Embedding error")),
            ),
        ):
            messages, memory = await service._fetch_intent_context(1, "test")

        assert len(messages) == 1
        assert memory is None


# ============ Prompt 包含上下文信息 ============


class TestPromptContainsContext:
    """验证 LLM 请求的 prompt 包含对话上下文和记忆"""

    @pytest.fixture
    def service(self):
        return ResponseDecisionService()

    @pytest.fixture
    def mock_model_config(self):
        config = MagicMock()
        config.api_base = "https://api.example.com/v1"
        config.decrypted_api_key = "sk-test"
        config.model_name = "test-model"
        return config

    @pytest.mark.asyncio
    async def test_prompt_includes_recent_messages(self, service, mock_model_config):
        """prompt 中包含最近对话内容"""
        mock_response = _build_llm_response("RESPOND", 0.9, "test")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        recent = [{"role": "user", "content": "你好小鱼"}, {"role": "assistant", "content": "你好！"}]

        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 5.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "apps.voice.services.response_decision_service.ResponseDecisionService._fetch_intent_context",
                AsyncMock(return_value=(recent, None)),
            ),
        ):
            await service._classify_intent_llm("帮我开灯", user_id=1)

        # 验证 prompt 内容
        call_args = mock_client.post.call_args
        body = call_args[1]["json"]
        prompt_content = body["messages"][0]["content"]
        assert "你好小鱼" in prompt_content
        assert "你好！" in prompt_content

    @pytest.mark.asyncio
    async def test_prompt_includes_memory_summary(self, service, mock_model_config):
        """prompt 中包含用户记忆"""
        mock_response = _build_llm_response("RESPOND", 0.9, "test")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        memory = "[用户记忆]\n1. 用户住在深圳"

        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 5.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "apps.voice.services.response_decision_service.ResponseDecisionService._fetch_intent_context",
                AsyncMock(return_value=([], memory)),
            ),
        ):
            await service._classify_intent_llm("我住哪里", user_id=1)

        call_args = mock_client.post.call_args
        body = call_args[1]["json"]
        prompt_content = body["messages"][0]["content"]
        assert "用户住在深圳" in prompt_content

    @pytest.mark.asyncio
    async def test_prompt_no_context_when_user_id_zero(self, service, mock_model_config):
        """user_id=0 时不获取上下文"""
        mock_response = _build_llm_response("RESPOND", 0.9, "test")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_fetch = AsyncMock(return_value=([], None))

        with (
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 5.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "apps.voice.services.response_decision_service.ResponseDecisionService._fetch_intent_context",
                mock_fetch,
            ),
        ):
            await service._classify_intent_llm("帮我开灯", user_id=0)

        mock_fetch.assert_not_called()


# ============ batch-30: flag=true 短路双轨（对照上方 flag=false 守护用例） ============


class TestShortCircuitFlagOn:
    """batch-30 VOICE_DECISION_SHORTCIRCUIT_ENABLED=True 时的 LLM 调用行为（真实 httpx mock）。

    对照组：本文件上方 TestLLM* / TestLLMLowConfidence* 用例默认 flag=false（现状守护）。
    本类断言 flag=true 下 active_conversation / question 先短路、httpx 不被调用，
    仅歧义声明句仍调 LLM（§8.2 M3/M5 对照 + BC1/BC2/BC4）。
    """

    @pytest.mark.asyncio
    async def test_question_shortcircuits_llm_not_called(
        self, service, mock_model_config
    ):
        """flag=true: 疑问句「你吃了吗？」→ question_detected RESPOND，httpx 不调用
        （对照 test_llm_record_only_high_confidence_skips_rule_engine 的旧顺序）。"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=_build_llm_response("RECORD_ONLY", 0.95, "与他人交谈"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(wake_words=["小鱼"], is_active=False, speaker_count=0)
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_SHORTCIRCUIT_ENABLED", True),
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide("你吃了吗？", None, 1, mode="ambient")

        assert result == DecisionResult.RESPOND
        assert reason == "question_detected"
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_active_conv_shortcircuits_llm_not_called(
        self, service, mock_model_config
    ):
        """flag=true: 活跃对话内声明句 → active_conversation RESPOND，httpx 不调用
        （对照 test_timeout_returns_record_only_even_with_active_conv 的旧顺序）。"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(wake_words=["小鱼"], is_active=True, speaker_count=0)
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_SHORTCIRCUIT_ENABLED", True),
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide("好的我知道了", None, 1, mode="ambient")

        assert result == DecisionResult.RESPOND
        assert reason == "active_conversation"
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_declarative_still_calls_llm_flag_on(
        self, service, mock_model_config
    ):
        """flag=true: 非疑问声明句「今天天气不错」+ 已识别单说话人 + 非活跃 → LLM 仍被调用（BC4 末位兜底）。"""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=_build_llm_response("RESPOND", 0.9, "指令"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        base = _patch_base_dependencies(wake_words=["小鱼"], is_active=False, speaker_count=0)
        with (
            base["repo"],
            base["active"],
            base["redis"],
            base["context"],
            patch("django.conf.settings.VOICE_DECISION_SHORTCIRCUIT_ENABLED", True),
            patch("django.conf.settings.VOICE_DECISION_USE_LLM", True),
            patch("django.conf.settings.VOICE_DECISION_LLM_THRESHOLD", 0.7),
            patch("django.conf.settings.VOICE_DECISION_LLM_TIMEOUT", 1.0),
            patch(
                "apps.models.services.model_service.get_active_model",
                MagicMock(return_value=mock_model_config),
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result, reason = await service.decide(
                "今天天气不错", None, 1, mode="ambient", speaker_identified=True
            )

        assert result == DecisionResult.RESPOND
        assert reason == "llm_指令"
        mock_client.post.assert_called_once()
