"""EventMixin（apps.voice.consumer_events）分支单测 — batch-20。

覆盖 consumer_events.py 基线未命中的关键分支（plan 5.4）：
  _handle_asr_event 未知/已知 type 分发、_on_vad_speech_start 模式分支、
  _on_transcription_completed 空文本 / ambient / 非 ambient、
  _handle_ambient_transcription 紧急停止 & 已识别说话人、
  _identify_ambient_speaker no_audio & profile 缺失、
  _legacy_aggregate 无 aggregator fallback、_on_asr_error 不可恢复重连失败。

测试方式：以 EventMixin.<method>(mock_consumer, ...) 的 unbound 姿势调用，
mock_consumer 为预置共享属性的 MagicMock（运行时 EventMixin 不真正继承 Protocol，
__bases__ == (object,)，故直接构造轻量宿主对象即可）。全程 mock 协作者，运行时零真实 IO。
"""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings

from apps.voice.consumer_events import EventMixin

pytestmark = pytest.mark.django_db


def _make_consumer(user_id: int = 1, mode: str = "ambient"):
    """构造仅含 EventMixin 依赖的轻量宿主对象（预置共享属性 + mock 协作方法）。"""
    c = MagicMock()
    c.user_id = user_id
    c.username = "tester"
    c._mode = mode
    c._current_segment_id = "seg00001"
    c._vad_start_ts = None
    c._is_speaking = False
    c._last_activity = 0.0
    c._aggregator = None
    c._speaker_aggregators = {}
    c._last_unknown_label = None
    c._asr_client = MagicMock()
    # async 协作方法
    c._send_json = AsyncMock()
    c._start_voice_pipeline = AsyncMock()
    c._reconnect_asr = AsyncMock()
    c.close = AsyncMock()
    c._identify_ambient_speaker = AsyncMock()
    c._assign_unknown_label = AsyncMock(return_value="unknown_01")
    c._handle_ambient_transcription = AsyncMock()
    c._legacy_aggregate = AsyncMock()
    c._on_vad_speech_start = AsyncMock()
    c._on_vad_speech_end = AsyncMock()
    c._on_transcription_completed = AsyncMock()
    c._on_transcription_failed = AsyncMock()
    c._on_asr_error = AsyncMock()
    # sync 协作方法
    c._start_segment_timer = MagicMock()
    c._get_or_create_aggregator = MagicMock()
    return c


def _sent_types(consumer):
    """收集 consumer._send_json 收到的所有 payload 的 type 字段。"""
    return [call.args[0]["type"] for call in consumer._send_json.await_args_list]


# =====================================================================
# _handle_asr_event 分发
# =====================================================================
class TestHandleAsrEvent:
    @pytest.mark.asyncio
    async def test_unknown_type_silently_returns(self):
        """未知事件 type → handlers.get 返回 None → 静默返回，无任何 handler 触发。"""
        c = _make_consumer()
        await EventMixin._handle_asr_event(c, {"type": "does.not.exist"})
        for name in ("_on_vad_speech_start", "_on_transcription_completed", "_on_asr_error"):
            getattr(c, name).assert_not_awaited()

    @pytest.mark.asyncio
    async def test_known_type_dispatched_to_handler(self):
        """已知 type → 分发到对应 handler 一次。"""
        c = _make_consumer()
        event = {"type": "transcription.completed", "text": "hi"}
        await EventMixin._handle_asr_event(c, event)
        c._on_transcription_completed.assert_awaited_once_with(event)


# =====================================================================
# _on_vad_speech_start 模式分支（line 37-39）
# =====================================================================
class TestOnVadSpeechStart:
    @pytest.mark.asyncio
    async def test_voice_chat_mode_sets_active_conversation(self):
        """非 ambient（voice_chat）→ 设置 active_conversation + 启动分段计时。"""
        c = _make_consumer(mode="voice_chat")
        vss = MagicMock()
        vss.set_active_conversation = AsyncMock()
        with patch("apps.voice.consumer_events.voice_session_service", vss), \
                patch("apps.voice.consumer_events.latency_anchor"):
            await EventMixin._on_vad_speech_start(c, {"timestamp": 1})
        vss.set_active_conversation.assert_awaited_once_with(c.user_id)
        c._start_segment_timer.assert_called_once()
        assert "vad.speech_start" in _sent_types(c)

    @pytest.mark.asyncio
    async def test_ambient_mode_skips_active_conversation(self):
        """ambient 模式 → 不在 VAD 阶段设置 active_conversation。"""
        c = _make_consumer(mode="ambient")
        vss = MagicMock()
        vss.set_active_conversation = AsyncMock()
        with patch("apps.voice.consumer_events.voice_session_service", vss), \
                patch("apps.voice.consumer_events.latency_anchor"):
            await EventMixin._on_vad_speech_start(c, {"timestamp": 1})
        vss.set_active_conversation.assert_not_awaited()


# =====================================================================
# _on_transcription_completed 分支（line 64-73）
# =====================================================================
class TestOnTranscriptionCompleted:
    @pytest.mark.asyncio
    async def test_empty_text_sends_transcription_failed(self):
        """空文本 → transcription.failed，不进入 pipeline。"""
        c = _make_consumer(mode="voice_chat")
        with patch("apps.voice.consumer_events.latency_record"):
            await EventMixin._on_transcription_completed(c, {"text": "   "})
        assert "transcription.failed" in _sent_types(c)
        c._start_voice_pipeline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_ambient_starts_pipeline(self):
        """非 ambient + 有文本 → 调用 _start_voice_pipeline。"""
        c = _make_consumer(mode="voice_chat")
        with patch("apps.voice.consumer_events.latency_record"):
            await EventMixin._on_transcription_completed(c, {"text": "hello"})
        c._start_voice_pipeline.assert_awaited_once_with(c._current_segment_id, "hello")
        c._handle_ambient_transcription.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ambient_delegates_to_handle_ambient(self):
        """ambient + 有文本 → 委派 _handle_ambient_transcription，不直接进 pipeline。"""
        c = _make_consumer(mode="ambient")
        with patch("apps.voice.consumer_events.latency_record"):
            await EventMixin._on_transcription_completed(c, {"text": "hello"})
        c._handle_ambient_transcription.assert_awaited_once_with("hello", c._current_segment_id)
        c._start_voice_pipeline.assert_not_awaited()


# =====================================================================
# _handle_ambient_transcription 分支（line 81-103）
# =====================================================================
class TestHandleAmbientTranscription:
    @pytest.mark.asyncio
    async def test_emergency_stop_resets_and_cancels(self):
        """命中紧急停止词 → reset 聚合器 + VoicePipeline.cancel + decision.result STOP。"""
        c = _make_consumer()
        agg = MagicMock()
        c._aggregator = agg
        spk_agg = MagicMock()
        c._speaker_aggregators = {5: spk_agg}
        with patch(
            "apps.voice.services.response_decision_service.ResponseDecisionService._check_emergency_stop",
            return_value=True,
        ), patch("apps.voice.services.voice_pipeline.VoicePipeline.cancel", new=AsyncMock()) as cancel:
            await EventMixin._handle_ambient_transcription(c, "停止", "seg1")
        agg.reset.assert_called_once()
        spk_agg.reset.assert_called_once()
        cancel.assert_awaited_once_with(c.user_id)
        assert "decision.result" in _sent_types(c)

    @override_settings(VOICE_SPEAKER_IDENTIFICATION_ENABLED=True)
    @pytest.mark.asyncio
    async def test_identified_speaker_uses_per_speaker_aggregator(self):
        """已识别说话人 → per-speaker 聚合器 + aggregation.utterance_added（带 speaker_user_id）。"""
        c = _make_consumer()
        c._identify_ambient_speaker = AsyncMock(return_value={"speaker_user_id": 7})
        agg = MagicMock()
        agg.add = AsyncMock()
        agg.buffer_count = 2
        agg.timeout_remaining = 1.5
        c._get_or_create_aggregator = MagicMock(return_value=agg)
        with patch(
            "apps.voice.services.response_decision_service.ResponseDecisionService._check_emergency_stop",
            return_value=False,
        ):
            await EventMixin._handle_ambient_transcription(c, "你好", "seg1")
        c._get_or_create_aggregator.assert_called_once_with(7)
        agg.add.assert_awaited_once_with("你好")
        added = [call.args[0] for call in c._send_json.await_args_list
                 if call.args[0]["type"] == "aggregation.utterance_added"]
        assert added and added[0]["data"]["speaker_user_id"] == 7


# =====================================================================
# _identify_ambient_speaker 分支（line 119-155）
# =====================================================================
class TestIdentifyAmbientSpeaker:
    @pytest.mark.asyncio
    async def test_no_audio_chunks_returns_none(self):
        """无音频 chunk → 返回 None（no_audio 分支）。"""
        c = _make_consumer()
        vss = MagicMock()
        vss.get_audio_chunks = AsyncMock(return_value=[])
        with patch("apps.voice.consumer_events.voice_session_service", vss):
            result = await EventMixin._identify_ambient_speaker(c, "seg1")
        assert result is None

    @pytest.mark.asyncio
    async def test_gateway_speaker_not_in_profile_returns_unknown(self):
        """Gateway 返回 speaker_id 但 SpeakerProfile 缺失 → 走 unknown 标签，speaker_user_id=None。"""
        c = _make_consumer()
        vss = MagicMock()
        vss.get_audio_chunks = AsyncMock(return_value=[b"pcmdata"])
        spk = MagicMock()
        spk.identify_from_pcm = AsyncMock(
            return_value={"speaker_id": "gw_x", "confidence": 0.9, "embedding_hash": "h1"}
        )
        spk.identify_speaker = AsyncMock(return_value=None)  # profile 缺失
        with patch("apps.voice.consumer_events.voice_session_service", vss), \
                patch("apps.voice.services.speaker_service.speaker_service", spk):
            result = await EventMixin._identify_ambient_speaker(c, "seg1")
        assert result == {"speaker_user_id": None, "speaker_label": "unknown_01"}
        assert "speaker.identified" in _sent_types(c)

    # batch-29: speaker_identify 跳进 latency tracker（三条 return 路径均记录）
    @pytest.mark.asyncio
    async def test_no_audio_records_speaker_identify_hop(self):
        """no_audio 分支记录 speaker_identify 跳（user_id=self.user_id, 同 asr_approx 惯例）。"""
        c = _make_consumer()
        vss = MagicMock()
        vss.get_audio_chunks = AsyncMock(return_value=[])
        with patch("apps.voice.consumer_events.voice_session_service", vss), \
                patch("apps.voice.consumer_events.latency_record") as rec:
            await EventMixin._identify_ambient_speaker(c, "seg1")
        rec.assert_any_call(c.user_id, "seg1", "speaker_identify", ANY)

    @pytest.mark.asyncio
    async def test_identified_speaker_records_speaker_identify_hop(self):
        """已识别说话人分支记录 speaker_identify 跳。"""
        c = _make_consumer()
        vss = MagicMock()
        vss.get_audio_chunks = AsyncMock(return_value=[b"pcmdata"])
        spk = MagicMock()
        spk.identify_from_pcm = AsyncMock(return_value={"speaker_id": "gw_x", "confidence": 0.9})
        spk.identify_speaker = AsyncMock(return_value={"user_id": 7, "speaker_name": "Alice"})
        with patch("apps.voice.consumer_events.voice_session_service", vss), \
                patch("apps.voice.services.speaker_service.speaker_service", spk), \
                patch("apps.voice.consumer_events.latency_record") as rec:
            result = await EventMixin._identify_ambient_speaker(c, "seg1")
        assert result["speaker_user_id"] == 7
        rec.assert_any_call(c.user_id, "seg1", "speaker_identify", ANY)

    @pytest.mark.asyncio
    async def test_unknown_speaker_records_speaker_identify_hop(self):
        """unknown 分支记录 speaker_identify 跳。"""
        c = _make_consumer()
        vss = MagicMock()
        vss.get_audio_chunks = AsyncMock(return_value=[b"pcmdata"])
        spk = MagicMock()
        spk.identify_from_pcm = AsyncMock(
            return_value={"speaker_id": None, "confidence": 0.1, "embedding_hash": "h1"})
        with patch("apps.voice.consumer_events.voice_session_service", vss), \
                patch("apps.voice.services.speaker_service.speaker_service", spk), \
                patch("apps.voice.consumer_events.latency_record") as rec:
            await EventMixin._identify_ambient_speaker(c, "seg1")
        rec.assert_any_call(c.user_id, "seg1", "speaker_identify", ANY)


# =====================================================================
# _legacy_aggregate 无 aggregator fallback（line 188-190）
# =====================================================================
class TestLegacyAggregate:
    @pytest.mark.asyncio
    async def test_no_aggregator_falls_back_to_pipeline(self):
        """无 aggregator → fallback 到 _start_voice_pipeline。"""
        c = _make_consumer()
        c._aggregator = None
        await EventMixin._legacy_aggregate(c, "hello", "seg1")
        c._start_voice_pipeline.assert_awaited_once_with("seg1", "hello")


# =====================================================================
# _on_asr_error 不可恢复 + 重连失败（line 204-211）
# =====================================================================
class TestOnAsrError:
    @pytest.mark.asyncio
    async def test_unrecoverable_reconnect_failed_closes_session(self):
        """不可恢复（CONNECTION_CLOSED）+ ambient 重连失败 → session.closed + close(4002)。"""
        c = _make_consumer(mode="ambient")
        c._asr_client.connected = False  # 重连后仍未连上
        await EventMixin._on_asr_error(c, {"code": "CONNECTION_CLOSED", "message": "lost"})
        c._reconnect_asr.assert_awaited_once()
        assert "session.closed" in _sent_types(c)
        c.close.assert_awaited_once_with(code=4002)
