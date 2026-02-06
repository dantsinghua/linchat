"""Home Assistant REST API 客户端

封装 HA HTTP API 调用，统一异常处理。
参考: contracts/ha-api-contract.md
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from django.conf import settings


# ============ 自定义异常 ============
# 参考: contracts/ha-api-contract.md 错误映射


class HAError(Exception):
    """HA 通用错误基类"""

    pass


class HAAuthError(HAError):
    """HA 认证失败（401）"""

    pass


class HANotFoundError(HAError):
    """设备不存在（404）"""

    pass


class HAConnectionError(HAError):
    """HA 连接失败（超时/网络错误）"""

    pass


# ============ HAClient 实现 ============


class HAClient:
    """Home Assistant REST API 客户端

    使用 httpx.AsyncClient context manager 模式（R-004 决策）。
    所有方法为 async，统一超时和异常处理。
    """

    def __init__(self) -> None:
        self.base_url = settings.HA_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.HA_TOKEN}",
            "Content-Type": "application/json",
        }
        self.timeout = settings.HA_REQUEST_TIMEOUT

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        """统一 HTTP 请求，处理超时和认证错误"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self.headers,
                    **kwargs,
                )
        except httpx.TimeoutException as e:
            raise HAConnectionError(f"HA 请求超时: {e}") from e
        except httpx.ConnectError as e:
            raise HAConnectionError(f"HA 连接失败: {e}") from e

        # HTTP 状态码映射到异常
        if resp.status_code == 401:
            raise HAAuthError("HA 认证失败，请检查 Token 配置")
        if resp.status_code == 404:
            raise HANotFoundError(f"资源不存在: {path}")
        if resp.status_code >= 400:
            raise HAError(f"HA 服务返回错误: {resp.status_code}")

        return resp

    async def get_state(self, entity_id: str) -> dict:
        """获取单个设备状态

        Args:
            entity_id: 设备实体ID，如 light.living_room

        Returns:
            设备状态字典，包含 entity_id, state, attributes, last_changed 等
        """
        resp = await self._request("GET", f"/api/states/{entity_id}")
        return resp.json()

    async def get_states(self, domain: str | None = None) -> list[dict]:
        """获取所有设备状态

        Args:
            domain: 可选的设备域过滤，如 light, switch, climate

        Returns:
            设备状态列表
        """
        resp = await self._request("GET", "/api/states")
        states = resp.json()

        if domain:
            states = [s for s in states if s["entity_id"].startswith(f"{domain}.")]

        return states

    async def call_service(
        self, domain: str, service: str, data: dict
    ) -> list[dict]:
        """调用 HA 服务

        Args:
            domain: 服务域，如 homeassistant, light, climate
            service: 服务名，如 turn_on, turn_off
            data: 服务数据，通常包含 entity_id 和其他参数

        Returns:
            受影响的设备状态列表
        """
        resp = await self._request(
            "POST", f"/api/services/{domain}/{service}", json=data
        )
        return resp.json()

    async def get_history(
        self, entity_id: str, hours: int = 24
    ) -> list[list[dict]]:
        """获取设备历史记录

        Args:
            entity_id: 设备实体ID
            hours: 查询时间范围（小时），默认24小时

        Returns:
            历史状态变化列表的列表
        """
        # 计算起始时间
        start_time = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        timestamp = start_time.strftime("%Y-%m-%dT%H:%M:%S%z")

        resp = await self._request(
            "GET",
            f"/api/history/period/{timestamp}",
            params={
                "filter_entity_id": entity_id,
                "minimal_response": "true",
            },
        )
        return resp.json()

    async def get_error_log(self) -> str:
        """获取 HA 错误日志

        Returns:
            纯文本日志内容
        """
        resp = await self._request("GET", "/api/error_log")
        return resp.text

    async def get_config(self) -> dict:
        """获取 HA 系统配置

        Returns:
            配置字典，包含 version, components, unit_system 等
        """
        resp = await self._request("GET", "/api/config")
        return resp.json()

    async def check_health(self) -> bool:
        """检查 HA 服务健康状态

        Returns:
            True 表示服务正常，False 表示不可达
        """
        try:
            resp = await self._request("GET", "/api/")
            return resp.status_code == 200
        except HAError:
            return False
