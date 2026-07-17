"""InferenceMixin（apps.voice.consumer_inference）接线冒烟单测 — batch-21。

本批仅做 Protocol 接线所需的最小验证（plan 第 4/7 节，完整覆盖留给 batch-25）：
  1. import 冒烟 + 运行时基类未变（__mro__ 含 object，未真正继承 Protocol）
  2. _is_pipeline_busy 三态（无 task / task 未完成 / task 已完成）
  3. _reset_response_state 四字段重置

测试方式：以 InferenceMixin.<method>(host, ...) 的 unbound 姿势调用，host 为
预置共享属性的轻量宿主对象（运行时 InferenceMixin 不真正继承 Protocol，
__bases__ == (object,)），全程无真实 IO。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from apps.voice.consumer_inference import InferenceMixin


# =====================================================================
# import 冒烟 + 运行时基类
# =====================================================================
class TestImportSmoke:
    def test_importable_and_runtime_base_is_object(self):
        """接线后运行时基类仍为 object（else: object 分支），未真正继承 Protocol。"""
        assert InferenceMixin.__bases__ == (object,)
        assert object in InferenceMixin.__mro__


# =====================================================================
# _is_pipeline_busy 三态
# =====================================================================
class TestIsPipelineBusy:
    def test_no_task_returns_false(self):
        """无 pipeline task → 非忙。"""
        c = SimpleNamespace(_pipeline_task=None)
        assert InferenceMixin._is_pipeline_busy(c) is False

    def test_running_task_returns_true(self):
        """task 存在且未完成 → 忙。"""
        task = MagicMock()
        task.done.return_value = False
        c = SimpleNamespace(_pipeline_task=task)
        assert InferenceMixin._is_pipeline_busy(c) is True

    def test_done_task_returns_false(self):
        """task 已完成 → 非忙。"""
        task = MagicMock()
        task.done.return_value = True
        c = SimpleNamespace(_pipeline_task=task)
        assert InferenceMixin._is_pipeline_busy(c) is False


# =====================================================================
# _reset_response_state 四字段重置
# =====================================================================
class TestResetResponseState:
    def test_resets_all_four_fields(self):
        """调用后 4 个响应状态字段被正确重置。"""
        c = SimpleNamespace(
            _current_response_id="resp-1",
            _response_start_time=123.4,
            _accumulated_content="partial",
            _response_cancelled=True,
        )
        InferenceMixin._reset_response_state(c)
        assert c._current_response_id is None
        assert c._response_start_time is None
        assert c._accumulated_content == ""
        assert c._response_cancelled is False
