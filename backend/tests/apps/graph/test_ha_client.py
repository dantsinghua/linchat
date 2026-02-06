"""HAClient 单元测试

使用 pytest-httpx 或 respx mock HTTP 请求。
测试所有 7 个方法的正常响应和 4 种错误场景。
"""

import pytest
import httpx
from unittest.mock import patch, AsyncMock, MagicMock

from apps.graph.tools.ha_client import (
    HAClient,
    HAError,
    HAAuthError,
    HANotFoundError,
    HAConnectionError,
)


@pytest.fixture
def ha_client():
    """创建 HAClient 实例（使用 mock settings）"""
    with patch("apps.graph.tools.ha_client.settings") as mock_settings:
        mock_settings.HA_URL = "http://ha.local:8123"
        mock_settings.HA_TOKEN = "test-token"
        mock_settings.HA_REQUEST_TIMEOUT = 10
        yield HAClient()


class TestHAClientGetState:
    """测试 get_state 方法"""

    @pytest.mark.asyncio
    async def test_get_state_success(self, ha_client):
        """测试正常获取单设备状态"""
        mock_response = {
            "entity_id": "light.living_room",
            "state": "on",
            "attributes": {"friendly_name": "客厅主灯", "brightness": 178},
            "last_changed": "2026-02-05T10:30:15+00:00",
        }

        with patch.object(
            ha_client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_request.return_value = mock_resp

            result = await ha_client.get_state("light.living_room")

            assert result["entity_id"] == "light.living_room"
            assert result["state"] == "on"
            assert result["attributes"]["brightness"] == 178
            mock_request.assert_called_once_with(
                "GET", "/api/states/light.living_room"
            )


class TestHAClientGetStates:
    """测试 get_states 方法"""

    @pytest.mark.asyncio
    async def test_get_states_all(self, ha_client):
        """测试获取所有设备状态"""
        mock_response = [
            {"entity_id": "light.living_room", "state": "on"},
            {"entity_id": "switch.bedroom", "state": "off"},
            {"entity_id": "climate.ac", "state": "cool"},
        ]

        with patch.object(
            ha_client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_request.return_value = mock_resp

            result = await ha_client.get_states()

            assert len(result) == 3
            mock_request.assert_called_once_with("GET", "/api/states")

    @pytest.mark.asyncio
    async def test_get_states_filtered_by_domain(self, ha_client):
        """测试按域过滤设备"""
        mock_response = [
            {"entity_id": "light.living_room", "state": "on"},
            {"entity_id": "switch.bedroom", "state": "off"},
            {"entity_id": "light.kitchen", "state": "off"},
        ]

        with patch.object(
            ha_client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_request.return_value = mock_resp

            result = await ha_client.get_states(domain="light")

            assert len(result) == 2
            assert all(r["entity_id"].startswith("light.") for r in result)


class TestHAClientCallService:
    """测试 call_service 方法"""

    @pytest.mark.asyncio
    async def test_call_service_success(self, ha_client):
        """测试正常调用服务"""
        mock_response = [
            {"entity_id": "light.living_room", "state": "on"}
        ]

        with patch.object(
            ha_client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_request.return_value = mock_resp

            result = await ha_client.call_service(
                "homeassistant", "turn_on", {"entity_id": "light.living_room"}
            )

            assert len(result) == 1
            assert result[0]["state"] == "on"
            mock_request.assert_called_once_with(
                "POST",
                "/api/services/homeassistant/turn_on",
                json={"entity_id": "light.living_room"},
            )


class TestHAClientGetHistory:
    """测试 get_history 方法"""

    @pytest.mark.asyncio
    async def test_get_history_success(self, ha_client):
        """测试获取历史记录"""
        mock_response = [
            [
                {"state": "on", "last_changed": "2026-02-05T08:00:00+00:00"},
                {"state": "off", "last_changed": "2026-02-05T10:00:00+00:00"},
            ]
        ]

        with patch.object(
            ha_client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_request.return_value = mock_resp

            result = await ha_client.get_history("light.living_room", hours=24)

            assert len(result) == 1
            assert len(result[0]) == 2


class TestHAClientGetErrorLog:
    """测试 get_error_log 方法"""

    @pytest.mark.asyncio
    async def test_get_error_log_success(self, ha_client):
        """测试获取错误日志"""
        mock_log = "2026-02-05 10:00:00 ERROR: Something went wrong\n"

        with patch.object(
            ha_client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_resp = MagicMock()
            mock_resp.text = mock_log
            mock_request.return_value = mock_resp

            result = await ha_client.get_error_log()

            assert "ERROR" in result
            mock_request.assert_called_once_with("GET", "/api/error_log")


class TestHAClientGetConfig:
    """测试 get_config 方法"""

    @pytest.mark.asyncio
    async def test_get_config_success(self, ha_client):
        """测试获取系统配置"""
        mock_response = {
            "version": "2024.12.1",
            "components": ["light", "switch", "climate"],
            "unit_system": {"temperature": "°C"},
        }

        with patch.object(
            ha_client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_request.return_value = mock_resp

            result = await ha_client.get_config()

            assert result["version"] == "2024.12.1"
            assert "light" in result["components"]


class TestHAClientCheckHealth:
    """测试 check_health 方法"""

    @pytest.mark.asyncio
    async def test_check_health_success(self, ha_client):
        """测试健康检查成功"""
        with patch.object(
            ha_client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_request.return_value = mock_resp

            result = await ha_client.check_health()

            assert result is True

    @pytest.mark.asyncio
    async def test_check_health_failure(self, ha_client):
        """测试健康检查失败"""
        with patch.object(
            ha_client, "_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.side_effect = HAConnectionError("连接失败")

            result = await ha_client.check_health()

            assert result is False


class TestHAClientErrors:
    """测试错误场景"""

    @pytest.mark.asyncio
    async def test_auth_error_401(self, ha_client):
        """测试 401 认证错误"""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_client.request.return_value = mock_resp

            with pytest.raises(HAAuthError) as exc_info:
                await ha_client.get_state("light.test")

            assert "认证失败" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_not_found_error_404(self, ha_client):
        """测试 404 设备不存在"""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_client.request.return_value = mock_resp

            with pytest.raises(HANotFoundError) as exc_info:
                await ha_client.get_state("light.not_exist")

            assert "不存在" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_connection_timeout(self, ha_client):
        """测试连接超时"""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            mock_client.request.side_effect = httpx.TimeoutException("timeout")

            with pytest.raises(HAConnectionError) as exc_info:
                await ha_client.get_state("light.test")

            assert "超时" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_server_error_5xx(self, ha_client):
        """测试 5xx 服务器错误"""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client_class.return_value = mock_client

            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_client.request.return_value = mock_resp

            with pytest.raises(HAError) as exc_info:
                await ha_client.get_state("light.test")

            assert "500" in str(exc_info.value)
