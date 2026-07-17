"""补充覆盖率测试 — 覆盖多个服务的缺失行

覆盖:
1. voice_messages.py lines 22-23, 25-26: build_agent_error 各分支
2. ws_client_base.py lines 22-23, 71-76, 79, 82, 85: cleanup_ws_connection + _receive_loop 分支
3. tts_pipeline_manager.py lines 66-67, 69-72, 81-82, 118-119, 145-146: cancel/shutdown/play/drain 分支
4. voice_session_service.py lines 97-103: add_recent_speaker
5. tts_router.py lines 54, 104, 117: send_warning + send_to_ha_speaker 非预期响应
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ===========================================================================
# 1. voice_messages.py — build_agent_error branches (lines 22-23, 25-26)
# ===========================================================================


class TestBuildAgentError:
    """build_agent_error 各分支覆盖。"""

    def test_basic_error_no_data(self):
        """chunk.data 为 None/falsy 时返回默认 AGENT_ERROR。"""
        from apps.voice.services.voice_messages import build_agent_error

        chunk = MagicMock()
        chunk.content = "推理出错了"
        chunk.data = None

        result = build_agent_error(chunk)

        assert result["code"] == "AGENT_ERROR"
        assert result["message"] == "推理出错了"
        assert result["recoverable"] is True

    def test_gateway_error_code_overrides(self):
        """chunk.data 包含 gateway_error 时 code 被覆盖 (line 22-23)。"""
        from apps.voice.services.voice_messages import build_agent_error

        chunk = MagicMock()
        chunk.content = "some error"
        chunk.data = {"gateway_error": "RATE_LIMIT_EXCEEDED"}

        result = build_agent_error(chunk)

        assert result["code"] == "RATE_LIMIT_EXCEEDED"

    def test_content_control_sets_content_filter(self):
        """chunk.data 包含 content_control 时 code=CONTENT_FILTER (lines 25-26)。"""
        from apps.voice.services.voice_messages import build_agent_error

        chunk = MagicMock()
        chunk.content = "filtered"
        chunk.data = {
            "content_control": True,
            "replacement": "内容被过滤，请重新提问",
        }

        result = build_agent_error(chunk)

        assert result["code"] == "CONTENT_FILTER"
        assert result["message"] == "内容被过滤，请重新提问"

    def test_content_control_without_replacement_uses_original_message(self):
        """content_control=True 但无 replacement 时 message 保持原值。"""
        from apps.voice.services.voice_messages import build_agent_error

        chunk = MagicMock()
        chunk.content = "original error"
        chunk.data = {"content_control": True}

        result = build_agent_error(chunk)

        assert result["code"] == "CONTENT_FILTER"
        assert result["message"] == "original error"

    def test_retry_after_sets_not_recoverable(self):
        """chunk.data 包含 retry_after 时 recoverable=False (line 22, 25-26)。"""
        from apps.voice.services.voice_messages import build_agent_error

        chunk = MagicMock()
        chunk.content = "rate limited"
        chunk.data = {"retry_after": 30}

        result = build_agent_error(chunk)

        assert result["recoverable"] is False
        assert result["retry_after"] == 30

    def test_empty_content_uses_default_message(self):
        """chunk.content 为空时使用默认 message。"""
        from apps.voice.services.voice_messages import build_agent_error

        chunk = MagicMock()
        chunk.content = ""
        chunk.data = None

        result = build_agent_error(chunk)

        assert result["message"] == "Agent 推理出错"

    def test_all_data_fields_combined(self):
        """gateway_error + retry_after 组合。"""
        from apps.voice.services.voice_messages import build_agent_error

        chunk = MagicMock()
        chunk.content = "overload"
        chunk.data = {
            "gateway_error": "OVERLOAD",
            "retry_after": 60,
        }

        result = build_agent_error(chunk)

        assert result["code"] == "OVERLOAD"
        assert result["retry_after"] == 60
        assert result["recoverable"] is False


# ===========================================================================
# 2. ws_client_base.py — cleanup + _receive_loop branches
# ===========================================================================


class TestCleanupWsConnection:
    """cleanup_ws_connection lines 22-23: recv_task cancel + await path。"""

    @pytest.mark.asyncio
    async def test_cleanup_cancels_running_task(self):
        """recv_task 未完成时: cancel() + await (lines 14-18)。"""
        from apps.voice.services.ws_client_base import cleanup_ws_connection

        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()

        task = asyncio.create_task(asyncio.sleep(100))
        await cleanup_ws_connection(mock_ws, task)

        assert task.cancelled()
        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_done_task_not_cancelled(self):
        """recv_task 已完成时不调用 cancel()。"""
        from apps.voice.services.ws_client_base import cleanup_ws_connection

        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()

        task = asyncio.create_task(asyncio.sleep(0))
        await asyncio.sleep(0)  # let it finish

        await cleanup_ws_connection(mock_ws, task)

        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_none_task(self):
        """recv_task=None 时不出错。"""
        from apps.voice.services.ws_client_base import cleanup_ws_connection

        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()

        await cleanup_ws_connection(mock_ws, None)

        mock_ws.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_none_ws(self):
        """ws=None 时不出错，不调用 close。"""
        from apps.voice.services.ws_client_base import cleanup_ws_connection

        await cleanup_ws_connection(None, None)  # no exception

    @pytest.mark.asyncio
    async def test_cleanup_ws_close_exception_swallowed(self):
        """ws.close() 抛异常时被吞掉 (lines 22-23)。"""
        from apps.voice.services.ws_client_base import cleanup_ws_connection

        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock(side_effect=Exception("close failed"))

        await cleanup_ws_connection(mock_ws, None)  # no exception raised

    @pytest.mark.asyncio
    async def test_cleanup_task_cancelled_error_swallowed(self):
        """task await 抛 CancelledError 被吞掉 (lines 16-18)。"""
        from apps.voice.services.ws_client_base import cleanup_ws_connection

        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()

        # Create a task that raises CancelledError when awaited after cancel
        async def slow():
            await asyncio.sleep(10)

        task = asyncio.create_task(slow())
        # Cancel it before cleanup
        task.cancel()

        await cleanup_ws_connection(mock_ws, task)  # no exception


class TestReceiveLoopBranches:
    """_receive_loop error branches (lines 71-76, 79, 82, 85)。"""

    @pytest.mark.asyncio
    async def test_receive_loop_cancelled_error_exits_cleanly(self):
        """asyncio.CancelledError 时静默退出 (line 71-72)。"""
        import websockets.exceptions
        from apps.voice.services.ws_client_base import BaseWSClient

        class TestClient(BaseWSClient):
            async def _handle_message(self, msg):
                pass

        client = TestClient()
        client._connected = True

        # Create a mock ws that raises CancelledError on iteration
        async def aiter_raises():
            raise asyncio.CancelledError()
            yield  # make it async generator

        mock_ws = MagicMock()
        mock_ws.__aiter__ = MagicMock(return_value=aiter_raises())
        client._ws = mock_ws

        # Should complete without raising
        await client._receive_loop()
        # CancelledError path: _connected stays True (no change in that branch)

    @pytest.mark.asyncio
    async def test_receive_loop_connection_closed_sets_not_connected(self):
        """ConnectionClosed 时 _connected=False 并调用 _on_connection_lost (lines 67-70)。"""
        import websockets.exceptions
        from apps.voice.services.ws_client_base import BaseWSClient

        connection_lost_called = []

        class TestClient(BaseWSClient):
            async def _handle_message(self, msg):
                pass

            async def _on_connection_lost(self, err):
                connection_lost_called.append(err)

        client = TestClient()
        client._connected = True

        rcvd = MagicMock()
        rcvd.code = 1001
        rcvd.reason = "going away"
        exc = websockets.exceptions.ConnectionClosed(rcvd=rcvd, sent=None)

        async def aiter_raises():
            raise exc
            yield

        mock_ws = MagicMock()
        mock_ws.__aiter__ = MagicMock(return_value=aiter_raises())
        client._ws = mock_ws

        await client._receive_loop()

        assert client._connected is False
        assert len(connection_lost_called) == 1

    @pytest.mark.asyncio
    async def test_receive_loop_generic_error_calls_on_error(self):
        """非CancelledError/ConnectionClosed异常: _connected=False, _on_error (lines 73-76)。"""
        from apps.voice.services.ws_client_base import BaseWSClient

        on_error_called = []

        class TestClient(BaseWSClient):
            async def _handle_message(self, msg):
                pass

            async def _on_error(self, err):
                on_error_called.append(err)

        client = TestClient()
        client._connected = True

        async def aiter_raises():
            raise RuntimeError("network failure")
            yield

        mock_ws = MagicMock()
        mock_ws.__aiter__ = MagicMock(return_value=aiter_raises())
        client._ws = mock_ws

        await client._receive_loop()

        assert client._connected is False
        assert len(on_error_called) == 1
        assert isinstance(on_error_called[0], RuntimeError)

    def test_on_connection_lost_default_noop(self):
        """默认 _on_connection_lost 不抛异常 (line 82)。"""
        import asyncio
        from apps.voice.services.ws_client_base import BaseWSClient

        class ConcreteClient(BaseWSClient):
            async def _handle_message(self, msg):
                pass

        client = ConcreteClient()
        # Should complete without error
        asyncio.run(client._on_connection_lost(Exception("test")))

    def test_on_error_default_noop(self):
        """默认 _on_error 不抛异常 (line 85)。"""
        import asyncio
        from apps.voice.services.ws_client_base import BaseWSClient

        class ConcreteClient(BaseWSClient):
            async def _handle_message(self, msg):
                pass

        client = ConcreteClient()
        asyncio.run(client._on_error(RuntimeError("test")))


# ===========================================================================
# 3. tts_pipeline_manager.py — cancel/shutdown/play/drain branches
# ===========================================================================

_MGR_MODULE = "apps.voice.services.tts_pipeline_manager"
_MGR_SETTINGS = f"{_MGR_MODULE}.settings"


def _patch_mgr_settings(
    comfort_delay: float = 10.0,
    segment_gap: float = 0.0,
    comfort_texts=None,
    tts_timeout: int = 5,
):
    from unittest.mock import MagicMock, patch
    texts = comfort_texts if comfort_texts is not None else ["c1", "c2", "c3"]
    s = MagicMock()
    s.VOICE_TTS_COMFORT_DELAY = comfort_delay
    s.VOICE_TTS_SEGMENT_GAP = segment_gap
    s.VOICE_TTS_COMFORT_TEXTS = texts
    s.VOICE_TTS_TIMEOUT = tts_timeout
    s.VOICE_TTS_URL = "ws://test:8100/v1/audio/speech/stream"
    s.VOICE_TTS_VOICE = "test_voice"
    s.LLM_GATEWAY_API_KEY = "test_key"
    return patch(_MGR_SETTINGS, s)


def _patch_tts():
    mock_tts = MagicMock()
    mock_tts.connect = AsyncMock(return_value="session")
    mock_tts.configure = AsyncMock()
    mock_tts.send_text_delta = AsyncMock()
    mock_tts.send_text_done = AsyncMock()
    mock_tts.wait_for_done = AsyncMock()
    mock_tts.disconnect = AsyncMock()
    mock_tts.connected = True
    return patch(f"{_MGR_MODULE}.TTSStreamClient", return_value=mock_tts), mock_tts


@pytest.mark.asyncio(loop_scope="function")
class TestTtsPipelineManagerExtraCoverage:
    """TTSPipelineManager 缺失行覆盖。"""

    async def test_cancel_with_no_current_tts(self):
        """cancel 时 _current_tts=None 不报错 (lines 66-67 skip path)。"""
        from apps.voice.services.tts_pipeline_manager import TTSPipelineManager

        tts_patch, mock_tts = _patch_tts()
        with _patch_mgr_settings():
            with tts_patch:
                mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
                mgr.start()
                mgr.stop_comfort_timer()
                # Don't enqueue anything → _current_tts stays None
                await mgr.cancel()

        assert mgr._cancelled is True

    async def test_cancel_current_tts_disconnect_exception_swallowed(self):
        """cancel 时 disconnect() 抛异常被吞掉 (lines 69-72)。"""
        from apps.voice.services.tts_pipeline_manager import TTSPipelineManager

        tts_patch, mock_tts = _patch_tts()
        mock_tts.disconnect = AsyncMock(side_effect=Exception("disconnect error"))

        with _patch_mgr_settings(comfort_delay=0.01):
            with tts_patch:
                mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
                mgr.start()
                await asyncio.sleep(0.05)
                # Should not raise even if disconnect fails
                await mgr.cancel()

        assert mgr._cancelled is True

    async def test_shutdown_timeout_cancels_worker(self):
        """shutdown 等待 worker 超时时调用 cancel_task (lines 81-82)。"""
        from apps.voice.services.tts_pipeline_manager import TTSPipelineManager

        tts_patch, mock_tts = _patch_tts()
        # Make wait_for_done block forever to simulate hung worker
        mock_tts.wait_for_done = AsyncMock(side_effect=asyncio.sleep(100))

        with _patch_mgr_settings(comfort_delay=10.0):
            with tts_patch:
                mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
                mgr.start()
                mgr.stop_comfort_timer()
                mgr.enqueue("long text", "response")

                await asyncio.sleep(0.02)

                # Patch asyncio.wait_for to raise TimeoutError immediately
                with patch(f"{_MGR_MODULE}.asyncio.wait_for",
                           side_effect=asyncio.TimeoutError):
                    await mgr.shutdown()

    async def test_play_text_exception_logs_warning(self):
        """_play_text 中 connect 失败后 disconnect 被调用 (lines 118-119)。"""
        from apps.voice.services.tts_pipeline_manager import TTSPipelineManager

        tts_patch, mock_tts = _patch_tts()
        mock_tts.connect = AsyncMock(side_effect=Exception("connection refused"))

        with _patch_mgr_settings(comfort_delay=10.0):
            with tts_patch:
                mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
                mgr.start()
                mgr.stop_comfort_timer()
                mgr.enqueue("test text", "response")

                await mgr.wait_idle()
                await mgr.shutdown()

        # disconnect should still be called in finally block
        mock_tts.disconnect.assert_called()

    async def test_drain_comfort_from_queue_keeps_non_comfort(self):
        """_drain_comfort_from_queue: comfort 被移除，非 comfort 保留 (lines 145-146)。"""
        from apps.voice.services.tts_pipeline_manager import QueueItem, TTSPipelineManager

        mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
        mgr._queue.put_nowait(QueueItem(text="c", item_type="comfort"))
        mgr._queue.put_nowait(QueueItem(text="r", item_type="response"))
        mgr._queue.put_nowait(QueueItem(text="s", item_type="sentinel"))

        mgr._drain_comfort_from_queue()

        items = []
        while not mgr._queue.empty():
            items.append(mgr._queue.get_nowait())

        texts = [i.text for i in items]
        assert "r" in texts
        assert "s" in texts
        assert "c" not in texts

    async def test_drain_comfort_empty_queue(self):
        """_drain_comfort_from_queue 空队列不报错 (lines 138-148 loop exits immediately)。"""
        from apps.voice.services.tts_pipeline_manager import TTSPipelineManager

        mgr = TTSPipelineManager(on_audio=AsyncMock(), voice="test")
        mgr._drain_comfort_from_queue()  # no exception

        assert mgr._queue.empty()


# ===========================================================================
# 4. voice_session_service.py — add_recent_speaker (lines 97-103)
# ===========================================================================


class TestAddRecentSpeaker:
    """add_recent_speaker — 向 Redis Set 添加说话人并设置 TTL。"""

    @pytest.mark.asyncio
    async def test_add_recent_speaker_calls_sadd_and_expire(self):
        """sadd + expire 被调用，key 格式正确 (lines 97-103)。"""
        from apps.voice.services.voice_session_service import VoiceSessionService

        mock_redis = AsyncMock()
        mock_redis.sadd = AsyncMock()
        mock_redis.expire = AsyncMock()
        mock_redis.aclose = AsyncMock()

        with patch("apps.voice.services.voice_session_service.get_redis",
                   return_value=mock_redis):
            svc = VoiceSessionService()
            await svc.add_recent_speaker(owner_user_id=42, speaker_user_id=99)

        mock_redis.sadd.assert_called_once_with("voice:recent_speakers:42", "99")
        mock_redis.expire.assert_called_once_with("voice:recent_speakers:42", 60)
        mock_redis.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_recent_speaker_different_owner(self):
        """不同 owner_user_id 使用不同 key。"""
        from apps.voice.services.voice_session_service import VoiceSessionService

        mock_redis = AsyncMock()

        with patch("apps.voice.services.voice_session_service.get_redis",
                   return_value=mock_redis):
            svc = VoiceSessionService()
            await svc.add_recent_speaker(owner_user_id=10, speaker_user_id=20)

        mock_redis.sadd.assert_called_once_with("voice:recent_speakers:10", "20")

    @pytest.mark.asyncio
    async def test_add_recent_speaker_closes_redis_on_success(self):
        """成功时 redis.aclose() 被调用 (finally 块)。"""
        from apps.voice.services.voice_session_service import VoiceSessionService

        mock_redis = AsyncMock()

        with patch("apps.voice.services.voice_session_service.get_redis",
                   return_value=mock_redis):
            svc = VoiceSessionService()
            await svc.add_recent_speaker(owner_user_id=5, speaker_user_id=6)

        mock_redis.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_recent_speaker_speaker_id_is_string(self):
        """speaker_user_id 转为字符串传给 sadd (str(speaker_user_id))。"""
        from apps.voice.services.voice_session_service import VoiceSessionService

        mock_redis = AsyncMock()

        with patch("apps.voice.services.voice_session_service.get_redis",
                   return_value=mock_redis):
            svc = VoiceSessionService()
            await svc.add_recent_speaker(owner_user_id=1, speaker_user_id=999)

        call_args = mock_redis.sadd.call_args[0]
        assert call_args[1] == "999"  # str conversion


# ===========================================================================
# 5. tts_router.py — send_warning (line 54) + non-expected HA response (line 104)
# ===========================================================================

_ROUTER_MODULE = "apps.voice.services.tts_router"


@pytest.fixture
def mock_channel_layer_fixture():
    layer = AsyncMock()
    layer.group_send = AsyncMock()
    return layer


@pytest.fixture
def router_fixture(mock_channel_layer_fixture):
    from apps.voice.services.tts_router import TTSRouter
    with patch(f"{_ROUTER_MODULE}.get_channel_layer", return_value=mock_channel_layer_fixture):
        return TTSRouter()


@pytest.mark.asyncio(loop_scope="function")
class TestSendWarning:
    """send_warning — line 54: group_send tts_control with warning payload。"""

    async def test_send_warning_format(self, router_fixture, mock_channel_layer_fixture):
        """send_warning 发送 tts_control 类型的 warning 消息 (line 54)。"""
        await router_fixture.send_warning(42, reason="fallback", message="降级播报")

        mock_channel_layer_fixture.group_send.assert_called_once_with(
            "voice_tts_42",
            {
                "type": "tts_control",
                "payload": {
                    "type": "warning",
                    "reason": "fallback",
                    "message": "降级播报",
                },
            },
        )

    async def test_send_warning_correct_group(self, router_fixture, mock_channel_layer_fixture):
        """send_warning 使用 voice_tts_{user_id} group。"""
        await router_fixture.send_warning(7, reason="r", message="m")

        call = mock_channel_layer_fixture.group_send.call_args[0]
        assert call[0] == "voice_tts_7"

    async def test_send_warning_payload_structure(self, router_fixture, mock_channel_layer_fixture):
        """payload 包含 type/reason/message 三个字段。"""
        await router_fixture.send_warning(1, reason="minio_fail", message="音频上传失败")

        sent = mock_channel_layer_fixture.group_send.call_args[0][1]
        payload = sent["payload"]
        assert payload["type"] == "warning"
        assert payload["reason"] == "minio_fail"
        assert payload["message"] == "音频上传失败"


@pytest.mark.asyncio(loop_scope="function")
class TestHASpeakerNonExpectedResponse:
    """send_to_ha_speaker: non-404/non-5xx (line 104) 降级路径。"""

    async def test_non_404_non_5xx_logs_warning_and_falls_through(
        self, router_fixture
    ):
        """xiaomi_miot 返回 403 时记录 warning 然后进入降级 (line 104)。"""
        import sys
        from types import ModuleType

        import httpx

        from apps.voice.services.tts_router import HASpeakerError

        resp_403 = MagicMock(spec=httpx.Response)
        resp_403.status_code = 403

        fake_wav = b"RIFF" + b"\x00" * 40
        mock_mod = ModuleType("apps.common.storage.minio_service")
        mock_storage = MagicMock()
        mock_storage.upload_bytes = MagicMock()
        mock_mod.MinIOService = MagicMock(return_value=mock_storage)

        resp_200 = MagicMock(spec=httpx.Response)
        resp_200.status_code = 200
        resp_200.raise_for_status = MagicMock()

        call_urls = []

        async def mock_post(url, **kwargs):
            call_urls.append(url)
            if "intelligent_speaker" in url:
                return resp_403
            return resp_200

        with patch(f"{_ROUTER_MODULE}.settings") as ms:
            ms.HA_URL = "http://ha:8123"
            ms.HA_TOKEN = "tok"
            ms.MINIO_AUDIO_BUCKET = "audio"
            ms.HA_LAN_HOST = "192.168.1.1"

            with patch(f"{_ROUTER_MODULE}.httpx.AsyncClient") as MockClient:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=mock_post)
                MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

                from apps.voice.services.tts_router import TTSRouter

                with patch.object(
                    TTSRouter, "_generate_tts_wav",
                    new_callable=AsyncMock, return_value=fake_wav
                ):
                    with patch.dict(
                        sys.modules, {"apps.common.storage.minio_service": mock_mod}
                    ):
                        await router_fixture.send_to_ha_speaker("media_player.x", "test")

        # Should have called intelligent_speaker (403) then play_media
        assert len(call_urls) == 2
        assert "intelligent_speaker" in call_urls[0]
        assert "play_media" in call_urls[1]


@pytest.mark.asyncio(loop_scope="function")
class TestGenerateTtsWav:
    """_generate_tts_wav — lines 147-163: TTS WAV 生成。"""

    async def test_generate_tts_wav_exception_returns_none(self):
        """TTS 出错时返回 None，不抛异常 (lines 161-163)。"""
        from apps.voice.services.tts_router import TTSRouter

        # TTSStreamClient is lazily imported inside _generate_tts_wav,
        # patch the module where it's defined so the lazy import picks it up.
        with patch(
            "apps.voice.services.tts_stream_client.TTSStreamClient",
            side_effect=Exception("TTS unavailable")
        ):
            with patch(f"{_ROUTER_MODULE}.settings") as ms:
                ms.VOICE_TTS_VOICE = "test_voice"
                # Patch the lazy import path used inside _generate_tts_wav
                with patch.dict(
                    "sys.modules",
                    {"apps.voice.services.tts_stream_client": MagicMock(
                        TTSStreamClient=MagicMock(side_effect=Exception("TTS unavailable"))
                    )},
                ):
                    result = await TTSRouter._generate_tts_wav("hello")

        assert result is None

    async def test_generate_tts_wav_no_chunks_returns_none(self):
        """TTS 完成但无音频 chunks 时返回 None (line 157-158)。"""
        from apps.voice.services.tts_router import TTSRouter

        mock_tts_instance = AsyncMock()
        mock_tts_instance.connect = AsyncMock()
        mock_tts_instance.configure = AsyncMock()
        mock_tts_instance.send_text_delta = AsyncMock()
        mock_tts_instance.send_text_done = AsyncMock()
        mock_tts_instance.wait_for_done = AsyncMock()
        mock_tts_instance.disconnect = AsyncMock()

        mock_tts_cls = MagicMock(return_value=mock_tts_instance)

        with patch(f"{_ROUTER_MODULE}.settings") as ms:
            ms.VOICE_TTS_VOICE = "test_voice"

            # Patch the lazy import inside _generate_tts_wav via sys.modules
            import sys
            fake_module = MagicMock()
            fake_module.TTSStreamClient = mock_tts_cls
            with patch.dict(sys.modules, {"apps.voice.services.tts_stream_client": fake_module}):
                result = await TTSRouter._generate_tts_wav("hello")

        # No on_audio calls → chunks=[] → return None
        assert result is None

    async def test_generate_tts_wav_with_audio_chunks(self):
        """on_audio 被调用时 chunks 非空，返回 WAV bytes (lines 157-160)。

        on_audio is a synchronous lambda (chunks.append), so fake_wait
        appends directly to the captured chunks list via the closure.
        voice_persist_service is lazily imported — patch it via sys.modules.
        """
        from apps.voice.services.tts_router import TTSRouter
        import sys

        fake_wav = b"RIFF" + b"\x00" * 20

        # The real code: client = TTSStreamClient(on_audio=lambda d: chunks.append(d))
        # on_audio is sync. We capture the instance to inject audio into chunks.
        captured_inst = {}

        def fake_tts_cls(on_audio):
            inst = MagicMock()
            inst.connect = AsyncMock()
            inst.configure = AsyncMock()
            inst.send_text_delta = AsyncMock()
            inst.send_text_done = AsyncMock()
            inst.disconnect = AsyncMock()

            async def fake_wait(timeout=30):
                # on_audio is sync (lambda d: chunks.append(d)), call it directly
                on_audio(b"\x00\x01\x02\x03")

            inst.wait_for_done = fake_wait
            return inst

        fake_tts_module = MagicMock()
        fake_tts_module.TTSStreamClient = fake_tts_cls

        # voice_persist_service is lazily imported inside _generate_tts_wav.
        # Patch it via sys.modules so the local import picks up our mock.
        mock_vps = MagicMock()
        mock_vps.merge_pcm_to_wav.return_value = fake_wav
        fake_persist_module = MagicMock()
        fake_persist_module.voice_persist_service = mock_vps

        with patch(f"{_ROUTER_MODULE}.settings") as ms:
            ms.VOICE_TTS_VOICE = "test_voice"

            with patch.dict(sys.modules, {
                "apps.voice.services.tts_stream_client": fake_tts_module,
                "apps.voice.services.voice_persist_service": fake_persist_module,
            }):
                result = await TTSRouter._generate_tts_wav("hi")

        assert result == fake_wav
        mock_vps.merge_pcm_to_wav.assert_called_once_with([b"\x00\x01\x02\x03"])
