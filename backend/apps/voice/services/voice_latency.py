"""batch-07: 端到端语音延迟收集器。

在 batch-06 已铺设的 voice stage 锚点（logger.info("voice", extra={"stage": ...})）
之上，按 ``user_id:segment_id`` 聚合各跳耗时，并在 pipeline 结束时输出一条
``stage=latency.summary`` 的 JSON 汇总日志行，为 5s SLO 建立 P50/P95 基线。

设计约束：
- **只加日志**：内部只调用 ``logger.info("voice", extra={...})``，不自造 formatter；
  trace_id 由 ``core.logging_config.TraceIdFilter`` 自动注入，无需塞进 extra。
- **内存安全**：``latency_flush`` 必须 pop；另设 ``_MAX_ENTRIES`` 上限，防 RECORD_ONLY /
  异常路径只 anchor 不 flush 导致泄漏。

双 total 口径（team lead 决策 2）：
- ``total_from_vad_ms``        — vad_speech_start 起算（含 ASR + 聚合静默等待），最贴合 5s SLO
- ``total_from_speech_end_ms`` — vad_speech_end 起算（不含用户说话时长）
- ``total_from_pipeline_ms``   — pipeline 起点起算（hops 覆盖率校验基准）

ambient 聚合模式下 pipeline 使用的 segment_id 可能与上游 ASR 段不一致，vad/speech_end 锚点
与 ASR 跳为**近似归因**（team lead 决策 3）；近似跳字段名带 ``_approx`` 后缀标注。
push-to-talk / voice_chat 直连模式无此问题，归因精确。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 键 = "user_id:segment_id"；值 = {"t0": monotonic|None, "anchors": {name: monotonic}, "hops": {stage: ms}}
_LATENCY: dict[str, dict] = {}

# 上限：防只 anchor 不 flush 的段（RECORD_ONLY / 异常）无限堆积；超限按插入序淘汰最旧。
_MAX_ENTRIES = 256

# 子阶段跳：是某个总跳的一部分，不计入 hop_sum 覆盖率求和（避免重复计数）。
_SUBPHASE_HOPS = {"llm_first_token"}


def _key(user_id: int, segment_id: str) -> str:
    return f"{user_id}:{segment_id}"


def _entry(user_id: int, segment_id: str) -> dict:
    key = _key(user_id, segment_id)
    entry = _LATENCY.get(key)
    if entry is None:
        entry = {"t0": None, "anchors": {}, "hops": {}}
        _LATENCY[key] = entry
        _evict_if_needed()
    return entry


def _evict_if_needed() -> None:
    while len(_LATENCY) > _MAX_ENTRIES:
        _LATENCY.pop(next(iter(_LATENCY)), None)


def latency_anchor(user_id: int, segment_id: str, name: str) -> None:
    """记录一个绝对时间锚点（monotonic），如 vad_start / speech_end。

    惰性建 entry，不覆盖已存在同名锚点（幂等）。
    """
    if not segment_id:
        return
    _entry(user_id, segment_id)["anchors"].setdefault(name, time.monotonic())


def latency_start(user_id: int, segment_id: str) -> None:
    """pipeline 起点：记录 t0（monotonic），幂等（不覆盖已存在的 t0）。

    保留此前可能已存在的 vad/speech_end 锚点；仅清空 hops 防同 key 复用时脏累加。
    """
    if not segment_id:
        return
    entry = _entry(user_id, segment_id)
    if entry["t0"] is not None:
        entry["hops"] = {}
    entry["t0"] = entry["t0"] or time.monotonic()


def latency_record(user_id: int, segment_id: str, hop: str, ms: Optional[int]) -> None:
    """累加一跳耗时（ms 为 None 时跳过）。无对应 entry 时惰性建。"""
    if not segment_id or ms is None:
        return
    _entry(user_id, segment_id)["hops"][hop] = int(ms)


def latency_flush(user_id: int, segment_id: str) -> None:
    """输出一条 stage=latency.summary 的 JSON 汇总行并 pop 释放内存。

    hop_sum = sum(非子阶段 hops)；total_from_pipeline_ms = now - t0；
    delta_pct = (total_from_pipeline_ms - hop_sum) / total_from_pipeline_ms（覆盖率误差）。
    找不到 key 时安全 no-op。
    """
    if not segment_id:
        return
    entry = _LATENCY.pop(_key(user_id, segment_id), None)
    if entry is None:
        return
    now = time.monotonic()
    hops = entry["hops"]
    anchors = entry["anchors"]
    hop_sum = sum(v for k, v in hops.items() if k not in _SUBPHASE_HOPS)

    t0 = entry["t0"]
    vad_t0 = anchors.get("vad_start")
    speech_end_t0 = anchors.get("speech_end")
    total_from_pipeline_ms = int((now - t0) * 1000) if t0 else None
    total_from_vad_ms = int((now - vad_t0) * 1000) if vad_t0 else None
    total_from_speech_end_ms = int((now - speech_end_t0) * 1000) if speech_end_t0 else None
    delta_pct = (
        round((total_from_pipeline_ms - hop_sum) / total_from_pipeline_ms, 4)
        if total_from_pipeline_ms else None
    )
    # batch-29: 三缺跳（speaker_identify/aggregation_wait/decision_llm）补入后 hop_sum 跨越 t0 之前，
    # 使 delta_pct（对 pipeline 段）失真变负。新增 delta_vad_pct 以 total_from_vad_ms 为基准衡量整链覆盖率，
    # 是本批 "hop_sum 与 total_from_vad_ms 误差 < 10%" 的度量字段。delta_pct 保留不变以兼容 batch-07 脚本。
    delta_vad_pct = (
        round((total_from_vad_ms - hop_sum) / total_from_vad_ms, 4)
        if total_from_vad_ms else None
    )
    # batch-09 口径说明：VOICE_TTS_INCREMENTAL_ENABLED 开启时，hops.tts_synth 语义为
    # 「首帧 text.delta 送出 → audio.done」窗口（含与 LLM 推理重叠段），非旧口径「全文送完 → audio.done」；
    # 两口径不可直接同轴比较，收益以 total_from_speech_end_ms P50 衡量。字段名保持不变以兼容 batch-07 脚本。
    logger.info("voice", extra={
        "stage": "latency.summary",
        "user_id": user_id, "seg": segment_id,
        "hops": hops, "hop_sum_ms": hop_sum,
        "total_from_pipeline_ms": total_from_pipeline_ms,
        "total_from_vad_ms": total_from_vad_ms,
        "total_from_speech_end_ms": total_from_speech_end_ms,
        "delta_pct": delta_pct,
        "delta_vad_pct": delta_vad_pct,
    })
