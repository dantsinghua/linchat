"""ContextMonitor 单元测试

覆盖: 三级阈值判断、边界值、防除零、全零场景、
      build_monitor_data 字段完整性、事件推送 mock
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.context.monitoring import AlertLevel, ContextMonitor
from apps.context.types import TokenBreakdown


class TestAlertLevel:
    """AlertLevel 枚举测试"""

    def test_values(self):
        assert AlertLevel.NORMAL.value == "normal"
        assert AlertLevel.WARNING.value == "warning"
        assert AlertLevel.CRITICAL.value == "critical"


class TestTokenBreakdown:
    """TokenBreakdown dataclass 测试"""

    def test_default_values(self):
        bd = TokenBreakdown()
        assert bd.total == 0
        assert bd.system_prompt == 0
        assert bd.tool_call_count == 0

    def test_total_calculation(self):
        bd = TokenBreakdown(
            system_prompt=100, history_messages=200,
            retrieved_memories=50, compaction_summary=30,
            tool_definitions=80, user_input=40,
            tool_calls=10, tool_results=20,
        )
        assert bd.total == 530

    def test_total_excludes_tool_call_count(self):
        bd = TokenBreakdown(system_prompt=100, tool_call_count=5)
        assert bd.total == 100

    def test_usage_ratio_normal(self):
        bd = TokenBreakdown(system_prompt=500)
        assert bd.usage_ratio(1000) == pytest.approx(0.5)

    def test_usage_ratio_zero_max(self):
        bd = TokenBreakdown(system_prompt=500)
        assert bd.usage_ratio(0) == 0.0

    def test_usage_ratio_negative_max(self):
        bd = TokenBreakdown(system_prompt=500)
        assert bd.usage_ratio(-100) == 0.0

    def test_to_dict_keys(self):
        bd = TokenBreakdown(system_prompt=100)
        d = bd.to_dict()
        expected_keys = {
            "system_prompt", "history", "memories", "compaction",
            "tool_defs", "user_input", "tool_calls", "tool_results",
            "tool_count", "total",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_aliases(self):
        bd = TokenBreakdown(
            history_messages=200, retrieved_memories=50,
            compaction_summary=30, tool_definitions=80,
            tool_call_count=3,
        )
        d = bd.to_dict()
        assert d["history"] == 200
        assert d["memories"] == 50
        assert d["compaction"] == 30
        assert d["tool_defs"] == 80
        assert d["tool_count"] == 3


class TestContextMonitorEvaluate:
    """ContextMonitor.evaluate() 测试"""

    def test_normal_below_70(self):
        bd = TokenBreakdown(system_prompt=500)
        level, pct = ContextMonitor.evaluate(bd, 1000)
        assert level == AlertLevel.NORMAL
        assert pct == 50.0

    def test_normal_at_zero(self):
        bd = TokenBreakdown()
        level, pct = ContextMonitor.evaluate(bd, 1000)
        assert level == AlertLevel.NORMAL
        assert pct == 0.0

    def test_warning_at_70(self):
        bd = TokenBreakdown(system_prompt=700)
        level, pct = ContextMonitor.evaluate(bd, 1000)
        assert level == AlertLevel.WARNING
        assert pct == 70.0

    def test_warning_at_89(self):
        bd = TokenBreakdown(system_prompt=890)
        level, pct = ContextMonitor.evaluate(bd, 1000)
        assert level == AlertLevel.WARNING
        assert pct == 89.0

    def test_critical_at_90(self):
        bd = TokenBreakdown(system_prompt=900)
        level, pct = ContextMonitor.evaluate(bd, 1000)
        assert level == AlertLevel.CRITICAL
        assert pct == 90.0

    def test_critical_at_100(self):
        bd = TokenBreakdown(system_prompt=1000)
        level, pct = ContextMonitor.evaluate(bd, 1000)
        assert level == AlertLevel.CRITICAL
        assert pct == 100.0

    def test_max_tokens_zero(self):
        bd = TokenBreakdown(system_prompt=500)
        level, pct = ContextMonitor.evaluate(bd, 0)
        assert level == AlertLevel.NORMAL
        assert pct == 0.0

    def test_max_tokens_negative(self):
        bd = TokenBreakdown(system_prompt=500)
        level, pct = ContextMonitor.evaluate(bd, -100)
        assert level == AlertLevel.NORMAL
        assert pct == 0.0

    def test_all_zero_breakdown(self):
        bd = TokenBreakdown()
        level, pct = ContextMonitor.evaluate(bd, 65536)
        assert level == AlertLevel.NORMAL
        assert pct == 0.0


class TestContextMonitorBuildMonitorData:
    """ContextMonitor.build_monitor_data() 测试"""

    def test_basic_fields(self):
        bd = TokenBreakdown(system_prompt=1000, user_input=200)
        data = ContextMonitor.build_monitor_data(
            breakdown=bd, max_tokens=65536,
            model_name="test-model",
            input_tokens=500, output_tokens=300,
        )
        assert data["type"] == "context_status"
        assert data["model_name"] == "test-model"
        assert data["total_tokens"] == 800
        assert data["input_tokens"] == 500
        assert data["output_tokens"] == 300
        assert data["max_context_tokens"] == 65536
        assert data["alert"] == "normal"
        assert "pct" in data
        assert "breakdown" in data
        assert data["memory_types"] == []
        assert data["memory_count"] == 0
        assert data["memory_records"] == []
        assert data["tool_processes"] == []

    def test_breakdown_structure(self):
        bd = TokenBreakdown(system_prompt=100, history_messages=200)
        data = ContextMonitor.build_monitor_data(
            breakdown=bd, max_tokens=65536, model_name="test",
        )
        assert data["breakdown"]["system_prompt"] == 100
        assert data["breakdown"]["history"] == 200
        assert data["breakdown"]["total"] == 300

    @patch("apps.common.tokenizer.count_tokens", return_value=50)
    def test_memory_records_with_tags(self, mock_count):
        mem1 = MagicMock()
        mem1.id = 1
        mem1.content = "记忆内容1"
        mem1.tags = ["个人喜好"]
        mem1.updated_at = MagicMock()
        mem1.updated_at.isoformat.return_value = "2026-02-04T10:00:00"

        mem2 = MagicMock()
        mem2.id = 2
        mem2.content = "记忆内容2"
        mem2.tags = None
        mem2.updated_at = MagicMock()
        mem2.updated_at.isoformat.return_value = "2026-02-04T11:00:00"

        memory_results = [
            {"memory": mem1, "score": 0.9},
            {"memory": mem2, "score": 0.8},
        ]

        bd = TokenBreakdown(system_prompt=100)
        data = ContextMonitor.build_monitor_data(
            breakdown=bd, max_tokens=65536, model_name="test",
            memory_results=memory_results,
        )

        assert data["memory_count"] == 2
        assert len(data["memory_records"]) == 2
        assert data["memory_records"][0]["tag"] == "个人喜好"
        assert data["memory_records"][1]["tag"] == "未分类"
        assert len(data["memory_types"]) == 2

    def test_tool_processes(self):
        bd = TokenBreakdown(system_prompt=100)
        tools = [
            {"name": "web_search", "task": "搜索", "input_tokens": 50, "output_tokens": 200},
        ]
        data = ContextMonitor.build_monitor_data(
            breakdown=bd, max_tokens=65536, model_name="test",
            tool_processes=tools,
        )
        assert len(data["tool_processes"]) == 1
        assert data["tool_processes"][0]["name"] == "web_search"

    def test_warning_alert(self):
        bd = TokenBreakdown(system_prompt=50000)
        data = ContextMonitor.build_monitor_data(
            breakdown=bd, max_tokens=65536, model_name="test",
        )
        assert data["alert"] == "warning"

    def test_critical_alert(self):
        bd = TokenBreakdown(system_prompt=60000)
        data = ContextMonitor.build_monitor_data(
            breakdown=bd, max_tokens=65536, model_name="test",
        )
        assert data["alert"] == "critical"

    @patch("apps.common.tokenizer.count_tokens", return_value=50)
    def test_memory_records_limit_4(self, mock_count):
        mems = []
        for i in range(6):
            m = MagicMock()
            m.id = i
            m.content = f"内容{i}"
            m.tags = ["标签"]
            m.updated_at = MagicMock()
            m.updated_at.isoformat.return_value = "2026-02-04T10:00:00"
            mems.append({"memory": m, "score": 0.9})

        bd = TokenBreakdown(system_prompt=100)
        data = ContextMonitor.build_monitor_data(
            breakdown=bd, max_tokens=65536, model_name="test",
            memory_results=mems,
        )

        assert data["memory_count"] == 6
        assert len(data["memory_records"]) == 4
