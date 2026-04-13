"""T027b: VoicePipeline._try_ha_speaker_tts 路由测试

覆盖:
(1) tts_output_device="browser" → 提前返回，send_to_ha_speaker 不被调用
(2) tts_output_device="ha_speaker" + 有效 entity_id → send_to_ha_speaker 被调用
(3) HA 音箱不可达 (HASpeakerError) → 降级到浏览器，send_warning 被调用且 reason="ha_speaker_unreachable"
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.voice.services.tts_router import HASpeakerError
from apps.voice.services.voice_pipeline import VoicePipeline

_PIPELINE_MODULE = "apps.voice.services.voice_pipeline"
_ROUTER_MODULE = "apps.voice.services.tts_router"


def _make_voice_settings(tts_output_device: str = "browser",
                         ha_speaker_entity_id: str | None = None) -> MagicMock:
    """构造 mock VoiceSettings 对象。"""
    vs = MagicMock()
    vs.tts_output_device = tts_output_device
    vs.ha_speaker_entity_id = ha_speaker_entity_id
    return vs


# ========================================================================
# (1) tts_output_device="browser" — 提前返回
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestBrowserModeSkipsHaSpeaker:
    """browser 模式下 _try_ha_speaker_tts 提前返回。"""

    async def test_browser_mode_does_not_call_send_to_ha_speaker(self):
        """tts_output_device=browser → send_to_ha_speaker 不被调用。"""
        vs = _make_voice_settings("browser", None)

        with patch("apps.voice.repositories.voice_settings_repo") as mock_repo:
            mock_repo.get_or_create = AsyncMock(return_value=(vs, False))

            with patch(f"{_ROUTER_MODULE}.get_channel_layer", return_value=AsyncMock()):
                with patch.object(
                    __import__("apps.voice.services.tts_router", fromlist=["TTSRouter"]).TTSRouter,
                    "send_to_ha_speaker",
                    new_callable=AsyncMock,
                ) as mock_send:
                    await VoicePipeline._try_ha_speaker_tts(1, "你好")
                    mock_send.assert_not_called()

    async def test_browser_mode_with_entity_id_still_skips(self):
        """browser 模式即使有 entity_id 也不调用 send_to_ha_speaker。"""
        vs = _make_voice_settings("browser", "media_player.xiaomi_lx06")

        with patch("apps.voice.repositories.voice_settings_repo") as mock_repo:
            mock_repo.get_or_create = AsyncMock(return_value=(vs, False))

            with patch(f"{_ROUTER_MODULE}.get_channel_layer", return_value=AsyncMock()):
                with patch.object(
                    __import__("apps.voice.services.tts_router", fromlist=["TTSRouter"]).TTSRouter,
                    "send_to_ha_speaker",
                    new_callable=AsyncMock,
                ) as mock_send:
                    await VoicePipeline._try_ha_speaker_tts(1, "你好")
                    mock_send.assert_not_called()

    async def test_ha_speaker_mode_without_entity_id_skips(self):
        """ha_speaker 模式但 entity_id 为空 → 也提前返回。"""
        vs = _make_voice_settings("ha_speaker", None)

        with patch("apps.voice.repositories.voice_settings_repo") as mock_repo:
            mock_repo.get_or_create = AsyncMock(return_value=(vs, False))

            with patch(f"{_ROUTER_MODULE}.get_channel_layer", return_value=AsyncMock()):
                with patch.object(
                    __import__("apps.voice.services.tts_router", fromlist=["TTSRouter"]).TTSRouter,
                    "send_to_ha_speaker",
                    new_callable=AsyncMock,
                ) as mock_send:
                    await VoicePipeline._try_ha_speaker_tts(1, "你好")
                    mock_send.assert_not_called()


# ========================================================================
# (2) tts_output_device="ha_speaker" — 调用 send_to_ha_speaker
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestHaSpeakerModeCalls:
    """ha_speaker 模式下 _try_ha_speaker_tts 调用 send_to_ha_speaker。"""

    async def test_ha_speaker_calls_send_to_ha_speaker(self):
        """ha_speaker + 有效 entity_id → send_to_ha_speaker 被调用。"""
        vs = _make_voice_settings("ha_speaker", "media_player.xiaomi_lx06")

        with patch("apps.voice.repositories.voice_settings_repo") as mock_repo:
            mock_repo.get_or_create = AsyncMock(return_value=(vs, False))

            with patch(f"{_ROUTER_MODULE}.get_channel_layer", return_value=AsyncMock()):
                with patch.object(
                    __import__("apps.voice.services.tts_router", fromlist=["TTSRouter"]).TTSRouter,
                    "send_to_ha_speaker",
                    new_callable=AsyncMock,
                ) as mock_send:
                    await VoicePipeline._try_ha_speaker_tts(1, "今天天气不错")
                    mock_send.assert_called_once_with("media_player.xiaomi_lx06", "今天天气不错")

    async def test_ha_speaker_passes_correct_text(self):
        """send_to_ha_speaker 收到完整文本。"""
        vs = _make_voice_settings("ha_speaker", "media_player.test")
        long_text = "这是一段很长的文本" * 10

        with patch("apps.voice.repositories.voice_settings_repo") as mock_repo:
            mock_repo.get_or_create = AsyncMock(return_value=(vs, False))

            with patch(f"{_ROUTER_MODULE}.get_channel_layer", return_value=AsyncMock()):
                with patch.object(
                    __import__("apps.voice.services.tts_router", fromlist=["TTSRouter"]).TTSRouter,
                    "send_to_ha_speaker",
                    new_callable=AsyncMock,
                ) as mock_send:
                    await VoicePipeline._try_ha_speaker_tts(1, long_text)
                    mock_send.assert_called_once_with("media_player.test", long_text)


# ========================================================================
# (3) HASpeakerError — 降级到浏览器 + send_warning
# ========================================================================


@pytest.mark.asyncio(loop_scope="function")
class TestHaSpeakerFallback:
    """HA 音箱不可达时降级到浏览器并推送 warning。"""

    async def test_ha_speaker_error_triggers_warning(self):
        """HASpeakerError → send_warning 被调用。"""
        vs = _make_voice_settings("ha_speaker", "media_player.xiaomi_lx06")

        with patch("apps.voice.repositories.voice_settings_repo") as mock_repo:
            mock_repo.get_or_create = AsyncMock(return_value=(vs, False))

            with patch(f"{_ROUTER_MODULE}.get_channel_layer", return_value=AsyncMock()):
                TTSRouterCls = __import__(
                    "apps.voice.services.tts_router", fromlist=["TTSRouter"]
                ).TTSRouter

                with patch.object(
                    TTSRouterCls, "send_to_ha_speaker",
                    new_callable=AsyncMock,
                    side_effect=HASpeakerError("Connection refused"),
                ):
                    with patch.object(
                        TTSRouterCls, "send_warning",
                        new_callable=AsyncMock,
                    ) as mock_warning:
                        await VoicePipeline._try_ha_speaker_tts(1, "你好")

                        mock_warning.assert_called_once()

    async def test_warning_reason_is_ha_speaker_unreachable(self):
        """send_warning 的 reason 参数为 ha_speaker_unreachable。"""
        vs = _make_voice_settings("ha_speaker", "media_player.xiaomi_lx06")

        with patch("apps.voice.repositories.voice_settings_repo") as mock_repo:
            mock_repo.get_or_create = AsyncMock(return_value=(vs, False))

            with patch(f"{_ROUTER_MODULE}.get_channel_layer", return_value=AsyncMock()):
                TTSRouterCls = __import__(
                    "apps.voice.services.tts_router", fromlist=["TTSRouter"]
                ).TTSRouter

                with patch.object(
                    TTSRouterCls, "send_to_ha_speaker",
                    new_callable=AsyncMock,
                    side_effect=HASpeakerError("timeout"),
                ):
                    with patch.object(
                        TTSRouterCls, "send_warning",
                        new_callable=AsyncMock,
                    ) as mock_warning:
                        await VoicePipeline._try_ha_speaker_tts(42, "测试")

                        call_args = mock_warning.call_args
                        assert call_args[0][0] == 42  # user_id
                        assert call_args[0][1] == "ha_speaker_unreachable"  # reason

    async def test_ha_speaker_error_does_not_propagate(self):
        """HASpeakerError 不向上传播（降级处理，不崩溃管道）。"""
        vs = _make_voice_settings("ha_speaker", "media_player.xiaomi_lx06")

        with patch("apps.voice.repositories.voice_settings_repo") as mock_repo:
            mock_repo.get_or_create = AsyncMock(return_value=(vs, False))

            with patch(f"{_ROUTER_MODULE}.get_channel_layer", return_value=AsyncMock()):
                TTSRouterCls = __import__(
                    "apps.voice.services.tts_router", fromlist=["TTSRouter"]
                ).TTSRouter

                with patch.object(
                    TTSRouterCls, "send_to_ha_speaker",
                    new_callable=AsyncMock,
                    side_effect=HASpeakerError("HA 不可达"),
                ):
                    with patch.object(TTSRouterCls, "send_warning", new_callable=AsyncMock):
                        # 不应抛出异常
                        await VoicePipeline._try_ha_speaker_tts(1, "不应崩溃")
