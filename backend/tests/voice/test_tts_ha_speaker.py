"""T025b: send_to_ha_speaker 单元测试

覆盖:
(1) xiaomi_miot.intelligent_speaker HTTP 200 — 文本直传成功，body 包含 text/execute/silent（非 message）
(2) intelligent_speaker HTTP 404 — 降级到 play_media 路径
(3) HA 完全不可达 (httpx.ConnectError) — 抛出 HASpeakerError
(4) HA 超时 (httpx.TimeoutException) — 抛出 HASpeakerError
(5) HA 返回 HTTP 500 — 抛出 HASpeakerError
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.voice.services.tts_router import HASpeakerError, TTSRouter

_MODULE = "apps.voice.services.tts_router"


def _inject_minio_mock():
    """注入 mock 的 apps.common.storage.minio_service 模块。"""
    mock_module = ModuleType("apps.common.storage.minio_service")
    mock_svc_instance = MagicMock()
    mock_svc_instance.upload_bytes = MagicMock(return_value="tts_ha/test.wav")
    mock_svc_class = MagicMock(return_value=mock_svc_instance)
    mock_module.MinIOService = mock_svc_class
    return mock_module, mock_svc_instance


@pytest.fixture
def mock_channel_layer():
    layer = AsyncMock()
    layer.group_send = AsyncMock()
    return layer


@pytest.fixture
def router(mock_channel_layer):
    with patch(f"{_MODULE}.get_channel_layer", return_value=mock_channel_layer):
        return TTSRouter()


def _mock_response(status_code: int = 200) -> MagicMock:
    """构造 mock httpx.Response。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp,
        )
    return resp


# ========================================================================
# (1) intelligent_speaker HTTP 200 — 直传成功
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestIntelligentSpeakerSuccess:
    """xiaomi_miot.intelligent_speaker 返回 200 — 文本直传成功。"""

    async def test_text_sent_correctly(self, router):
        """HTTP 200 时 send_to_ha_speaker 正常返回，不抛异常。"""
        resp_200 = _mock_response(200)

        with patch(f"{_MODULE}.settings") as mock_settings:
            mock_settings.HA_URL = "http://192.168.1.100:8123"
            mock_settings.HA_TOKEN = "test-token"

            with patch(f"{_MODULE}.httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=resp_200)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                await router.send_to_ha_speaker("media_player.xiaomi_lx06", "你好世界")

                mock_client.post.assert_called_once()

    async def test_body_contains_correct_fields(self, router):
        """请求 body 包含 text/execute/silent 字段（非 message 字段）。"""
        resp_200 = _mock_response(200)
        captured_json = {}

        async def capture_post(url, headers=None, json=None):
            captured_json.update(json or {})
            return resp_200

        with patch(f"{_MODULE}.settings") as mock_settings:
            mock_settings.HA_URL = "http://192.168.1.100:8123"
            mock_settings.HA_TOKEN = "test-token"

            with patch(f"{_MODULE}.httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=capture_post)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                await router.send_to_ha_speaker("media_player.xiaomi_lx06", "测试文本")

        assert "text" in captured_json
        assert captured_json["text"] == "测试文本"
        assert "execute" in captured_json
        assert captured_json["execute"] is False
        assert "silent" in captured_json
        assert captured_json["silent"] is False
        assert "entity_id" in captured_json
        assert captured_json["entity_id"] == "media_player.xiaomi_lx06"
        # 不应包含 message 字段
        assert "message" not in captured_json

    async def test_request_url_correct(self, router):
        """请求 URL 为 {HA_URL}/api/services/xiaomi_miot/intelligent_speaker。"""
        resp_200 = _mock_response(200)
        captured_url = []

        async def capture_post(url, **kwargs):
            captured_url.append(url)
            return resp_200

        with patch(f"{_MODULE}.settings") as mock_settings:
            mock_settings.HA_URL = "http://ha.local:8123"
            mock_settings.HA_TOKEN = "tok"

            with patch(f"{_MODULE}.httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=capture_post)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                await router.send_to_ha_speaker("media_player.x", "hi")

        assert captured_url[0] == "http://ha.local:8123/api/services/xiaomi_miot/intelligent_speaker"


# ========================================================================
# (2) intelligent_speaker HTTP 404 — 降级到 play_media
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestFallbackToPlayMedia:
    """intelligent_speaker 返回 404 时降级到 play_media。"""

    async def test_fallback_calls_play_media(self, router):
        """404 后调用 TTS 生成 + MinIO 上传 + play_media。"""
        resp_404 = _mock_response(404)
        resp_200 = _mock_response(200)
        call_urls = []

        async def mock_post(url, **kwargs):
            call_urls.append(url)
            if "intelligent_speaker" in url:
                return resp_404
            return resp_200

        fake_wav = b"RIFF" + b"\x00" * 100
        mock_mod, mock_storage = _inject_minio_mock()

        with patch(f"{_MODULE}.settings") as mock_settings:
            mock_settings.HA_URL = "http://ha:8123"
            mock_settings.HA_TOKEN = "tok"
            mock_settings.MINIO_AUDIO_BUCKET = "audio"
            mock_settings.HA_LAN_HOST = "192.168.1.100"

            with patch(f"{_MODULE}.httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=mock_post)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                with patch.object(TTSRouter, "_generate_tts_wav", new_callable=AsyncMock, return_value=fake_wav):
                    with patch.dict(sys.modules, {"apps.common.storage.minio_service": mock_mod}):
                        await router.send_to_ha_speaker("media_player.x", "降级测试")

        # 第一次调用 intelligent_speaker，第二次调用 play_media
        assert len(call_urls) == 2
        assert "intelligent_speaker" in call_urls[0]
        assert "play_media" in call_urls[1]

    async def test_fallback_uploads_to_minio(self, router):
        """降级路径中会上传 WAV 到 MinIO。"""
        resp_404 = _mock_response(404)
        resp_200 = _mock_response(200)

        async def mock_post(url, **kwargs):
            if "intelligent_speaker" in url:
                return resp_404
            return resp_200

        fake_wav = b"RIFF" + b"\x00" * 50
        mock_mod, mock_storage = _inject_minio_mock()

        with patch(f"{_MODULE}.settings") as mock_settings:
            mock_settings.HA_URL = "http://ha:8123"
            mock_settings.HA_TOKEN = "tok"
            mock_settings.MINIO_AUDIO_BUCKET = "audio"
            mock_settings.HA_LAN_HOST = "192.168.1.100"

            with patch(f"{_MODULE}.httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=mock_post)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                with patch.object(TTSRouter, "_generate_tts_wav", new_callable=AsyncMock, return_value=fake_wav):
                    with patch.dict(sys.modules, {"apps.common.storage.minio_service": mock_mod}):
                        await router.send_to_ha_speaker("media_player.x", "测试")

                        mock_storage.upload_bytes.assert_called_once()
                        call_kwargs = mock_storage.upload_bytes.call_args
                        # keyword args
                        kw = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
                        assert kw.get("bucket") == "audio"
                        assert kw.get("content_type") == "audio/wav"
                        assert kw.get("data") == fake_wav


# ========================================================================
# (3) HA 完全不可达 — ConnectError
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestHAUnreachable:
    """HA 完全不可达时抛出 HASpeakerError。"""

    async def test_connect_error_raises_ha_speaker_error(self, router):
        """httpx.ConnectError 被包装为 HASpeakerError。"""
        with patch(f"{_MODULE}.settings") as mock_settings:
            mock_settings.HA_URL = "http://ha:8123"
            mock_settings.HA_TOKEN = "tok"

            with patch(f"{_MODULE}.httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(
                    side_effect=httpx.ConnectError("Connection refused"),
                )
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                with pytest.raises(HASpeakerError, match="不可达"):
                    await router.send_to_ha_speaker("media_player.x", "hello")


# ========================================================================
# (4) HA 超时 — TimeoutException
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestHATimeout:
    """HA 请求超时抛出 HASpeakerError。"""

    async def test_timeout_raises_ha_speaker_error(self, router):
        """httpx.TimeoutException 被包装为 HASpeakerError。"""
        with patch(f"{_MODULE}.settings") as mock_settings:
            mock_settings.HA_URL = "http://ha:8123"
            mock_settings.HA_TOKEN = "tok"

            with patch(f"{_MODULE}.httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(
                    side_effect=httpx.TimeoutException("read timed out"),
                )
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                with pytest.raises(HASpeakerError, match="超时"):
                    await router.send_to_ha_speaker("media_player.x", "hello")


# ========================================================================
# (5) HA 返回 HTTP 500 — 服务端错误
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestHA500Error:
    """HA 返回 HTTP 500 时抛出 HASpeakerError。"""

    async def test_500_raises_ha_speaker_error(self, router):
        """HTTP 500 被包装为 HASpeakerError。"""
        resp_500 = _mock_response(500)

        with patch(f"{_MODULE}.settings") as mock_settings:
            mock_settings.HA_URL = "http://ha:8123"
            mock_settings.HA_TOKEN = "tok"

            with patch(f"{_MODULE}.httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=resp_500)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                with pytest.raises(HASpeakerError, match="服务端错误"):
                    await router.send_to_ha_speaker("media_player.x", "hello")
