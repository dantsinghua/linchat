from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from django.conf import settings


class HAError(Exception):
    pass


class HAAuthError(HAError):
    pass


class HANotFoundError(HAError):
    pass


class HAConnectionError(HAError):
    pass


class HAClient:

    def __init__(self) -> None:
        self.base_url = settings.HA_URL.rstrip("/")
        self.headers = {"Authorization": f"Bearer {settings.HA_TOKEN}", "Content-Type": "application/json"}
        self.timeout = settings.HA_REQUEST_TIMEOUT

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(method, f"{self.base_url}{path}", headers=self.headers, **kwargs)
        except httpx.TimeoutException as e:
            raise HAConnectionError(f"HA 请求超时: {e}") from e
        except httpx.ConnectError as e:
            raise HAConnectionError(f"HA 连接失败: {e}") from e
        if resp.status_code == 401: raise HAAuthError("HA 认证失败，请检查 Token 配置")
        if resp.status_code == 404: raise HANotFoundError(f"资源不存在: {path}")
        if resp.status_code >= 400: raise HAError(f"HA 服务返回错误: {resp.status_code}")
        return resp

    async def get_state(self, entity_id: str) -> dict:
        resp = await self._request("GET", f"/api/states/{entity_id}")
        return resp.json()

    async def get_states(self, domain: str | None = None) -> list[dict]:
        resp = await self._request("GET", "/api/states")
        states = resp.json()
        if domain: states = [s for s in states if s["entity_id"].startswith(f"{domain}.")]
        return states

    async def call_service(self, domain: str, service: str, data: dict) -> list[dict]:
        resp = await self._request("POST", f"/api/services/{domain}/{service}", json=data)
        return resp.json()

    async def get_history(self, entity_id: str, hours: int = 24) -> list[list[dict]]:
        start_time = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        timestamp = start_time.strftime("%Y-%m-%dT%H:%M:%S%z")
        resp = await self._request(
            "GET", f"/api/history/period/{timestamp}",
            params={"filter_entity_id": entity_id, "minimal_response": "true"},
        )
        return resp.json()

    async def get_error_log(self) -> str:
        resp = await self._request("GET", "/api/error_log")
        return resp.text

    async def get_config(self) -> dict:
        resp = await self._request("GET", "/api/config")
        return resp.json()

    async def check_health(self) -> bool:
        try:
            resp = await self._request("GET", "/api/")
            return resp.status_code == 200
        except HAError:
            return False
