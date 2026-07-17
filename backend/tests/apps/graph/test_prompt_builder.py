"""build_prompt_preamble 并行化测试（batch-12）。

覆盖：
- 三步 IO（取激活模型 / 记忆召回 / 取历史）并行执行（asyncio.gather）
- 记忆召回失败 → 降级为空、不抛出（保留原 try/except 语义）
- model_config / history 失败 → 异常传播（保留原无 try/except 语义）
- 返回 7 元组结构不变

Mock 策略：patch prompt.py 内部延迟导入的三个 IO 依赖 + PromptBuilder，
不触碰 DB，纯验证并发与异常语义。
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.graph.services.helpers.prompt import build_prompt_preamble

_PROMPT_MOD = "apps.graph.services.helpers.prompt"
_MODEL_CONFIG = {"max_context_window": 128000, "name": "kimi-k2.5"}


def _patch_builder():
    """把 PromptBuilder 替换为轻量 mock，隔离真实系统提示词/DB 渲染。"""
    builder = MagicMock()
    builder.build_preamble.return_value = []
    breakdown = MagicMock(total=10, user_input=2)
    builder.build_preamble_with_breakdown.return_value = ([], breakdown)
    return patch(f"{_PROMPT_MOD}.PromptBuilder", return_value=builder)


def _patch_io(model_config=_MODEL_CONFIG, memory=None, history=None,
              model_exc=None, memory_exc=None, history_exc=None,
              sleep=0.0):
    """统一 patch 三步 IO。sleep>0 时各步阻塞/等待 sleep 秒，用于并发计时。"""
    def _get_active_model(_type):
        if sleep:
            time.sleep(sleep)
        if model_exc:
            raise model_exc
        return model_config

    async def _search_memory(**_kwargs):
        if sleep:
            import asyncio
            await asyncio.sleep(sleep)
        if memory_exc:
            raise memory_exc
        return memory if memory is not None else []

    async def _find_latest(*_args, **_kwargs):
        if sleep:
            import asyncio
            await asyncio.sleep(sleep)
        if history_exc:
            raise history_exc
        return list(history) if history else []

    return (
        patch("apps.models.services.model_service.get_active_model",
              side_effect=_get_active_model),
        patch("apps.memory.services.MemoryService.search_memory",
              new=AsyncMock(side_effect=_search_memory)),
        patch("apps.chat.repositories.message_repo.find_latest_by_user",
              new=AsyncMock(side_effect=_find_latest)),
    )


@pytest.mark.asyncio
async def test_preamble_runs_three_io_in_parallel():
    """三步各耗 50ms 的 IO 并行执行，总耗时应远小于串行 150ms。"""
    p_model, p_mem, p_hist = _patch_io(sleep=0.05)
    with _patch_builder(), p_model, p_mem, p_hist:
        t0 = time.monotonic()
        await build_prompt_preamble(user_id=1, user_message="你好")
        elapsed = time.monotonic() - t0
    assert elapsed < 0.12, f"并行应 <120ms，实际 {elapsed*1000:.0f}ms（疑似串行）"


@pytest.mark.asyncio
async def test_preamble_memory_failure_degrades():
    """记忆召回抛异常 → 不抛出、memory_results 降级为空，其余正常。"""
    p_model, p_mem, p_hist = _patch_io(memory_exc=RuntimeError("es down"))
    with _patch_builder(), p_model, p_mem, p_hist:
        result = await build_prompt_preamble(user_id=1, user_message="你好")
    assert len(result) == 7
    memory_results = result[4]
    assert memory_results == []


@pytest.mark.asyncio
async def test_preamble_model_config_failure_propagates():
    """get_active_model 抛异常 → 传播（保留原无 try/except 语义）。"""
    p_model, p_mem, p_hist = _patch_io(model_exc=ValueError("model boom"))
    with _patch_builder(), p_model, p_mem, p_hist:
        with pytest.raises(ValueError, match="model boom"):
            await build_prompt_preamble(user_id=1, user_message="你好")


@pytest.mark.asyncio
async def test_preamble_history_failure_propagates():
    """find_latest_by_user 抛异常 → 传播（保留原无 try/except 语义）。"""
    p_model, p_mem, p_hist = _patch_io(history_exc=RuntimeError("db boom"))
    with _patch_builder(), p_model, p_mem, p_hist:
        with pytest.raises(RuntimeError, match="db boom"):
            await build_prompt_preamble(user_id=1, user_message="你好")


@pytest.mark.asyncio
async def test_preamble_returns_seven_values():
    """返回 7 元组结构不变。"""
    p_model, p_mem, p_hist = _patch_io()
    with _patch_builder(), p_model, p_mem, p_hist:
        result = await build_prompt_preamble(user_id=1, user_message="你好")
    assert isinstance(result, tuple)
    assert len(result) == 7
    # 第 6 项 model_name 来自 model_config["name"]
    assert result[5] == "kimi-k2.5"


# ============ C2 老公人设注入（方案A）============
# 注意：以下两例刻意**不** patch PromptBuilder（用真实 builder + 真实 j2 渲染），
# 以验证 settings.WECHAT_PERSONA_INSTRUCTION 是否真正流入 system prompt。


@pytest.mark.asyncio
async def test_preamble_wechat_channel_injects_persona():
    """channel=wechat → 主 prompt 系统段含老公人设关键词。"""
    p_model, p_mem, p_hist = _patch_io()
    with p_model, p_mem, p_hist:
        preamble, *_ = await build_prompt_preamble(
            user_id=1, user_message="你好", channel="wechat")
    sys_text = preamble[0].content
    assert "老公" in sys_text
    assert "装修" in sys_text


@pytest.mark.asyncio
async def test_preamble_web_channel_no_persona():
    """channel=web（默认）→ 不注入人设，防污染 Web/语音。"""
    p_model, p_mem, p_hist = _patch_io()
    with p_model, p_mem, p_hist:
        preamble_web, *_ = await build_prompt_preamble(
            user_id=1, user_message="你好", channel="web")
    sys_web = preamble_web[0].content
    assert "做老婆最坚实的情绪后盾" not in sys_web
