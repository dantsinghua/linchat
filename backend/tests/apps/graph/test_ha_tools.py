"""Home Assistant 工具单元测试

测试 ha_control, ha_query, ha_diagnose 三个工具。
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from apps.graph.tools.homeassistant import (
    ha_control,
    ha_query,
    ha_diagnose,
    _check_rate_limit,
    _is_blocked,
    _is_sensitive,
    ACTION_MAP,
    HA_TOOLS,
)


@pytest.fixture
def mock_config():
    """模拟 RunnableConfig"""
    return {"configurable": {"user_id": 123}}


@pytest.fixture
def mock_ha_client():
    """模拟 HAClient"""
    with patch("apps.graph.tools.homeassistant.HAClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_rate_limit():
    """模拟速率限制通过"""
    with patch(
        "apps.graph.tools.homeassistant._check_rate_limit",
        new_callable=AsyncMock,
        return_value=None
    ) as mock:
        yield mock


class TestRateLimit:
    """测试速率限制辅助函数"""

    @pytest.mark.asyncio
    async def test_rate_limit_pass(self):
        """测试速率限制通过"""
        with patch("apps.graph.tools.ha_helpers.aioredis") as mock_redis:
            mock_r = AsyncMock()
            mock_r.incr.return_value = 1
            mock_redis.from_url.return_value = mock_r

            result = await _check_rate_limit(123, "control")

            assert result is None
            mock_r.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self):
        """测试速率限制超出"""
        with patch("apps.graph.tools.ha_helpers.aioredis") as mock_redis:
            mock_r = AsyncMock()
            mock_r.incr.return_value = 15  # 超过 control 的 10/min 限制
            mock_redis.from_url.return_value = mock_r

            result = await _check_rate_limit(123, "control")

            assert result is not None
            assert "过于频繁" in result


class TestBlacklist:
    """测试黑名单检查"""

    def test_blocked_entity(self):
        """测试被屏蔽的设备"""
        with patch("apps.graph.tools.ha_helpers.settings") as mock_settings:
            mock_settings.HA_BLOCKED_ENTITIES = ["lock.front_door", "switch.danger"]

            assert _is_blocked("lock.front_door") is True
            assert _is_blocked("light.living_room") is False


class TestSensitiveOperations:
    """测试敏感操作检测"""

    def test_unlock_is_sensitive(self):
        """测试解锁操作为 L3 敏感"""
        is_sens, msg = _is_sensitive("unlock", "lock.front_door")
        assert is_sens is True
        assert "解锁" in msg
        assert "确认" in msg

    def test_garage_door_is_sensitive(self):
        """测试开车库门为 L3 敏感"""
        is_sens, msg = _is_sensitive("open_cover", "cover.garage_left")
        assert is_sens is True
        assert "车库门" in msg

    def test_non_garage_cover_not_sensitive(self):
        """测试普通窗帘不是敏感操作"""
        is_sens, msg = _is_sensitive("open_cover", "cover.bedroom_curtain")
        assert is_sens is False
        assert msg is None

    def test_disable_automation_is_l4(self):
        """测试禁用自动化为 L4 危险"""
        is_sens, msg = _is_sensitive("turn_off", "automation.night_mode")
        assert is_sens is True
        assert "禁用自动化" in msg
        assert "危险" in msg

    def test_turn_off_light_not_sensitive(self):
        """测试关灯不是敏感操作"""
        is_sens, msg = _is_sensitive("turn_off", "light.living_room")
        assert is_sens is False


class TestHaControl:
    """测试 ha_control 工具"""

    @pytest.mark.asyncio
    async def test_control_turn_on_success(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试正常开灯"""
        mock_ha_client.call_service.return_value = [
            {
                "entity_id": "light.living_room",
                "state": "on",
                "attributes": {"friendly_name": "客厅灯", "brightness": 255},
            }
        ]

        # 直接调用工具函数，传入正确的参数
        result = await ha_control.ainvoke(
            input={
                "entity_id": "light.living_room",
                "action": "turn_on",
            },
            config=mock_config,
        )

        assert "✅" in result
        assert "开启" in result
        assert "客厅灯" in result

    @pytest.mark.asyncio
    async def test_control_set_brightness(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试调节亮度"""
        mock_ha_client.call_service.return_value = [
            {
                "entity_id": "light.living_room",
                "state": "on",
                "attributes": {"friendly_name": "客厅灯", "brightness": 128},
            }
        ]

        result = await ha_control.ainvoke(
            input={
                "entity_id": "light.living_room",
                "action": "set_brightness",
                "params": {"brightness": 128},
            },
            config=mock_config,
        )

        assert "✅" in result
        assert "亮度" in result

    @pytest.mark.asyncio
    async def test_control_blocked_device(self, mock_config, mock_rate_limit):
        """测试黑名单设备被拒绝"""
        with patch("apps.graph.tools.homeassistant._is_blocked", return_value=True):
            result = await ha_control.ainvoke(
                input={
                    "entity_id": "lock.front_door",
                    "action": "unlock",
                },
                config=mock_config,
            )

            assert "禁止" in result

    @pytest.mark.asyncio
    async def test_control_sensitive_returns_confirmation(
        self, mock_config, mock_rate_limit
    ):
        """测试敏感操作返回确认提示"""
        with patch("apps.graph.tools.homeassistant._is_blocked", return_value=False):
            result = await ha_control.ainvoke(
                input={
                    "entity_id": "lock.front_door",
                    "action": "unlock",
                },
                config=mock_config,
            )

            assert "⚠️" in result
            assert "确认" in result

    @pytest.mark.asyncio
    async def test_control_l4_automation_disable(self, mock_config, mock_rate_limit):
        """测试 L4 禁用自动化返回确认提示"""
        with patch("apps.graph.tools.homeassistant._is_blocked", return_value=False):
            result = await ha_control.ainvoke(
                input={
                    "entity_id": "automation.security_alarm",
                    "action": "turn_off",
                },
                config=mock_config,
            )

            assert "⚠️" in result
            assert "禁用自动化" in result
            assert "危险" in result

    @pytest.mark.asyncio
    async def test_control_unknown_action(self, mock_config, mock_rate_limit):
        """测试未知 action 返回错误"""
        result = await ha_control.ainvoke(
            input={
                "entity_id": "light.test",
                "action": "unknown_action",
            },
            config=mock_config,
        )

        assert "不支持" in result
        assert "unknown_action" in result

    @pytest.mark.asyncio
    async def test_control_connection_error(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试 HA 连接失败返回友好提示"""
        from apps.graph.tools.ha_client import HAConnectionError

        mock_ha_client.call_service.side_effect = HAConnectionError("连接超时")

        result = await ha_control.ainvoke(
            input={
                "entity_id": "light.test",
                "action": "turn_on",
            },
            config=mock_config,
        )

        assert "不可达" in result

    @pytest.mark.asyncio
    async def test_control_rate_limit_exceeded(self, mock_config):
        """测试速率限制触发友好提示"""
        with patch(
            "apps.graph.tools.homeassistant._check_rate_limit",
            new_callable=AsyncMock,
            return_value="操作过于频繁",
        ):
            result = await ha_control.ainvoke(
                input={
                    "entity_id": "light.test",
                    "action": "turn_on",
                },
                config=mock_config,
            )

            assert "频繁" in result


class TestHaQuery:
    """测试 ha_query 工具"""

    @pytest.mark.asyncio
    async def test_query_state_success(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试查询单设备状态"""
        mock_ha_client.get_state.return_value = {
            "entity_id": "light.living_room",
            "state": "on",
            "attributes": {"friendly_name": "客厅灯", "brightness": 178},
            "last_changed": "2026-02-05T10:30:15+00:00",
        }

        result = await ha_query.ainvoke(
            input={
                "query_type": "state",
                "entity_id": "light.living_room",
            },
            config=mock_config,
        )

        assert "客厅灯" in result
        assert "on" in result
        assert "178" in result or "70%" in result

    @pytest.mark.asyncio
    async def test_query_list_with_truncation(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试设备列表超 20 个截断"""
        # 生成 25 个设备
        devices = [
            {
                "entity_id": f"light.room_{i}",
                "state": "on" if i % 2 == 0 else "off",
                "attributes": {"friendly_name": f"灯{i}"},
            }
            for i in range(25)
        ]
        mock_ha_client.get_states.return_value = devices

        result = await ha_query.ainvoke(
            input={
                "query_type": "list",
                "domain": "light",
            },
            config=mock_config,
        )

        assert "及其他" in result
        assert "5 个" in result  # 25 - 20 = 5

    @pytest.mark.asyncio
    async def test_query_history_success(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试查询历史记录"""
        mock_ha_client.get_history.return_value = [
            [
                {"state": "on", "last_changed": "2026-02-05T08:00:00+00:00"},
                {"state": "off", "last_changed": "2026-02-05T10:00:00+00:00"},
            ]
        ]

        result = await ha_query.ainvoke(
            input={
                "query_type": "history",
                "entity_id": "light.living_room",
            },
            config=mock_config,
        )

        assert "历史记录" in result
        assert "on" in result
        assert "off" in result

    @pytest.mark.asyncio
    async def test_query_not_found(self, mock_config, mock_ha_client, mock_rate_limit):
        """测试设备不存在返回友好提示"""
        from apps.graph.tools.ha_client import HANotFoundError

        mock_ha_client.get_state.side_effect = HANotFoundError("not found")

        result = await ha_query.ainvoke(
            input={
                "query_type": "state",
                "entity_id": "light.not_exist",
            },
            config=mock_config,
        )

        assert "未找到" in result


class TestHaDiagnose:
    """测试 ha_diagnose 工具"""

    @pytest.mark.asyncio
    async def test_diagnose_health_success(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试系统健康检查"""
        mock_ha_client.check_health.return_value = True
        mock_ha_client.get_config.return_value = {
            "version": "2024.12.1",
            "components": ["light", "switch", "climate"],
        }

        result = await ha_diagnose.ainvoke(
            input={
                "diagnose_type": "health",
            },
            config=mock_config,
        )

        assert "2024.12.1" in result
        assert "正常" in result

    @pytest.mark.asyncio
    async def test_diagnose_device_unavailable(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试 unavailable 设备诊断建议"""
        mock_ha_client.get_state.return_value = {
            "entity_id": "climate.ac",
            "state": "unavailable",
            "attributes": {"friendly_name": "客厅空调"},
            "last_changed": "2026-02-05T09:00:00+00:00",
        }

        result = await ha_diagnose.ainvoke(
            input={
                "diagnose_type": "device",
                "entity_id": "climate.ac",
            },
            config=mock_config,
        )

        assert "unavailable" in result
        assert "可能原因" in result
        assert "建议操作" in result

    @pytest.mark.asyncio
    async def test_diagnose_offline_scan(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试离线设备扫描"""
        mock_ha_client.get_states.return_value = [
            {"entity_id": "light.a", "state": "on", "attributes": {}},
            {"entity_id": "light.b", "state": "unavailable", "attributes": {}},
            {"entity_id": "switch.c", "state": "unknown", "attributes": {}},
        ]

        result = await ha_diagnose.ainvoke(
            input={
                "diagnose_type": "offline_scan",
            },
            config=mock_config,
        )

        assert "2 个离线" in result
        assert "light.b" in result
        assert "switch.c" in result

    @pytest.mark.asyncio
    async def test_diagnose_automations(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试自动化规则检查返回启用/禁用统计"""
        mock_ha_client.get_states.return_value = [
            {
                "entity_id": "automation.night_mode",
                "state": "on",
                "attributes": {"friendly_name": "夜间模式", "last_triggered": "2026-02-05T22:00:00"},
            },
            {
                "entity_id": "automation.away_mode",
                "state": "off",
                "attributes": {"friendly_name": "离家模式"},
            },
        ]

        result = await ha_diagnose.ainvoke(
            input={
                "diagnose_type": "automations",
            },
            config=mock_config,
        )

        assert "2 个规则" in result
        assert "1 个启用" in result
        assert "1 个禁用" in result
        assert "离家模式" in result

    @pytest.mark.asyncio
    async def test_diagnose_error_log_truncation(
        self, mock_config, mock_ha_client, mock_rate_limit
    ):
        """测试错误日志截断"""
        long_log = "ERROR: " + "x" * 3000  # 超过 2000 字符
        mock_ha_client.get_error_log.return_value = long_log

        result = await ha_diagnose.ainvoke(
            input={
                "diagnose_type": "error_log",
            },
            config=mock_config,
        )

        assert "截断" in result


class TestHAToolsExport:
    """测试工具导出"""

    def test_ha_tools_contains_all_tools(self):
        """测试 HA_TOOLS 包含所有工具"""
        tool_names = [t.name for t in HA_TOOLS]
        assert "ha_query" in tool_names
        assert "ha_control" in tool_names
        assert "ha_diagnose" in tool_names

    def test_action_map_has_18_actions(self):
        """测试 ACTION_MAP 包含 18 个 action"""
        assert len(ACTION_MAP) == 18
