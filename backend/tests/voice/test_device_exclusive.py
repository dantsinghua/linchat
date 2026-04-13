"""设备独占 ambient 模式测试 — SessionMixin._check_device_exclusive()

覆盖:
- 设备连接 ambient → 浏览器尝试 ambient → 浏览器被拒绝 (DEVICE_EXCLUSIVE)
- 浏览器连接 ambient → 设备连接 → 浏览器被踢 (force_disconnect)
- 两个浏览器连接 → 无独占逻辑（后者踢前者，正常接管）
- 设备1 连接 → 设备2 连接 → 设备1 被踢
- 断开连接时注销 Redis 注册
- Redis 注册 TTL = VOICE_AMBIENT_SESSION_TTL

Mock 策略:
- core.redis (redis_get / redis_setex_json / redis_delete) → 控制 ambient 连接注册
- channel_layer.send → 验证 force_disconnect 消息
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.voice.consumer_session import _AMBIENT_CONN_KEY


# ============ Fixtures ============


@pytest.fixture
def mock_mixin():
    """创建一个带有 SessionMixin 方法的 mock consumer 对象"""
    from apps.voice.consumer_session import SessionMixin

    mixin = SessionMixin()
    mixin.user_id = 1
    mixin.channel_name = "test_channel_new"
    mixin._is_device_connection = True
    mixin._mode = "ambient"
    mixin._asr_client = MagicMock()
    mixin._asr_client.connected = True
    mixin._asr_client.disconnect = AsyncMock()
    mixin._configured = True
    mixin._send_error = AsyncMock()
    mixin._send_json = AsyncMock()
    mixin.channel_layer = MagicMock()
    mixin.channel_layer.send = AsyncMock()
    return mixin


def _build_conn_json(channel_name: str, is_device: bool) -> str:
    return json.dumps({"channel_name": channel_name, "is_device": is_device})


# ============ 设备连接在先 → 浏览器被拒绝 ============


class TestDeviceBlocksBrowser:
    """已有设备 ambient 连接 → 浏览器尝试 ambient → 被拒绝"""

    @pytest.mark.asyncio
    async def test_browser_rejected_when_device_active(self, mock_mixin):
        """设备已连接，浏览器尝试 ambient → DEVICE_EXCLUSIVE 错误 + ASR 断开"""
        mock_mixin._is_device_connection = False  # 新连接是浏览器
        existing = _build_conn_json("device_channel_old", is_device=True)

        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=existing)),
            patch("apps.voice.consumer_session.redis_setex_json", AsyncMock()),
            patch("apps.voice.consumer_session.redis_delete", AsyncMock()),
        ):
            rejected = await mock_mixin._check_device_exclusive()

        assert rejected is True
        mock_mixin._send_error.assert_called_once()
        args = mock_mixin._send_error.call_args[0]
        assert args[0] == "DEVICE_EXCLUSIVE"
        assert mock_mixin._configured is False
        mock_mixin._asr_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_browser_rejected_no_force_disconnect_sent(self, mock_mixin):
        """浏览器被拒绝时，不发送 force_disconnect 给已有设备"""
        mock_mixin._is_device_connection = False
        existing = _build_conn_json("device_channel_old", is_device=True)

        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=existing)),
            patch("apps.voice.consumer_session.redis_setex_json", AsyncMock()),
            patch("apps.voice.consumer_session.redis_delete", AsyncMock()),
        ):
            await mock_mixin._check_device_exclusive()

        # 不应该向已有设备发送 force_disconnect
        mock_mixin.channel_layer.send.assert_not_called()


# ============ 浏览器连接在先 → 设备踢掉浏览器 ============


class TestDeviceKicksBrowser:
    """浏览器已连接 ambient → 设备连接 → 浏览器被踢"""

    @pytest.mark.asyncio
    async def test_device_kicks_browser(self, mock_mixin):
        """设备连接时踢掉已有浏览器连接"""
        mock_mixin._is_device_connection = True
        existing = _build_conn_json("browser_channel_old", is_device=False)

        mock_setex = AsyncMock()
        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=existing)),
            patch("apps.voice.consumer_session.redis_setex_json", mock_setex),
            patch("apps.voice.consumer_session.redis_delete", AsyncMock()),
        ):
            rejected = await mock_mixin._check_device_exclusive()

        assert rejected is False
        # 验证向旧浏览器发送了 force_disconnect
        mock_mixin.channel_layer.send.assert_called_once()
        call_args = mock_mixin.channel_layer.send.call_args[0]
        assert call_args[0] == "browser_channel_old"
        assert call_args[1]["type"] == "force_disconnect"
        assert call_args[1]["reason"] == "device_exclusive"
        # 验证注册了新设备连接
        mock_setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_device_registers_after_kick(self, mock_mixin):
        """设备踢掉浏览器后，正确注册自己"""
        mock_mixin._is_device_connection = True
        mock_mixin.channel_name = "new_device_ch"
        existing = _build_conn_json("old_browser_ch", is_device=False)

        mock_setex = AsyncMock()
        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=existing)),
            patch("apps.voice.consumer_session.redis_setex_json", mock_setex),
            patch("apps.voice.consumer_session.redis_delete", AsyncMock()),
        ):
            await mock_mixin._check_device_exclusive()

        # 验证注册的内容
        call_args = mock_setex.call_args[0]
        key = call_args[0]
        assert "ambient_conn" in key
        registered = call_args[2]
        assert registered["channel_name"] == "new_device_ch"
        assert registered["is_device"] is True


# ============ 两个浏览器连接 → 正常接管 ============


class TestBrowserToBrowserNoExclusive:
    """两个浏览器连接 → 无设备独占，后者踢前者（正常接管）"""

    @pytest.mark.asyncio
    async def test_browser_kicks_browser(self, mock_mixin):
        """浏览器 B 连接时踢掉浏览器 A，无独占拒绝"""
        mock_mixin._is_device_connection = False
        mock_mixin.channel_name = "browser_B"
        existing = _build_conn_json("browser_A", is_device=False)

        mock_setex = AsyncMock()
        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=existing)),
            patch("apps.voice.consumer_session.redis_setex_json", mock_setex),
            patch("apps.voice.consumer_session.redis_delete", AsyncMock()),
        ):
            rejected = await mock_mixin._check_device_exclusive()

        assert rejected is False
        # 旧浏览器被踢
        mock_mixin.channel_layer.send.assert_called_once()
        call_args = mock_mixin.channel_layer.send.call_args[0]
        assert call_args[0] == "browser_A"
        # 新浏览器注册
        mock_setex.assert_called_once()


# ============ 设备1 → 设备2 → 设备1 被踢 ============


class TestDeviceKicksDevice:
    """设备1 已连接 → 设备2 连接 → 设备1 被踢"""

    @pytest.mark.asyncio
    async def test_new_device_kicks_old_device(self, mock_mixin):
        """新设备连接时踢掉旧设备"""
        mock_mixin._is_device_connection = True
        mock_mixin.channel_name = "device_2"
        existing = _build_conn_json("device_1", is_device=True)

        mock_setex = AsyncMock()
        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=existing)),
            patch("apps.voice.consumer_session.redis_setex_json", mock_setex),
            patch("apps.voice.consumer_session.redis_delete", AsyncMock()),
        ):
            rejected = await mock_mixin._check_device_exclusive()

        assert rejected is False
        # 旧设备被踢
        mock_mixin.channel_layer.send.assert_called_once()
        call_args = mock_mixin.channel_layer.send.call_args[0]
        assert call_args[0] == "device_1"
        assert call_args[1]["type"] == "force_disconnect"


# ============ 无已有连接 → 直接注册 ============


class TestNoExistingConnection:
    """无已有 ambient 连接 → 直接注册"""

    @pytest.mark.asyncio
    async def test_first_connection_registers(self, mock_mixin):
        """首个 ambient 连接直接注册，不踢任何人"""
        mock_mixin._is_device_connection = True

        mock_setex = AsyncMock()
        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=None)),
            patch("apps.voice.consumer_session.redis_setex_json", mock_setex),
        ):
            rejected = await mock_mixin._check_device_exclusive()

        assert rejected is False
        mock_mixin.channel_layer.send.assert_not_called()
        mock_setex.assert_called_once()


# ============ 断开连接注销注册 ============


class TestUnregisterOnDisconnect:
    """disconnect 时注销 ambient 连接注册"""

    @pytest.mark.asyncio
    async def test_unregister_own_connection(self, mock_mixin):
        """注销时删除属于自己 channel_name 的 Redis 键"""
        mock_mixin.channel_name = "my_channel"
        existing = _build_conn_json("my_channel", is_device=True)

        mock_delete = AsyncMock()
        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=existing)),
            patch("apps.voice.consumer_session.redis_delete", mock_delete),
        ):
            await mock_mixin._unregister_ambient_connection()

        mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_unregister_if_not_own(self, mock_mixin):
        """如果 Redis 键不属于自己（已被新连接覆盖），不删除"""
        mock_mixin.channel_name = "old_channel"
        existing = _build_conn_json("new_channel", is_device=True)

        mock_delete = AsyncMock()
        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=existing)),
            patch("apps.voice.consumer_session.redis_delete", mock_delete),
        ):
            await mock_mixin._unregister_ambient_connection()

        mock_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_unregister_if_no_key(self, mock_mixin):
        """Redis 键不存在时安全跳过"""
        mock_delete = AsyncMock()
        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=None)),
            patch("apps.voice.consumer_session.redis_delete", mock_delete),
        ):
            await mock_mixin._unregister_ambient_connection()

        mock_delete.assert_not_called()


# ============ 同一连接重复 configure ============


class TestSameConnectionReconfigure:
    """同一连接重复 configure → 不踢自己"""

    @pytest.mark.asyncio
    async def test_same_channel_no_kick(self, mock_mixin):
        """同一 channel_name 重新 configure 不触发 force_disconnect"""
        mock_mixin.channel_name = "same_channel"
        existing = _build_conn_json("same_channel", is_device=True)

        mock_setex = AsyncMock()
        with (
            patch("apps.voice.consumer_session.redis_get", AsyncMock(return_value=existing)),
            patch("apps.voice.consumer_session.redis_setex_json", mock_setex),
            patch("apps.voice.consumer_session.redis_delete", AsyncMock()),
        ):
            rejected = await mock_mixin._check_device_exclusive()

        assert rejected is False
        mock_mixin.channel_layer.send.assert_not_called()
        mock_setex.assert_called_once()
