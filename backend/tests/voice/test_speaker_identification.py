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
    """identify_from_pcm 声纹识别测试"""

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_identify_from_pcm_success(
        self,
        mock_async_client_cls,
        service,
        pcm_sufficient,
        mock_gateway_settings,
    ):
        """测试 Gateway 返回 identified=True 时正确返回 speaker_id 和 confidence"""
        mock_response = _make_mock_response(
            200,
            json_data={
                "identified": True,
                "speaker_id": "gw-speaker-001",
                "confidence": 0.92,
                "embedding_hash": "abc123def456",
            },
        )
        mock_async_client_cls.return_value = _make_mock_client(
            post_response=mock_response
        )

        result = await service.identify_from_pcm(pcm_sufficient)

        assert result["identified"] is True
        assert result["speaker_id"] == "gw-speaker-001"
        assert result["confidence"] == 0.92
        assert result["embedding_hash"] == "abc123def456"

        # 验证 POST 请求使用了正确的 URL 和 Authorization header
        mock_client = mock_async_client_cls.return_value
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/v1/voice/speakers/identify" in call_args.args[0]
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer test-api-key-123"

        # 验证音频以 WAV base64 编码传输（identify_from_pcm 先 PCM→WAV 再编码）
        from apps.voice.services.voice_persist_service import VoicePersistService
        expected_wav = VoicePersistService.merge_pcm_to_wav([pcm_sufficient])
        expected_b64 = base64.b64encode(expected_wav).decode("ascii")
        assert call_args.kwargs["json"]["audio"] == expected_b64

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_identify_from_pcm_not_identified(
        self,
        mock_async_client_cls,
        service,
        pcm_sufficient,
        mock_gateway_settings,
    ):
        """测试 Gateway 返回 identified=False 时返回未识别结果"""
        mock_response = _make_mock_response(
            200,
            json_data={
                "identified": False,
                "speaker_id": None,
                "confidence": 0.15,
                "embedding_hash": "zzzzzzzzzzzz",
            },
        )
        mock_async_client_cls.return_value = _make_mock_client(
            post_response=mock_response
        )

        result = await service.identify_from_pcm(pcm_sufficient)

        assert result["identified"] is False
        assert result["speaker_id"] is None
        assert result["confidence"] == 0.15

        # Gateway 应被调用（音频足够长）
        mock_client = mock_async_client_cls.return_value
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_identify_from_pcm_gateway_timeout(
        self,
        mock_async_client_cls,
        service,
        pcm_sufficient,
        mock_gateway_settings,
    ):
        """测试 Gateway 超时时优雅降级，返回 identified=False"""
        mock_async_client_cls.return_value = _make_mock_client(
            side_effect=httpx.TimeoutException("请求超时")
        )

        result = await service.identify_from_pcm(pcm_sufficient)

        assert result["identified"] is False
        assert result["speaker_id"] is None
        assert result["confidence"] == 0.0
        # embedding_hash 仍然基于本地计算的 md5 填充
        assert result["embedding_hash"] is not None

        # 验证 Gateway 被调用了（但抛了异常）
        mock_client = mock_async_client_cls.return_value
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_identify_from_pcm_gateway_error(
        self,
        mock_async_client_cls,
        service,
        pcm_sufficient,
        mock_gateway_settings,
    ):
        """测试 Gateway 返回 500 时优雅降级，返回 identified=False"""
        mock_response = _make_mock_response(500)
        mock_async_client_cls.return_value = _make_mock_client(
            post_response=mock_response
        )

        result = await service.identify_from_pcm(pcm_sufficient)

        assert result["identified"] is False
        assert result["speaker_id"] is None
        assert result["confidence"] == 0.0

        # Gateway 应被调用（音频足够长）
        mock_client = mock_async_client_cls.return_value
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_identify_from_pcm_audio_too_short(
        self,
        mock_async_client_cls,
        service,
        pcm_too_short,
        mock_gateway_settings,
    ):
        """测试音频过短（< 16000 bytes）时跳过 Gateway 调用，直接返回未识别"""
        result = await service.identify_from_pcm(pcm_too_short)

        assert result["identified"] is False
        assert result["speaker_id"] is None
        assert result["confidence"] == 0.0
        assert result["embedding_hash"] is None

        # 验证 Gateway 完全未被调用
        mock_async_client_cls.assert_not_called()


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
            "identified": True,
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
             patch.object(django_settings, "VOICE_SPEAKER_THRESHOLD", 0.5), \
             patch.object(ss_mod, "speaker_service", mock_ss):
            result = await EventMixin._identify_ambient_speaker(consumer, "seg-001")

        assert result is not None
        assert result["speaker_user_id"] == 42
        assert result["speaker_label"] == "测试用户"

    @pytest.mark.asyncio
    async def test_identify_ambient_speaker_low_confidence(self):
        """identify_from_pcm 返回 identified=True 但置信度低于 VOICE_SPEAKER_THRESHOLD → None"""
        from apps.voice.consumer_events import EventMixin
        from django.conf import settings as django_settings
        import apps.voice.services.speaker_service as ss_mod
        from core.redis import get_async_redis_client

        consumer = MockConsumer(user_id=1)
        mock_vss = _make_mock_vss()

        mock_ss = MagicMock()
        mock_ss.identify_from_pcm = AsyncMock(return_value={
            "identified": True,
            "speaker_id": "gw-spk-001",
            "confidence": 0.3,   # below threshold 0.5
            "embedding_hash": "low_conf_hash",
        })

        # _assign_unknown_label calls get_async_redis_client; provide a minimal stub
        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value=None)
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.hset = AsyncMock(return_value=1)

        with patch("apps.voice.consumer_events.voice_session_service", mock_vss), \
             patch.object(django_settings, "VOICE_SPEAKER_THRESHOLD", 0.5), \
             patch.object(ss_mod, "speaker_service", mock_ss), \
             patch("core.redis.get_async_redis_client", AsyncMock(return_value=mock_redis)):
            result = await EventMixin._identify_ambient_speaker(consumer, "seg-001")

        assert result is None

    @pytest.mark.asyncio
    async def test_identify_ambient_speaker_not_identified(self):
        """identify_from_pcm 返回 identified=False → None"""
        from apps.voice.consumer_events import EventMixin
        from django.conf import settings as django_settings
        import apps.voice.services.speaker_service as ss_mod

        consumer = MockConsumer(user_id=1)
        mock_vss = _make_mock_vss()

        mock_ss = MagicMock()
        mock_ss.identify_from_pcm = AsyncMock(return_value={
            "identified": False,
            "speaker_id": None,
            "confidence": 0.1,
            "embedding_hash": "zzz000",
        })

        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value=None)
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.hset = AsyncMock(return_value=1)

        with patch("apps.voice.consumer_events.voice_session_service", mock_vss), \
             patch.object(django_settings, "VOICE_SPEAKER_THRESHOLD", 0.5), \
             patch.object(ss_mod, "speaker_service", mock_ss), \
             patch("core.redis.get_async_redis_client", AsyncMock(return_value=mock_redis)):
            result = await EventMixin._identify_ambient_speaker(consumer, "seg-001")

        assert result is None

    @pytest.mark.asyncio
    async def test_identify_ambient_speaker_gateway_error(self):
        """identify_from_pcm 抛异常 → 优雅降级返回 None"""
        from apps.voice.consumer_events import EventMixin
        from django.conf import settings as django_settings
        import apps.voice.services.speaker_service as ss_mod

        consumer = MockConsumer(user_id=1)
        mock_vss = _make_mock_vss()

        mock_ss = MagicMock()
        mock_ss.identify_from_pcm = AsyncMock(
            side_effect=Exception("Gateway connection error")
        )

        with patch("apps.voice.consumer_events.voice_session_service", mock_vss), \
             patch.object(django_settings, "VOICE_SPEAKER_THRESHOLD", 0.5), \
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
