"""
SpeakerService.identify_from_pcm 单元测试

覆盖:
- identify_from_pcm: 识别成功（identified=True）
- identify_from_pcm: 识别失败（identified=False）
- identify_from_pcm: Gateway 超时 → 优雅降级
- identify_from_pcm: Gateway 500 错误 → 优雅降级
- identify_from_pcm: 音频过短（< 16000 bytes）→ 跳过，无 Gateway 调用

Mock 策略: Mock httpx.AsyncClient 外部调用（llmgateway HTTP）
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.voice.services.speaker_service import SpeakerService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    """创建 SpeakerService 实例"""
    return SpeakerService()


@pytest.fixture
def mock_gateway_settings():
    """Mock Django settings 中的 gateway 配置"""
    with patch("apps.voice.services.speaker_service.settings") as mock_settings:
        mock_settings.LLM_GATEWAY_URL = "http://test-gateway:8889"
        mock_settings.LLM_GATEWAY_API_KEY = "test-api-key-123"
        yield mock_settings


@pytest.fixture
def pcm_sufficient():
    """足够长的 PCM 数据（16000 bytes，刚好满足 0.5s 阈值）"""
    return b"\x00\x01" * 8000


@pytest.fixture
def pcm_too_short():
    """过短的 PCM 数据（15999 bytes，低于阈值）"""
    return b"\x00" * 15999


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_mock_response(status_code: int, json_data: dict = None):
    """创建模拟的 httpx.Response"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
        resp.content = b"has content"
    else:
        resp.content = b""
        resp.json.side_effect = Exception("No JSON content")
    return resp


def _make_mock_client(post_response=None, side_effect=None):
    """创建模拟的 httpx.AsyncClient 上下文管理器"""
    mock_client = AsyncMock()
    if side_effect is not None:
        mock_client.post.side_effect = side_effect
    elif post_response is not None:
        mock_client.post.return_value = post_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ===========================================================================
# TestIdentifyFromPcm
# ===========================================================================


class TestIdentifyFromPcm:
    """identify_from_pcm 声纹识别测试（新契约：无 identified 字段，用 speaker_id 是否为 null 判断）"""

    @pytest.fixture
    def mock_profiles(self):
        """Mock speaker_profile_repo.find_all 返回候选列表"""
        mock_profile = MagicMock()
        mock_profile.gateway_speaker_id = "gw-speaker-001"
        with patch("apps.voice.services.speaker_service.speaker_profile_repo") as mock_repo:
            mock_repo.find_all = AsyncMock(return_value=[mock_profile])
            yield mock_repo

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_identify_from_pcm_success(
        self, mock_async_client_cls, service, pcm_sufficient, mock_gateway_settings, mock_profiles,
    ):
        """Gateway 返回 speaker_id 时正确返回"""
        mock_response = _make_mock_response(200, json_data={
            "speaker_id": "gw-speaker-001", "confidence": 0.92,
        })
        mock_async_client_cls.return_value = _make_mock_client(post_response=mock_response)

        result = await service.identify_from_pcm(pcm_sufficient)

        assert result["speaker_id"] == "gw-speaker-001"
        assert result["confidence"] == 0.92
        assert result["embedding_hash"] is not None
        # 验证传了 candidate_speaker_ids
        call_args = mock_async_client_cls.return_value.post.call_args
        assert call_args.kwargs["json"]["candidate_speaker_ids"] == ["gw-speaker-001"]

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_identify_from_pcm_not_matched(
        self, mock_async_client_cls, service, pcm_sufficient, mock_gateway_settings, mock_profiles,
    ):
        """Gateway 返回 speaker_id=null 时返回未匹配"""
        mock_response = _make_mock_response(200, json_data={
            "speaker_id": None, "confidence": 0.15,
        })
        mock_async_client_cls.return_value = _make_mock_client(post_response=mock_response)

        result = await service.identify_from_pcm(pcm_sufficient)

        assert result["speaker_id"] is None
        assert result["confidence"] == 0.15
        mock_async_client_cls.return_value.post.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_identify_from_pcm_gateway_timeout(
        self, mock_async_client_cls, service, pcm_sufficient, mock_gateway_settings, mock_profiles,
    ):
        """Gateway 超时 → speaker_id=None"""
        mock_async_client_cls.return_value = _make_mock_client(side_effect=httpx.TimeoutException("超时"))

        result = await service.identify_from_pcm(pcm_sufficient)

        assert result["speaker_id"] is None
        assert result["confidence"] == 0.0
        assert result["embedding_hash"] is not None

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_identify_from_pcm_gateway_error(
        self, mock_async_client_cls, service, pcm_sufficient, mock_gateway_settings, mock_profiles,
    ):
        """Gateway 500 → speaker_id=None"""
        mock_response = _make_mock_response(500)
        mock_async_client_cls.return_value = _make_mock_client(post_response=mock_response)

        result = await service.identify_from_pcm(pcm_sufficient)

        assert result["speaker_id"] is None
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_identify_from_pcm_audio_too_short(self, service, pcm_too_short):
        """音频过短 → 跳过 Gateway，直接返回 speaker_id=None"""
        result = await service.identify_from_pcm(pcm_too_short)

        assert result["speaker_id"] is None
        assert result["confidence"] == 0.0
        assert result["embedding_hash"] is None

    @pytest.mark.asyncio
    async def test_identify_from_pcm_no_profiles(self, service, pcm_sufficient, mock_gateway_settings):
        """无注册声纹 → 跳过 Gateway"""
        with patch("apps.voice.services.speaker_service.speaker_profile_repo") as mock_repo:
            mock_repo.find_all = AsyncMock(return_value=[])
            result = await service.identify_from_pcm(pcm_sufficient)

        assert result["speaker_id"] is None
        assert result["embedding_hash"] is not None


# ===========================================================================
# Integration Tests: _identify_ambient_speaker via EventMixin
# ===========================================================================


import base64 as _base64

# consumer_events.py imports voice_session_service at module level and
# speaker_service / django.conf.settings via local imports inside the method.
# Correct patch targets:
#   voice_session_service  → apps.voice.consumer_events.voice_session_service
#   speaker_service        → apps.voice.services.speaker_service.speaker_service
#   settings.*             → django.conf.settings.<attr>  (patch.object on the real settings)


def _make_fake_chunks():
    """Return raw PCM bytes (16000 bytes = 0.5 s at 16 kHz mono 16-bit).
    Matches get_audio_chunks() which already base64-decodes from Redis."""
    fake_pcm = b"\x00\x01" * 8000
    return [fake_pcm]


class MockConsumer:
    """Minimal consumer with EventMixin attributes for testing."""

    def __init__(self, user_id: int = 1):
        self.user_id = user_id
        self._mode = "ambient"
        self._send_json = AsyncMock()
        self._aggregator = MagicMock()
        self._aggregator.add = AsyncMock()
        self._aggregator.buffer_count = 1
        self._aggregator.timeout_remaining = 2.5
        self._speaker_aggregators = {}
        self._legacy_aggregate = AsyncMock()


def _make_mock_vss(chunks=None):
    m = MagicMock()
    m.get_audio_chunks = AsyncMock(return_value=chunks if chunks is not None else _make_fake_chunks())
    return m


class TestIdentifyAmbientSpeaker:
    """_identify_ambient_speaker 集成测试（EventMixin）"""

    @pytest.mark.asyncio
    async def test_identify_ambient_speaker_identified(self):
        """identify_from_pcm 返回 identified=True, 高置信度 → 返回 speaker_user_id"""
        from apps.voice.consumer_events import EventMixin
        from django.conf import settings as django_settings
        import apps.voice.services.speaker_service as ss_mod

        consumer = MockConsumer(user_id=1)
        mock_vss = _make_mock_vss()

        mock_ss = MagicMock()
        mock_ss.identify_from_pcm = AsyncMock(return_value={
            "speaker_id": "gw-spk-001",
            "confidence": 0.92,
            "embedding_hash": "abc123",
        })
        mock_ss.identify_speaker = AsyncMock(return_value={
            "user_id": 42,
            "username": "testuser",
            "speaker_name": "测试用户",
        })

        with patch("apps.voice.consumer_events.voice_session_service", mock_vss), \
             patch.object(ss_mod, "speaker_service", mock_ss):
            result = await EventMixin._identify_ambient_speaker(consumer, "seg-001")

        assert result is not None
        assert result["speaker_user_id"] == 42
        assert result["speaker_label"] == "测试用户"

    @pytest.mark.asyncio
    async def test_identify_ambient_speaker_not_matched(self):
        """identify_from_pcm 返回 speaker_id=None → 分配 unknown 标签"""
        from apps.voice.consumer_events import EventMixin
        import apps.voice.services.speaker_service as ss_mod
        import types

        consumer = MockConsumer(user_id=1)
        consumer._assign_unknown_label = types.MethodType(EventMixin._assign_unknown_label, consumer)
        mock_vss = _make_mock_vss()

        mock_ss = MagicMock()
        mock_ss.identify_from_pcm = AsyncMock(return_value={
            "speaker_id": None,
            "confidence": 0.1,
            "embedding_hash": "zzz000",
        })

        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value=None)
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.hset = AsyncMock(return_value=1)

        with patch("apps.voice.consumer_events.voice_session_service", mock_vss), \
             patch.object(ss_mod, "speaker_service", mock_ss), \
             patch("core.redis.get_async_redis_client", AsyncMock(return_value=mock_redis)):
            result = await EventMixin._identify_ambient_speaker(consumer, "seg-001")

        assert result is not None
        assert result["speaker_user_id"] is None
        assert result["speaker_label"] == "unknown_01"

    @pytest.mark.asyncio
    async def test_identify_ambient_speaker_gateway_error(self):
        """identify_from_pcm 抛异常 → 优雅降级返回 None"""
        from apps.voice.consumer_events import EventMixin
        import apps.voice.services.speaker_service as ss_mod

        consumer = MockConsumer(user_id=1)
        mock_vss = _make_mock_vss()

        mock_ss = MagicMock()
        mock_ss.identify_from_pcm = AsyncMock(
            side_effect=Exception("Gateway connection error")
        )

        with patch("apps.voice.consumer_events.voice_session_service", mock_vss), \
             patch.object(ss_mod, "speaker_service", mock_ss):
            result = await EventMixin._identify_ambient_speaker(consumer, "seg-001")

        assert result is None

    @pytest.mark.asyncio
    async def test_feature_flag_disabled_skips_identification(self):
        """VOICE_SPEAKER_IDENTIFICATION_ENABLED=False → _identify_ambient_speaker 不被调用"""
        from apps.voice.consumer_events import EventMixin
        from django.conf import settings as django_settings

        consumer = MockConsumer(user_id=1)
        consumer._aggregator = MagicMock()
        consumer._aggregator.add = AsyncMock()
        consumer._aggregator.buffer_count = 1
        consumer._aggregator.timeout_remaining = 2.5

        mock_vss = _make_mock_vss(chunks=[])

        identify_called = []
        original_identify = EventMixin._identify_ambient_speaker

        async def spy_identify(self_inner, seg_id):
            identify_called.append(seg_id)
            return await original_identify(self_inner, seg_id)

        with patch("apps.voice.consumer_events.voice_session_service", mock_vss), \
             patch.object(django_settings, "VOICE_SPEAKER_IDENTIFICATION_ENABLED", False), \
             patch.object(EventMixin, "_identify_ambient_speaker", spy_identify):
            await EventMixin._handle_ambient_transcription(
                consumer, "テスト音声", "seg-002"
            )

        assert len(identify_called) == 0, (
            "_identify_ambient_speaker must not be called when feature flag is disabled"
        )


# ===========================================================================
# Batch-01: _handle_ambient_transcription speaker labeling tests
# ===========================================================================


class TestHandleAmbientSpeakerLabeling:
    """_handle_ambient_transcription 中 speaker 标签传递流测试"""

    @pytest.mark.asyncio
    async def test_exception_fallback_assigns_unknown_label(self):
        """_identify_ambient_speaker 返回 None（异常路径）→ _last_unknown_label 仍被分配"""
        from apps.voice.consumer_events import EventMixin
        from django.conf import settings as django_settings

        consumer = MockConsumer(user_id=1)
        # Mock _identify_ambient_speaker on the instance (not class) since MockConsumer != EventMixin
        consumer._identify_ambient_speaker = AsyncMock(return_value=None)
        # Bind _assign_unknown_label from EventMixin so the fallback path works
        import types
        consumer._assign_unknown_label = types.MethodType(EventMixin._assign_unknown_label, consumer)

        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value=None)
        mock_redis.incr = AsyncMock(return_value=7)
        mock_redis.hset = AsyncMock(return_value=1)

        with patch.object(django_settings, "VOICE_SPEAKER_IDENTIFICATION_ENABLED", True), \
             patch("core.redis.get_async_redis_client", AsyncMock(return_value=mock_redis)):
            await EventMixin._handle_ambient_transcription(consumer, "你好", "seg-001")

        # After fix: exception fallback calls _assign_unknown_label(None) → "unknown_07"
        assert consumer._last_unknown_label == "unknown_07"
        consumer._legacy_aggregate.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_identified_sets_speaker_label(self):
        """_identify_ambient_speaker 返回 speaker_user_id=None → _last_unknown_label 被设置"""
        from apps.voice.consumer_events import EventMixin
        from django.conf import settings as django_settings

        consumer = MockConsumer(user_id=1)
        speaker_result = {"speaker_user_id": None, "speaker_label": "unknown_03"}
        consumer._identify_ambient_speaker = AsyncMock(return_value=speaker_result)

        with patch.object(django_settings, "VOICE_SPEAKER_IDENTIFICATION_ENABLED", True):
            await EventMixin._handle_ambient_transcription(consumer, "你好", "seg-001")

        assert consumer._last_unknown_label == "unknown_03"
        consumer._legacy_aggregate.assert_called_once()

    @pytest.mark.asyncio
    async def test_disabled_flag_no_label_set(self):
        """VOICE_SPEAKER_IDENTIFICATION_ENABLED=False → _last_unknown_label 不被设置"""
        from apps.voice.consumer_events import EventMixin
        from django.conf import settings as django_settings

        consumer = MockConsumer(user_id=1)

        with patch.object(django_settings, "VOICE_SPEAKER_IDENTIFICATION_ENABLED", False):
            await EventMixin._handle_ambient_transcription(consumer, "你好", "seg-001")

        assert not hasattr(consumer, "_last_unknown_label") or consumer._last_unknown_label is None
        consumer._legacy_aggregate.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_segment_different_labels_preserved(self):
        """连续两段未识别音频 → _last_unknown_label 分别被更新"""
        from apps.voice.consumer_events import EventMixin
        from django.conf import settings as django_settings

        consumer = MockConsumer(user_id=1)

        # First segment: unknown_01
        result1 = {"speaker_user_id": None, "speaker_label": "unknown_01"}
        consumer._identify_ambient_speaker = AsyncMock(return_value=result1)
        with patch.object(django_settings, "VOICE_SPEAKER_IDENTIFICATION_ENABLED", True):
            await EventMixin._handle_ambient_transcription(consumer, "第一段", "seg-001")
        assert consumer._last_unknown_label == "unknown_01"

        # Second segment: unknown_02
        result2 = {"speaker_user_id": None, "speaker_label": "unknown_02"}
        consumer._identify_ambient_speaker = AsyncMock(return_value=result2)
        with patch.object(django_settings, "VOICE_SPEAKER_IDENTIFICATION_ENABLED", True):
            await EventMixin._handle_ambient_transcription(consumer, "第二段", "seg-002")
        assert consumer._last_unknown_label == "unknown_02"
