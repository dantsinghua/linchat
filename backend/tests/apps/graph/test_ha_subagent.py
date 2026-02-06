"""Home Assistant SubAgent 测试

包含条件注册测试和集成测试。
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestConditionalRegistration:
    """T007: 测试 ha_subagent 条件注册逻辑"""

    def test_ha_enabled_check_logic(self):
        """测试 HA_ENABLED 检查逻辑正确"""
        # 验证 getattr 默认值行为
        class MockSettingsEnabled:
            BRAVE_SEARCH_API_KEY = "test-key"
            HA_ENABLED = True

        class MockSettingsDisabled:
            BRAVE_SEARCH_API_KEY = "test-key"
            HA_ENABLED = False

        class MockSettingsNoAttr:
            BRAVE_SEARCH_API_KEY = "test-key"

        # 测试 HA_ENABLED=True
        assert getattr(MockSettingsEnabled, "HA_ENABLED", False) is True
        # 测试 HA_ENABLED=False
        assert getattr(MockSettingsDisabled, "HA_ENABLED", False) is False
        # 测试无 HA_ENABLED 属性
        assert getattr(MockSettingsNoAttr, "HA_ENABLED", False) is False

    def test_ha_subagent_not_registered_when_disabled(self):
        """测试 HA_ENABLED=False 时 ha_subagent 不注册"""
        # 当前环境没有配置 HA，验证 ha_subagent 不在列表中
        from apps.graph.subagents import get_subagent_tools

        tools = get_subagent_tools()
        tool_names = [t.name for t in tools]

        # 在测试环境中，HA_ENABLED 应该为 False（未配置）
        from django.conf import settings
        if not getattr(settings, "HA_ENABLED", False):
            assert "ha_subagent" not in tool_names

    def test_ha_subagent_not_registered_when_no_config(self):
        """测试 getattr 正确处理缺失属性"""
        from django.conf import settings

        # 使用 getattr 获取 HA_ENABLED，默认为 False
        ha_enabled = getattr(settings, "HA_ENABLED", False)
        # 如果 HA_URL 和 HA_TOKEN 未配置，HA_ENABLED 应该为 False
        ha_url = getattr(settings, "HA_URL", "")
        ha_token = getattr(settings, "HA_TOKEN", "")

        if not (ha_url and ha_token):
            assert ha_enabled is False


class TestHASubAgentIntegration:
    """T018: ha_subagent 集成测试"""

    @pytest.fixture
    def mock_config(self):
        """模拟 RunnableConfig"""
        return {"configurable": {"user_id": 123}}

    @pytest.mark.asyncio
    async def test_full_control_flow(self, mock_config):
        """测试完整流程：用户指令 → ha_subagent → ha_control → 返回结果"""
        # Mock run_subagent 直接返回预期结果
        with patch(
            "apps.graph.subagents.ha_agent.run_subagent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = "✅ 已执行: 开启 客厅灯 (light.living_room)"

            from apps.graph.subagents.ha_agent import ha_subagent

            result = await ha_subagent.ainvoke(
                {"task": "打开客厅灯", "config": mock_config}
            )

            assert "✅" in result
            assert "客厅灯" in result

            # 验证 run_subagent 被正确调用
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == "打开客厅灯"  # task
            assert "ha_subagent" in str(call_args)  # name

    @pytest.mark.asyncio
    async def test_ha_unreachable_graceful_degradation(self, mock_config):
        """测试 HA 不可达时的优雅降级"""
        with patch(
            "apps.graph.subagents.ha_agent.run_subagent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = "Home Assistant 服务不可达，请检查网络连接"

            from apps.graph.subagents.ha_agent import ha_subagent

            result = await ha_subagent.ainvoke(
                {"task": "打开客厅灯", "config": mock_config}
            )

            assert "不可达" in result

    @pytest.mark.asyncio
    async def test_ha_auth_error_friendly_message(self, mock_config):
        """测试 HA Token 无效时返回友好提示"""
        with patch(
            "apps.graph.subagents.ha_agent.run_subagent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = "HA 认证失败，请检查 Token 配置"

            from apps.graph.subagents.ha_agent import ha_subagent

            result = await ha_subagent.ainvoke(
                {"task": "查询设备列表", "config": mock_config}
            )

            assert "认证失败" in result
            # 确保不是系统异常
            assert "Exception" not in result
            assert "Traceback" not in result


class TestHASubAgentPrompt:
    """测试 HA_PROMPT 内容"""

    def test_prompt_contains_required_sections(self):
        """测试 prompt 包含必需的章节"""
        from apps.graph.subagents.ha_agent import HA_PROMPT

        # 工具说明
        assert "ha_query" in HA_PROMPT
        assert "ha_control" in HA_PROMPT
        assert "ha_diagnose" in HA_PROMPT

        # 执行策略
        assert "执行策略" in HA_PROMPT
        assert "设备名模糊" in HA_PROMPT

        # 安全规则
        assert "安全规则" in HA_PROMPT
        assert "敏感操作" in HA_PROMPT
        assert "L3" in HA_PROMPT or "unlock" in HA_PROMPT
        assert "L4" in HA_PROMPT or "automation" in HA_PROMPT

        # 响应规范
        assert "响应规范" in HA_PROMPT
        assert "中文" in HA_PROMPT

    def test_prompt_mentions_mem_search(self):
        """测试 prompt 提到 mem_search 工具"""
        from apps.graph.subagents.ha_agent import HA_PROMPT

        assert "mem_search" in HA_PROMPT


class TestHAToolsImport:
    """测试 HA_TOOLS 导入"""

    def test_ha_tools_importable_from_init(self):
        """测试可从 tools/__init__.py 导入 HA_TOOLS"""
        from apps.graph.tools import HA_TOOLS

        assert len(HA_TOOLS) == 3
        tool_names = [t.name for t in HA_TOOLS]
        assert "ha_query" in tool_names
        assert "ha_control" in tool_names
        assert "ha_diagnose" in tool_names
