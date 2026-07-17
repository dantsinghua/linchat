"""batch-07: 端到端语音延迟收集器（apps.voice.services.voice_latency）单元测试。

验证按 user_id:segment_id 聚合各跳耗时、双 total 口径、覆盖率 delta_pct、
子阶段跳（llm_first_token）不计入 hop_sum、flush 弹出防泄漏、无 key 安全 no-op。

用可控假时钟（patch monotonic）使 total / delta_pct 断言确定化，仿 test_trace_id_propagation.py
用 _ListHandler + TraceIdFilter 捕获 stage=latency.summary 日志。
"""
import logging

import pytest

from apps.voice.services import voice_latency
from apps.voice.services.voice_latency import (
    latency_anchor,
    latency_flush,
    latency_record,
    latency_start,
)
from core.logging_config import TraceIdFilter


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _Clock:
    """单调递增的假时钟，秒为单位。"""
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, secs: float) -> None:
        self.t += secs


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(voice_latency.time, "monotonic", c)
    yield c


@pytest.fixture(autouse=True)
def _clean_registry():
    voice_latency._LATENCY.clear()
    yield
    voice_latency._LATENCY.clear()


@pytest.fixture
def summary_handler():
    logger = logging.getLogger("apps.voice.services.voice_latency")
    handler = _ListHandler()
    handler.addFilter(TraceIdFilter())
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


def _summaries(handler):
    return [r for r in handler.records if getattr(r, "stage", None) == "latency.summary"]


def test_flush_emits_single_summary_with_fields(clock, summary_handler):
    """T1: start + record + flush → 恰好一条 latency.summary，含 hops/total/delta_pct/trace_id。"""
    latency_start(1, "segA")
    latency_record(1, "segA", "llm_total", 2000)
    latency_record(1, "segA", "tts_synth", 1500)
    clock.advance(3.5)
    latency_flush(1, "segA")

    recs = _summaries(summary_handler)
    assert len(recs) == 1
    rec = recs[0]
    assert rec.seg == "segA"
    assert rec.hops == {"llm_total": 2000, "tts_synth": 1500}
    assert rec.hop_sum_ms == 3500
    assert rec.total_from_pipeline_ms == 3500
    assert rec.delta_pct == 0.0
    assert hasattr(rec, "trace_id")  # TraceIdFilter 注入


def test_dual_total_from_anchors(clock, summary_handler):
    """T2: vad_start / speech_end 锚点 → total_from_vad_ms / total_from_speech_end_ms。"""
    clock.t = 998.0
    latency_anchor(1, "segB", "vad_start")
    clock.t = 999.0
    latency_anchor(1, "segB", "speech_end")
    clock.t = 1000.0
    latency_start(1, "segB")
    latency_record(1, "segB", "llm_total", 4000)
    clock.t = 1004.5  # .5 秒可精确表示，避免浮点截断误差
    latency_flush(1, "segB")

    rec = _summaries(summary_handler)[0]
    assert rec.total_from_pipeline_ms == 4500
    assert rec.total_from_vad_ms == 6500
    assert rec.total_from_speech_end_ms == 5500


def test_subphase_hop_excluded_from_sum(clock, summary_handler):
    """T3: llm_first_token 属子阶段，出现在 hops 但不计入 hop_sum；delta_pct 覆盖率 <5%。"""
    latency_start(1, "segC")
    latency_record(1, "segC", "asr", 800)
    latency_record(1, "segC", "llm_first_token", 500)  # 子阶段，不计入
    latency_record(1, "segC", "llm_total", 2000)
    latency_record(1, "segC", "tts_connect", 100)
    latency_record(1, "segC", "tts_synth", 1500)
    clock.advance(4.5)  # .5 秒可精确表示
    latency_flush(1, "segC")

    rec = _summaries(summary_handler)[0]
    assert "llm_first_token" in rec.hops
    assert rec.hop_sum_ms == 800 + 2000 + 100 + 1500  # 4400，排除 llm_first_token
    assert rec.total_from_pipeline_ms == 4500
    assert abs(rec.delta_pct) < 0.05  # manual「误差 < 5%」(100/4500≈2.2%)


def test_flush_pops_entry_and_double_flush_noop(clock, summary_handler):
    """T4: flush 弹出 entry 防泄漏；重复 flush / 未知 key flush 均安全 no-op。"""
    latency_start(1, "segD")
    latency_record(1, "segD", "llm_total", 1000)
    latency_flush(1, "segD")
    assert "1:segD" not in voice_latency._LATENCY
    latency_flush(1, "segD")  # 第二次：no-op
    latency_flush(1, "never")  # 未知 key：no-op
    assert len(_summaries(summary_handler)) == 1


def test_empty_segment_id_is_noop(clock, summary_handler):
    """T5: segment_id 为空时所有入口安全 no-op，不建 entry、不产汇总行。"""
    latency_start(1, "")
    latency_anchor(1, "", "vad_start")
    latency_record(1, "", "asr", 100)
    latency_flush(1, "")
    assert voice_latency._LATENCY == {}
    assert _summaries(summary_handler) == []


def test_max_entries_eviction(clock):
    """T6: 只 anchor/record 不 flush 的段超过上限时，按插入序淘汰最旧，防内存泄漏。"""
    for i in range(voice_latency._MAX_ENTRIES + 10):
        latency_start(1, f"seg{i}")
    assert len(voice_latency._LATENCY) <= voice_latency._MAX_ENTRIES
    assert "1:seg0" not in voice_latency._LATENCY  # 最旧已被淘汰


def test_delta_vad_pct_covers_pre_pipeline_hops(clock, summary_handler):
    """batch-29-T7: 三缺跳补入后 hop_sum 跨越 t0 之前，delta_pct（对 pipeline 段）失真变负，
    新增 delta_vad_pct 以 total_from_vad_ms 为基准衡量整链覆盖率误差 < 10%。"""
    clock.t = 1000.0
    latency_anchor(1, "segV", "vad_start")
    # t0 之前三缺跳（本批新增归因）
    latency_record(1, "segV", "speaker_identify", 400)
    latency_record(1, "segV", "aggregation_wait", 1500)
    latency_record(1, "segV", "decision_llm", 1100)
    clock.t = 1003.0  # pipeline 起点：vad 后 3.0s
    latency_start(1, "segV")
    latency_record(1, "segV", "llm_total", 1800)
    latency_record(1, "segV", "tts_connect", 100)
    latency_record(1, "segV", "tts_synth", 1500)
    clock.t = 1006.5  # flush：vad 后 6.5s，pipeline 后 3.5s
    latency_flush(1, "segV")

    rec = _summaries(summary_handler)[0]
    assert rec.hop_sum_ms == 6400  # 400+1500+1100+1800+100+1500
    assert rec.total_from_pipeline_ms == 3500
    assert rec.total_from_vad_ms == 6500
    # delta_pct 对 pipeline 段失真变负（hop_sum 含 t0 之前）— 符合预期，保留兼容
    assert rec.delta_pct < 0
    # delta_vad_pct 才是本批度量：(6500-6400)/6500 ≈ 0.0154，整链覆盖误差 < 10%
    assert rec.delta_vad_pct == 0.0154
    assert abs(rec.delta_vad_pct) < 0.10


def test_delta_vad_pct_none_without_vad_anchor(clock, summary_handler):
    """batch-29-T8: 无 vad_start 锚点（voice_chat 直连未走 vad）时 delta_vad_pct 为 None，不报错。"""
    latency_start(1, "segN")
    latency_record(1, "segN", "llm_total", 2000)
    clock.advance(3.0)
    latency_flush(1, "segN")

    rec = _summaries(summary_handler)[0]
    assert rec.delta_vad_pct is None
    assert rec.total_from_vad_ms is None
    assert rec.delta_pct is not None  # pipeline 段口径仍有效
