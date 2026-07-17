"""batch-09: TTS 增量流式送稿单元测试。

覆盖 VoicePipeline._run_inner 的增量送稿路径（VOICE_TTS_INCREMENTAL_ENABLED）与
_split_sentences 句子切分器：
- 开关 off → 保持整体 enqueue 旧路径（零回归门槛）
- 开关 on → 按句子边界 begin_stream/feed_text/end_stream
- 最小分片长度防碎片化 / 尾巴 flush / error 中途 abort / interrupted 收播 / ambient 轻量路径
- full_response 旁路不被吞字
- _split_sentences 纯单测（中英文标点、小数点保护、min_chars 边界）

新增用例独立成文件（不塞进 test_voice_pipeline.py），旧文件既有用例继续全过。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import override_settings

from apps.chat.services.types import StreamChunk
from apps.voice.services.voice_pipeline import _is_en_sentence_dot, _split_sentences

_VP = "apps.voice.services.voice_pipeline"
_ALS = "apps.voice.services.ambient_light_service"


def _make_consumer() -> MagicMock:
    consumer = MagicMock()
    consumer._send_json = AsyncMock()
    consumer._send_binary = AsyncMock()
    return consumer


def _content(text: str) -> StreamChunk:
    return StreamChunk(type="content", content=text)


def _done() -> StreamChunk:
    return StreamChunk(type="done", content="", message_id=1)


def _error(msg: str = "网关超时") -> StreamChunk:
    return StreamChunk(type="error", content=msg, data={"gateway_error": "TIMEOUT"})


def _interrupted() -> StreamChunk:
    return StreamChunk(type="interrupted", content="")


def _agent_gen(*chunks):
    async def gen(*args, **kwargs):
        for c in chunks:
            yield c
    return gen


# ──────────────────────────────────────────────
# fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def mock_inference_svc():
    with patch(f"{_VP}.InferenceService") as cls:
        cls.register_task = AsyncMock(return_value=True)
        cls.cancel_task = AsyncMock(return_value=(True, "req"))
        cls.complete_task = AsyncMock()
        yield cls


@pytest.fixture
def mock_rate_limit():
    with patch(f"{_VP}.voice_session_service") as svc:
        svc.check_llm_rate_limit = AsyncMock(return_value=True)
        svc.set_active_conversation = AsyncMock()
        yield svc


@pytest.fixture
def mock_persist():
    with patch(f"{_VP}.voice_persist_service") as svc:
        svc.persist_audio_attachment = AsyncMock()
        yield svc


@pytest.fixture
def mock_tts():
    """Mock TTSPipelineManager，含 batch-09 流式会话动词。"""
    with patch(f"{_VP}.TTSPipelineManager") as cls:
        mgr = MagicMock()
        mgr.start = MagicMock()
        mgr.enqueue = MagicMock()
        mgr.stop_comfort_timer = MagicMock()
        mgr.begin_stream = MagicMock()
        mgr.feed_text = MagicMock()
        mgr.end_stream = MagicMock()
        mgr.abort_stream = AsyncMock()
        mgr.wait_idle = AsyncMock()
        mgr.shutdown = AsyncMock()
        mgr.cancel = AsyncMock()
        cls.return_value = mgr
        yield cls, mgr


async def _run(user_id: int, agent_chunks, mode: str = "voice_chat"):
    """便捷执行 run_pipeline，返回 consumer。调用方负责已 patch 依赖。"""
    from apps.voice.services.voice_pipeline import VoicePipeline, _pipeline_locks
    _pipeline_locks.pop(user_id, None)
    consumer = _make_consumer()
    with patch(f"{_VP}.AgentService") as MockAgent:
        MockAgent.execute = MagicMock(side_effect=_agent_gen(*agent_chunks))
        await VoicePipeline.run_pipeline(
            user_id=user_id, text="测试", segment_id=f"seg-{user_id}",
            consumer=consumer, mode=mode)
    return consumer


# ──────────────────────────────────────────────
# _split_sentences 纯单测
# ──────────────────────────────────────────────

class TestSentenceSplitter:

    def test_chinese_punctuation_splits(self):
        out, tail = _split_sentences("今天天气非常好呀。我们出去散步吧！", min_chars=8)
        assert out == ["今天天气非常好呀。", "我们出去散步吧！"]
        assert tail == ""

    def test_min_chars_keeps_fragment_as_tail(self):
        out, tail = _split_sentences("嗯。", min_chars=8)
        assert out == []
        assert tail == "嗯。"

    def test_fragment_merges_with_following(self):
        out, tail = _split_sentences("嗯。好的没问题呀哈哈。", min_chars=8)
        assert out == ["嗯。好的没问题呀哈哈。"]
        assert tail == ""

    def test_no_terminator_returns_all_as_tail(self):
        out, tail = _split_sentences("这是一段没有标点的文字", min_chars=8)
        assert out == []
        assert tail == "这是一段没有标点的文字"

    def test_english_sentence_dot_splits(self):
        out, tail = _split_sentences("This is a sentence. Next part", min_chars=8)
        assert out == ["This is a sentence."]
        assert tail == " Next part"

    def test_decimal_point_not_split(self):
        # 小数点 3.14 不应被当作句子边界
        assert _is_en_sentence_dot("pi is 3.14 now", 8) is False
        out, tail = _split_sentences("the value is 3.14 exactly", min_chars=5)
        assert out == []

    def test_semicolon_and_newline_split(self):
        out, _ = _split_sentences("第一部分内容；第二部分内容\n", min_chars=6)
        assert out == ["第一部分内容；", "第二部分内容\n"]


# ──────────────────────────────────────────────
# 增量送稿路径
# ──────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
class TestIncrementalStreaming:

    @override_settings(VOICE_TTS_INCREMENTAL_ENABLED=False)
    async def test_incremental_disabled_keeps_enqueue_full(
        self, mock_inference_svc, mock_rate_limit, mock_persist, mock_tts
    ):
        """开关 off → 整体 enqueue 旧路径，不触发流式会话。"""
        _, mgr = mock_tts
        await _run(1001, [_content("你好"), _content("，世界。"), _done()])
        mgr.enqueue.assert_called_once_with("你好，世界。", "response")
        mgr.begin_stream.assert_not_called()
        mgr.feed_text.assert_not_called()
        mgr.end_stream.assert_not_called()

    @override_settings(VOICE_TTS_INCREMENTAL_ENABLED=True)
    async def test_incremental_feeds_per_sentence(
        self, mock_inference_svc, mock_rate_limit, mock_persist, mock_tts
    ):
        """开关 on → 按句子边界 begin_stream 1 次、feed_text 逐句、end_stream 1 次，不整体 enqueue。"""
        _, mgr = mock_tts
        await _run(1002, [
            _content("今天天气"), _content("非常好呀。我们"),
            _content("出去散步吧！"), _done()])
        mgr.begin_stream.assert_called_once()
        fed = [c.args[0] for c in mgr.feed_text.call_args_list]
        assert fed == ["今天天气非常好呀。", "我们出去散步吧！"]
        mgr.end_stream.assert_called_once()
        mgr.enqueue.assert_not_called()

    @override_settings(VOICE_TTS_INCREMENTAL_ENABLED=True)
    async def test_incremental_min_chars_no_fragment(
        self, mock_inference_svc, mock_rate_limit, mock_persist, mock_tts
    ):
        """短碎片 '嗯。'(<min) 不单独成句，与后文合并送出。"""
        _, mgr = mock_tts
        await _run(1003, [_content("嗯。"), _content("好的没问题呀哈哈。"), _done()])
        fed = [c.args[0] for c in mgr.feed_text.call_args_list]
        assert fed == ["嗯。好的没问题呀哈哈。"]

    @override_settings(VOICE_TTS_INCREMENTAL_ENABLED=True)
    async def test_incremental_tail_flush(
        self, mock_inference_svc, mock_rate_limit, mock_persist, mock_tts
    ):
        """无终止标点残句在 end_stream 前被 feed_text flush。"""
        _, mgr = mock_tts
        await _run(1004, [_content("这是第一句话内容。"), _content("还有一点尾巴"), _done()])
        fed = [c.args[0] for c in mgr.feed_text.call_args_list]
        assert fed == ["这是第一句话内容。", "还有一点尾巴"]
        mgr.end_stream.assert_called_once()

    @override_settings(VOICE_TTS_INCREMENTAL_ENABLED=True)
    async def test_incremental_error_mid_stream_aborts(
        self, mock_inference_svc, mock_rate_limit, mock_persist, mock_tts
    ):
        """流式中 error chunk → abort_stream 丢弃半截会话 + error_text enqueue。"""
        from django.conf import settings
        _, mgr = mock_tts
        consumer = await _run(1005, [_content("这是一个很长的句子。"), _error()])
        mgr.begin_stream.assert_called_once()
        mgr.abort_stream.assert_awaited_once()
        mgr.enqueue.assert_any_call(settings.VOICE_TTS_ERROR_TEXT, "error")
        types = [c.args[0]["type"] for c in consumer._send_json.call_args_list]
        assert "error" in types

    @override_settings(VOICE_TTS_INCREMENTAL_ENABLED=True)
    async def test_incremental_interrupted_finishes_partial(
        self, mock_inference_svc, mock_rate_limit, mock_persist, mock_tts
    ):
        """interrupted → 已产文本收尾播完（feed 尾巴 + end_stream），不 abort。"""
        _, mgr = mock_tts
        await _run(1006, [_content("这是第一句话内容。"), _content("还有一点尾巴"), _interrupted()])
        fed = [c.args[0] for c in mgr.feed_text.call_args_list]
        assert fed == ["这是第一句话内容。", "还有一点尾巴"]
        mgr.end_stream.assert_called_once()
        mgr.abort_stream.assert_not_awaited()

    @override_settings(VOICE_TTS_INCREMENTAL_ENABLED=True, VOICE_AMBIENT_LIGHT_ENABLED=True)
    async def test_incremental_ambient_light_path(
        self, mock_inference_svc, mock_rate_limit, mock_persist, mock_tts
    ):
        """ambient + 轻量路径 → 增量送稿同样生效（三路共用循环体）。"""
        _, mgr = mock_tts
        from apps.voice.services.voice_pipeline import VoicePipeline, _pipeline_locks
        _pipeline_locks.pop(1007, None)
        consumer = _make_consumer()
        with (
            patch(f"{_VP}.AgentService") as MockAgent,
            patch("apps.voice.services.tts_router.TTSRouter") as MockRouter,
            patch(f"{_VP}.VoicePipeline._try_ha_speaker_tts", new=AsyncMock()),
            patch(f"{_ALS}.AmbientLightPipeline") as MockLight,
        ):
            router_inst = MagicMock()
            router_inst.get_on_audio_callback.return_value = AsyncMock()
            router_inst.send_control = AsyncMock()
            MockRouter.return_value = router_inst
            MockLight.stream = MagicMock(side_effect=_agent_gen(
                _content("这是轻量路径的回复。"), _done()))
            await VoicePipeline.run_pipeline(
                user_id=1007, text="帮我开灯", segment_id="seg-1007",
                consumer=consumer, mode="ambient")
            MockAgent.execute.assert_not_called()
        mgr.begin_stream.assert_called_once()
        fed = [c.args[0] for c in mgr.feed_text.call_args_list]
        assert fed == ["这是轻量路径的回复。"]

    @override_settings(VOICE_TTS_INCREMENTAL_ENABLED=True)
    async def test_incremental_full_response_still_accumulated(
        self, mock_inference_svc, mock_rate_limit, mock_persist, mock_tts
    ):
        """增量送稿是旁路叠加：full_response（HA/持久化依赖）完整不被吞字。"""
        from apps.voice.services.voice_pipeline import VoicePipeline, _pipeline_locks
        _pipeline_locks.pop(1008, None)
        consumer = _make_consumer()
        ha_spy = AsyncMock()
        with (
            patch(f"{_VP}.AgentService") as MockAgent,
            patch("apps.voice.services.tts_router.TTSRouter") as MockRouter,
            patch(f"{_VP}.VoicePipeline._try_ha_speaker_tts", new=ha_spy),
            patch(f"{_ALS}.AmbientLightPipeline") as MockLight,
            override_settings(VOICE_AMBIENT_LIGHT_ENABLED=True),
        ):
            router_inst = MagicMock()
            router_inst.get_on_audio_callback.return_value = AsyncMock()
            router_inst.send_control = AsyncMock()
            MockRouter.return_value = router_inst
            MockLight.stream = MagicMock(side_effect=_agent_gen(
                _content("第一句话内容在这。"), _content("第二句话也在这。"), _done()))
            del MockAgent  # ambient light path
            await VoicePipeline.run_pipeline(
                user_id=1008, text="帮我开灯", segment_id="seg-1008",
                consumer=consumer, mode="ambient")
        # full_response 完整传给 HA 音箱路由（旁路切句未影响累积）
        ha_spy.assert_awaited_once()
        assert ha_spy.await_args.args[1] == "第一句话内容在这。第二句话也在这。"
        # 同时前端 delta 拼接也完整
        deltas = [c.args[0]["data"]["delta"]["content"]
                  for c in consumer._send_json.call_args_list
                  if c.args[0]["type"] == "response.delta"]
        assert "".join(deltas) == "第一句话内容在这。第二句话也在这。"
