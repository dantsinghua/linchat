"""
celery Task trace_id 透传测试 (batch-28)

复用 batch-04 的 trace_id_var，验证三个 celery signal handler：
- T1: before_task_publish → 把当前 trace_id_var 写入 headers dict
- T2: trace_id_var 为空 → 不污染 headers（不写空串键）
- T3: task_prerun → 从 task.request.trace_id 恢复到 trace_id_var
- T4: task_prerun 无 request.trace_id（beat 场景）→ 生成 32 字符 hex
- T5: task_postrun → reset contextvar，任务结束不残留
- T6: chain 继承 → 父 prerun set 后，子 before_task_publish 读到父 trace_id
- T7: ensure_trace_id() 空→生成 hex / 非空→原值返回
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from apps.common import ensure_trace_id, trace_id_var
from core.celery import (
    _clear_trace_id,
    _inject_trace_id,
    _restore_trace_id,
    _trace_tokens,
)


UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture(autouse=True)
def _reset_trace_id_var():
    """每个 test 开头置空 contextvar 并清理 token 表，避免相互污染。"""
    token = trace_id_var.set("")
    _trace_tokens.clear()
    yield
    trace_id_var.reset(token)
    _trace_tokens.clear()


# ============ before_task_publish ============


def test_t1_publish_injects_trace_id_into_headers():
    """T1: 当前 trace_id_var 有值 → 写入 headers dict。"""
    trace_id_var.set("tid-parent-001")
    headers: dict = {}
    _inject_trace_id(headers=headers)
    assert headers["trace_id"] == "tid-parent-001"


def test_t2_empty_trace_id_does_not_pollute_headers():
    """T2: trace_id_var 为空 → 不写入 headers（无空串键）。"""
    headers: dict = {}
    _inject_trace_id(headers=headers)
    assert "trace_id" not in headers


def test_t2b_none_headers_no_crash():
    """T2b: headers=None（非 protocol v2）→ 静默不抛。"""
    trace_id_var.set("tid-x")
    _inject_trace_id(headers=None)  # 不应抛异常


# ============ task_prerun ============


def test_t3_prerun_restores_trace_id_from_request():
    """T3: task.request.trace_id → 恢复到 trace_id_var。"""
    task = SimpleNamespace(request=SimpleNamespace(trace_id="tid-restored-42"))
    _restore_trace_id(task_id="task-1", task=task)
    assert trace_id_var.get() == "tid-restored-42"
    assert "task-1" in _trace_tokens


def test_t4_prerun_generates_hex_for_beat():
    """T4: 无 request.trace_id（beat 场景）→ 生成 32 字符 hex。"""
    task = SimpleNamespace(request=SimpleNamespace(trace_id=None))
    _restore_trace_id(task_id="task-beat", task=task)
    tid = trace_id_var.get()
    assert UUID_HEX_RE.match(tid), f"Expected 32-char hex, got: {tid!r}"


# ============ task_postrun ============


def test_t5_postrun_resets_trace_id():
    """T5: postrun 后 trace_id_var 恢复到 prerun 之前的值（不残留）。"""
    task = SimpleNamespace(request=SimpleNamespace(trace_id="tid-transient"))
    _restore_trace_id(task_id="task-2", task=task)
    assert trace_id_var.get() == "tid-transient"

    _clear_trace_id(task_id="task-2")
    # reset 回 prerun 之前（fixture 设置的空串）
    assert trace_id_var.get() == ""
    assert "task-2" not in _trace_tokens


def test_t5b_postrun_unknown_task_id_no_crash():
    """T5b: postrun 收到未记录的 task_id → 静默不抛。"""
    _clear_trace_id(task_id="never-seen")  # 不应抛异常


# ============ chain 继承 ============


def test_t6_chain_inheritance():
    """T6: 父 prerun set trace_id 后，子 before_task_publish 读到父值。"""
    parent = SimpleNamespace(request=SimpleNamespace(trace_id="tid-chain-root"))
    _restore_trace_id(task_id="parent", task=parent)

    # 父任务体内发布子任务 → before_task_publish 读当前 contextvar
    child_headers: dict = {}
    _inject_trace_id(headers=child_headers)
    assert child_headers["trace_id"] == "tid-chain-root"


# ============ ensure_trace_id ============


def test_t7_ensure_trace_id():
    """T7: 空 → 生成 hex 并 set；非空 → 原值返回。"""
    # 空 → 生成
    generated = ensure_trace_id()
    assert UUID_HEX_RE.match(generated)
    assert trace_id_var.get() == generated

    # 非空 → 原值
    trace_id_var.set("tid-existing")
    assert ensure_trace_id() == "tid-existing"
