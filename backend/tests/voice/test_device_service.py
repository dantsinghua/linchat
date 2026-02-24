"""
设备管理服务单元测试 (T059)

覆盖:
- register_device: UUID 生成 + SM4 加密 + token_prefix 存储 + 明文 Token 仅返回一次
- revoke_device: 验证 is_active=False
- authenticate_by_token: 正确 Token 认证成功 + 更新 last_active_at、错误 Token 失败、已撤销设备失败
- list_devices: 返回设备列表（不含加密 Token）
- delete_device: 物理删除

测试框架: pytest + pytest-django
Mock 策略: mock 仓库层 + SM4 加密，纯单元测试不依赖数据库
覆盖率目标: >= 95%
"""

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from apps.voice.services.device_service import DeviceService


def run_async(coro):
    """在同步测试中运行异步协程"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_DEFAULT_CREATED_AT = datetime(2026, 2, 24, 10, 0, 0)

_SENTINEL = object()


def _make_device(
    pk=1,
    device_uuid="dev-uuid-001",
    user_id=100,
    name="测试设备",
    api_token_encrypted="encrypted_token_data",
    token_prefix="abcd1234",
    is_active=True,
    created_at=_SENTINEL,
    last_active_at=None,
):
    """创建模拟的 RegisteredDevice 对象"""
    return SimpleNamespace(
        pk=pk,
        device_uuid=device_uuid,
        user_id=user_id,
        name=name,
        api_token_encrypted=api_token_encrypted,
        token_prefix=token_prefix,
        is_active=is_active,
        created_at=_DEFAULT_CREATED_AT if created_at is _SENTINEL else created_at,
        last_active_at=last_active_at,
    )


# ============================================================================
# register_device 测试
# ============================================================================


class TestRegisterDevice(unittest.TestCase):
    """register_device 测试：UUID 生成 + SM4 加密 + token_prefix 存储 + 明文 Token 返回"""

    def setUp(self):
        self.service = DeviceService()
        self.user_id = 100

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_encrypt")
    @patch("apps.voice.services.device_service.secrets")
    @patch("apps.voice.services.device_service.uuid")
    def test_register_returns_device_uuid(
        self, mock_uuid, mock_secrets, mock_encrypt, mock_repo
    ):
        """注册返回有效的 device_uuid"""
        mock_uuid.uuid4.return_value = MagicMock(
            __str__=lambda self: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )
        mock_secrets.token_urlsafe.return_value = "ABCDEFGHabcdefgh12345678901234567890abcd"
        mock_encrypt.return_value = "encrypted_value"
        mock_repo.create = AsyncMock(
            return_value=_make_device(
                device_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                name="新设备",
            )
        )

        result = run_async(
            self.service.register_device(self.user_id, "新设备")
        )

        self.assertEqual(
            result["device_uuid"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_encrypt")
    @patch("apps.voice.services.device_service.secrets")
    @patch("apps.voice.services.device_service.uuid")
    def test_register_returns_name(
        self, mock_uuid, mock_secrets, mock_encrypt, mock_repo
    ):
        """注册返回设备名称"""
        mock_uuid.uuid4.return_value = MagicMock(
            __str__=lambda self: "test-uuid"
        )
        mock_secrets.token_urlsafe.return_value = "ABCDEFGH" + "x" * 30
        mock_encrypt.return_value = "enc"
        mock_repo.create = AsyncMock(
            return_value=_make_device(device_uuid="test-uuid", name="客厅音箱")
        )

        result = run_async(
            self.service.register_device(self.user_id, "客厅音箱")
        )
        self.assertEqual(result["name"], "客厅音箱")

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_encrypt")
    @patch("apps.voice.services.device_service.secrets")
    @patch("apps.voice.services.device_service.uuid")
    def test_register_returns_plaintext_token(
        self, mock_uuid, mock_secrets, mock_encrypt, mock_repo
    ):
        """注册返回明文 API Token（仅此一次可见）"""
        plain_token = "PlainTextToken1234567890abcdefghij"
        mock_uuid.uuid4.return_value = MagicMock(
            __str__=lambda self: "dev-uuid"
        )
        mock_secrets.token_urlsafe.return_value = plain_token
        mock_encrypt.return_value = "encrypted_value"
        mock_repo.create = AsyncMock(
            return_value=_make_device(device_uuid="dev-uuid")
        )

        result = run_async(
            self.service.register_device(self.user_id, "设备")
        )
        self.assertEqual(result["api_token"], plain_token)

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_encrypt")
    @patch("apps.voice.services.device_service.secrets")
    @patch("apps.voice.services.device_service.uuid")
    def test_register_calls_sm4_encrypt(
        self, mock_uuid, mock_secrets, mock_encrypt, mock_repo
    ):
        """注册时使用 SM4 加密 Token"""
        plain_token = "ABCDEFGH_plaintext_token_content"
        mock_uuid.uuid4.return_value = MagicMock(
            __str__=lambda self: "uuid-1"
        )
        mock_secrets.token_urlsafe.return_value = plain_token
        mock_encrypt.return_value = "sm4_encrypted_data"
        mock_repo.create = AsyncMock(
            return_value=_make_device(device_uuid="uuid-1")
        )

        run_async(self.service.register_device(self.user_id, "设备"))

        mock_encrypt.assert_called_once_with(plain_token)

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_encrypt")
    @patch("apps.voice.services.device_service.secrets")
    @patch("apps.voice.services.device_service.uuid")
    def test_register_stores_token_prefix(
        self, mock_uuid, mock_secrets, mock_encrypt, mock_repo
    ):
        """注册时存储 Token 前 8 位作为 token_prefix"""
        plain_token = "12345678_remaining_token_body_here"
        mock_uuid.uuid4.return_value = MagicMock(
            __str__=lambda self: "uuid-2"
        )
        mock_secrets.token_urlsafe.return_value = plain_token
        mock_encrypt.return_value = "encrypted"
        mock_repo.create = AsyncMock(
            return_value=_make_device(device_uuid="uuid-2")
        )

        run_async(self.service.register_device(self.user_id, "设备"))

        # 验证 create 调用参数中 token_prefix 为前 8 位
        call_kwargs = mock_repo.create.call_args[1]
        self.assertEqual(call_kwargs["token_prefix"], "12345678")

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_encrypt")
    @patch("apps.voice.services.device_service.secrets")
    @patch("apps.voice.services.device_service.uuid")
    def test_register_passes_encrypted_token_to_repo(
        self, mock_uuid, mock_secrets, mock_encrypt, mock_repo
    ):
        """注册时将加密后的 Token 传给仓库层"""
        mock_uuid.uuid4.return_value = MagicMock(
            __str__=lambda self: "uuid-3"
        )
        mock_secrets.token_urlsafe.return_value = "ABCDEFGHrest_of_token"
        mock_encrypt.return_value = "sm4_cipher_text_base64"
        mock_repo.create = AsyncMock(
            return_value=_make_device(device_uuid="uuid-3")
        )

        run_async(self.service.register_device(self.user_id, "设备"))

        call_kwargs = mock_repo.create.call_args[1]
        self.assertEqual(
            call_kwargs["api_token_encrypted"], "sm4_cipher_text_base64"
        )

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_encrypt")
    @patch("apps.voice.services.device_service.secrets")
    @patch("apps.voice.services.device_service.uuid")
    def test_register_passes_user_id_to_repo(
        self, mock_uuid, mock_secrets, mock_encrypt, mock_repo
    ):
        """注册时将 user_id 传给仓库层"""
        mock_uuid.uuid4.return_value = MagicMock(
            __str__=lambda self: "uuid-4"
        )
        mock_secrets.token_urlsafe.return_value = "ABCDEFGH" + "x" * 30
        mock_encrypt.return_value = "enc"
        mock_repo.create = AsyncMock(
            return_value=_make_device(device_uuid="uuid-4")
        )

        run_async(self.service.register_device(42, "设备"))

        call_kwargs = mock_repo.create.call_args[1]
        self.assertEqual(call_kwargs["user_id"], 42)

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_encrypt")
    @patch("apps.voice.services.device_service.secrets")
    @patch("apps.voice.services.device_service.uuid")
    def test_register_result_has_only_three_keys(
        self, mock_uuid, mock_secrets, mock_encrypt, mock_repo
    ):
        """注册结果只包含 device_uuid / name / api_token 三个键"""
        mock_uuid.uuid4.return_value = MagicMock(
            __str__=lambda self: "uuid-5"
        )
        mock_secrets.token_urlsafe.return_value = "ABCDEFGH" + "x" * 30
        mock_encrypt.return_value = "enc"
        mock_repo.create = AsyncMock(
            return_value=_make_device(device_uuid="uuid-5", name="设备")
        )

        result = run_async(
            self.service.register_device(self.user_id, "设备")
        )
        self.assertEqual(
            set(result.keys()), {"device_uuid", "name", "api_token"}
        )


# ============================================================================
# revoke_device 测试
# ============================================================================


class TestRevokeDevice(unittest.TestCase):
    """revoke_device 测试：验证 is_active=False"""

    def setUp(self):
        self.service = DeviceService()
        self.user_id = 100

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_revoke_success(self, mock_repo):
        """停用设备成功返回 True"""
        mock_repo.deactivate = AsyncMock(return_value=1)

        result = run_async(
            self.service.revoke_device(self.user_id, "dev-uuid-001")
        )
        self.assertTrue(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_revoke_calls_deactivate(self, mock_repo):
        """停用时调用仓库层 deactivate"""
        mock_repo.deactivate = AsyncMock(return_value=1)

        run_async(
            self.service.revoke_device(self.user_id, "dev-uuid-002")
        )

        mock_repo.deactivate.assert_awaited_once_with(
            "dev-uuid-002", self.user_id
        )

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_revoke_nonexistent_returns_false(self, mock_repo):
        """停用不存在的设备返回 False"""
        mock_repo.deactivate = AsyncMock(return_value=0)

        result = run_async(
            self.service.revoke_device(self.user_id, "nonexistent-uuid")
        )
        self.assertFalse(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_revoke_wrong_user_returns_false(self, mock_repo):
        """其他用户停用返回 False（隔离粒度为 user_id）"""
        mock_repo.deactivate = AsyncMock(return_value=0)

        result = run_async(
            self.service.revoke_device(999, "dev-uuid-001")
        )
        self.assertFalse(result)


# ============================================================================
# authenticate_by_token 测试
# ============================================================================


class TestAuthenticateByToken(unittest.TestCase):
    """authenticate_by_token 测试：Token 认证 + last_active_at 更新"""

    def setUp(self):
        self.service = DeviceService()

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_decrypt")
    def test_auth_success(self, mock_decrypt, mock_repo):
        """正确 Token 认证成功，返回用户信息"""
        raw_token = "ABCDEFGH_valid_token_content_here"
        device = _make_device(
            pk=10,
            device_uuid="auth-dev-001",
            user_id=100,
            name="认证设备",
            api_token_encrypted="encrypted_data",
            token_prefix=raw_token[:8],
        )
        mock_repo.find_by_token_prefix = AsyncMock(return_value=[device])
        mock_repo.update_last_active = AsyncMock()
        mock_decrypt.return_value = raw_token

        result = run_async(
            self.service.authenticate_by_token(raw_token)
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["user_id"], 100)
        self.assertEqual(result["device_uuid"], "auth-dev-001")
        self.assertEqual(result["device_name"], "认证设备")

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_decrypt")
    def test_auth_updates_last_active_at(self, mock_decrypt, mock_repo):
        """认证成功后调用 update_last_active"""
        raw_token = "ABCDEFGH_token_for_update_test"
        device = _make_device(pk=20, token_prefix=raw_token[:8])
        mock_repo.find_by_token_prefix = AsyncMock(return_value=[device])
        mock_repo.update_last_active = AsyncMock()
        mock_decrypt.return_value = raw_token

        run_async(self.service.authenticate_by_token(raw_token))

        mock_repo.update_last_active.assert_awaited_once_with(20)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_auth_wrong_prefix_no_candidates(self, mock_repo):
        """前缀不匹配时无候选设备，认证失败"""
        mock_repo.find_by_token_prefix = AsyncMock(return_value=[])

        result = run_async(
            self.service.authenticate_by_token("ZZZZZZZZ_wrong_token")
        )
        self.assertIsNone(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_decrypt")
    def test_auth_prefix_match_but_token_mismatch(
        self, mock_decrypt, mock_repo
    ):
        """前缀匹配但完整 Token 不同，认证失败"""
        raw_token = "ABCDEFGH_actual_token_value"
        device = _make_device(pk=30, token_prefix=raw_token[:8])
        mock_repo.find_by_token_prefix = AsyncMock(return_value=[device])
        mock_decrypt.return_value = "ABCDEFGH_different_token_value"

        result = run_async(
            self.service.authenticate_by_token(raw_token)
        )
        self.assertIsNone(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_auth_empty_token_fails(self, mock_repo):
        """空 Token 认证失败"""
        result = run_async(self.service.authenticate_by_token(""))
        self.assertIsNone(result)
        mock_repo.find_by_token_prefix.assert_not_called()

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_auth_none_token_fails(self, mock_repo):
        """None Token 认证失败"""
        result = run_async(self.service.authenticate_by_token(None))
        self.assertIsNone(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_auth_short_token_fails(self, mock_repo):
        """Token 不足 8 位认证失败"""
        result = run_async(
            self.service.authenticate_by_token("short")
        )
        self.assertIsNone(result)
        mock_repo.find_by_token_prefix.assert_not_called()

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_decrypt")
    def test_auth_sm4_decrypt_failure_skips_device(
        self, mock_decrypt, mock_repo
    ):
        """SM4 解密失败时跳过该设备，不崩溃"""
        raw_token = "ABCDEFGH_token_with_decrypt_error"
        device = _make_device(pk=40, token_prefix=raw_token[:8])
        mock_repo.find_by_token_prefix = AsyncMock(return_value=[device])
        mock_decrypt.side_effect = ValueError("解密失败")

        result = run_async(
            self.service.authenticate_by_token(raw_token)
        )
        self.assertIsNone(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_decrypt")
    def test_auth_generic_exception_skips_device(
        self, mock_decrypt, mock_repo
    ):
        """SM4 解密抛出通用异常时跳过该设备"""
        raw_token = "ABCDEFGH_token_generic_error"
        device = _make_device(pk=41, token_prefix=raw_token[:8])
        mock_repo.find_by_token_prefix = AsyncMock(return_value=[device])
        mock_decrypt.side_effect = Exception("未知错误")

        result = run_async(
            self.service.authenticate_by_token(raw_token)
        )
        self.assertIsNone(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_decrypt")
    def test_auth_multiple_candidates_matches_correct(
        self, mock_decrypt, mock_repo
    ):
        """多个候选设备时能正确匹配"""
        raw_token = "ABCDEFGH_the_correct_token"
        device1 = _make_device(
            pk=50,
            device_uuid="dev-wrong",
            user_id=200,
            name="错误设备",
            token_prefix=raw_token[:8],
        )
        device2 = _make_device(
            pk=51,
            device_uuid="dev-correct",
            user_id=100,
            name="正确设备",
            token_prefix=raw_token[:8],
        )
        mock_repo.find_by_token_prefix = AsyncMock(
            return_value=[device1, device2]
        )
        mock_repo.update_last_active = AsyncMock()
        # 第一个设备解密不匹配，第二个匹配
        mock_decrypt.side_effect = [
            "ABCDEFGH_different_token",
            raw_token,
        ]

        result = run_async(
            self.service.authenticate_by_token(raw_token)
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["device_uuid"], "dev-correct")
        self.assertEqual(result["user_id"], 100)
        mock_repo.update_last_active.assert_awaited_once_with(51)

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_decrypt")
    def test_auth_revoked_device_not_in_candidates(
        self, mock_decrypt, mock_repo
    ):
        """已撤销设备不在候选列表中（仓库层 filter is_active=True）"""
        raw_token = "ABCDEFGH_token_for_revoked"
        # 仓库层已过滤掉 is_active=False 的设备，返回空列表
        mock_repo.find_by_token_prefix = AsyncMock(return_value=[])

        result = run_async(
            self.service.authenticate_by_token(raw_token)
        )
        self.assertIsNone(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_decrypt")
    def test_auth_first_decrypt_fails_second_succeeds(
        self, mock_decrypt, mock_repo
    ):
        """第一个候选设备解密失败，第二个成功"""
        raw_token = "ABCDEFGH_skip_first_device"
        device1 = _make_device(pk=60, token_prefix=raw_token[:8])
        device2 = _make_device(
            pk=61,
            device_uuid="dev-second",
            user_id=100,
            name="第二设备",
            token_prefix=raw_token[:8],
        )
        mock_repo.find_by_token_prefix = AsyncMock(
            return_value=[device1, device2]
        )
        mock_repo.update_last_active = AsyncMock()
        mock_decrypt.side_effect = [ValueError("解密失败"), raw_token]

        result = run_async(
            self.service.authenticate_by_token(raw_token)
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["device_uuid"], "dev-second")


# ============================================================================
# list_devices 测试
# ============================================================================


class TestListDevices(unittest.TestCase):
    """list_devices 测试：返回设备列表（不含加密 Token）"""

    def setUp(self):
        self.service = DeviceService()
        self.user_id = 100

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_empty(self, mock_repo):
        """无设备时返回空列表"""
        mock_repo.find_by_user_id = AsyncMock(return_value=[])

        devices = run_async(self.service.list_devices(self.user_id))
        self.assertEqual(devices, [])

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_returns_devices(self, mock_repo):
        """返回已注册的设备列表"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=[
                _make_device(device_uuid="dev-a", name="设备A"),
                _make_device(device_uuid="dev-b", name="设备B"),
            ]
        )

        devices = run_async(self.service.list_devices(self.user_id))
        self.assertEqual(len(devices), 2)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_contains_expected_fields(self, mock_repo):
        """列表中包含正确的字段"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=[_make_device()]
        )

        devices = run_async(self.service.list_devices(self.user_id))
        expected_keys = {
            "device_uuid",
            "name",
            "is_active",
            "created_at",
            "last_active_at",
        }
        self.assertEqual(set(devices[0].keys()), expected_keys)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_does_not_contain_sensitive_fields(self, mock_repo):
        """列表不包含加密 Token 和 token_prefix"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=[_make_device()]
        )

        devices = run_async(self.service.list_devices(self.user_id))
        self.assertNotIn("api_token", devices[0])
        self.assertNotIn("api_token_encrypted", devices[0])
        self.assertNotIn("token_prefix", devices[0])

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_created_at_iso_format(self, mock_repo):
        """created_at 以 ISO 格式返回"""
        dt = datetime(2026, 2, 24, 10, 30, 0)
        mock_repo.find_by_user_id = AsyncMock(
            return_value=[_make_device(created_at=dt)]
        )

        devices = run_async(self.service.list_devices(self.user_id))
        self.assertEqual(devices[0]["created_at"], dt.isoformat())

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_last_active_at_null(self, mock_repo):
        """last_active_at 为 None 时返回 None"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=[_make_device(last_active_at=None)]
        )

        devices = run_async(self.service.list_devices(self.user_id))
        self.assertIsNone(devices[0]["last_active_at"])

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_last_active_at_iso_format(self, mock_repo):
        """last_active_at 有值时以 ISO 格式返回"""
        dt = datetime(2026, 2, 24, 15, 0, 0)
        mock_repo.find_by_user_id = AsyncMock(
            return_value=[_make_device(last_active_at=dt)]
        )

        devices = run_async(self.service.list_devices(self.user_id))
        self.assertEqual(devices[0]["last_active_at"], dt.isoformat())

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_includes_inactive_devices(self, mock_repo):
        """列表包含已停用的设备"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=[
                _make_device(device_uuid="active-dev", is_active=True),
                _make_device(device_uuid="inactive-dev", is_active=False),
            ]
        )

        devices = run_async(self.service.list_devices(self.user_id))
        self.assertEqual(len(devices), 2)
        active_flags = {d["device_uuid"]: d["is_active"] for d in devices}
        self.assertTrue(active_flags["active-dev"])
        self.assertFalse(active_flags["inactive-dev"])

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_calls_repo_with_user_id(self, mock_repo):
        """list_devices 按 user_id 查询（隔离粒度）"""
        mock_repo.find_by_user_id = AsyncMock(return_value=[])

        run_async(self.service.list_devices(42))

        mock_repo.find_by_user_id.assert_awaited_once_with(42)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_list_created_at_none(self, mock_repo):
        """created_at 为 None 时返回 None"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=[_make_device(created_at=None)]
        )

        devices = run_async(self.service.list_devices(self.user_id))
        self.assertIsNone(devices[0]["created_at"])


# ============================================================================
# delete_device 测试
# ============================================================================


class TestDeleteDevice(unittest.TestCase):
    """delete_device 测试：物理删除"""

    def setUp(self):
        self.service = DeviceService()
        self.user_id = 100

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_delete_success(self, mock_repo):
        """删除设备成功返回 True"""
        mock_repo.delete_by_uuid = AsyncMock(return_value=1)

        result = run_async(
            self.service.delete_device(self.user_id, "dev-uuid-001")
        )
        self.assertTrue(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_delete_calls_repo(self, mock_repo):
        """删除时调用仓库层 delete_by_uuid"""
        mock_repo.delete_by_uuid = AsyncMock(return_value=1)

        run_async(
            self.service.delete_device(self.user_id, "dev-uuid-002")
        )

        mock_repo.delete_by_uuid.assert_awaited_once_with(
            "dev-uuid-002", self.user_id
        )

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_delete_nonexistent_returns_false(self, mock_repo):
        """删除不存在的设备返回 False"""
        mock_repo.delete_by_uuid = AsyncMock(return_value=0)

        result = run_async(
            self.service.delete_device(self.user_id, "nonexistent-uuid")
        )
        self.assertFalse(result)

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_delete_wrong_user_returns_false(self, mock_repo):
        """其他用户删除返回 False（隔离粒度为 user_id）"""
        mock_repo.delete_by_uuid = AsyncMock(return_value=0)

        result = run_async(
            self.service.delete_device(999, "dev-uuid-001")
        )
        self.assertFalse(result)


# ============================================================================
# 集成场景测试（跨方法组合验证）
# ============================================================================


class TestDeviceServiceIntegration(unittest.TestCase):
    """跨方法组合验证：注册 -> 认证 -> 停用 -> 认证失败"""

    def setUp(self):
        self.service = DeviceService()

    @patch("apps.voice.services.device_service.registered_device_repo")
    @patch("apps.voice.services.device_service.sm4_encrypt")
    @patch("apps.voice.services.device_service.sm4_decrypt")
    @patch("apps.voice.services.device_service.secrets")
    @patch("apps.voice.services.device_service.uuid")
    def test_register_then_authenticate(
        self, mock_uuid, mock_secrets, mock_decrypt, mock_encrypt, mock_repo
    ):
        """注册后可以用 Token 认证"""
        plain_token = "ABCDEFGH_full_token_content_12345"
        mock_uuid.uuid4.return_value = MagicMock(
            __str__=lambda self: "reg-auth-uuid"
        )
        mock_secrets.token_urlsafe.return_value = plain_token
        mock_encrypt.return_value = "encrypted_token_data"

        created_device = _make_device(
            pk=70,
            device_uuid="reg-auth-uuid",
            user_id=100,
            name="集成测试设备",
            api_token_encrypted="encrypted_token_data",
            token_prefix=plain_token[:8],
        )
        mock_repo.create = AsyncMock(return_value=created_device)
        mock_repo.find_by_token_prefix = AsyncMock(
            return_value=[created_device]
        )
        mock_repo.update_last_active = AsyncMock()
        mock_decrypt.return_value = plain_token

        # 注册
        reg_result = run_async(
            self.service.register_device(100, "集成测试设备")
        )
        self.assertEqual(reg_result["api_token"], plain_token)

        # 认证
        auth_result = run_async(
            self.service.authenticate_by_token(plain_token)
        )
        self.assertIsNotNone(auth_result)
        self.assertEqual(auth_result["device_uuid"], "reg-auth-uuid")

    @patch("apps.voice.services.device_service.registered_device_repo")
    def test_revoke_then_delete(self, mock_repo):
        """停用后再删除"""
        mock_repo.deactivate = AsyncMock(return_value=1)
        mock_repo.delete_by_uuid = AsyncMock(return_value=1)

        # 停用
        revoke_ok = run_async(
            self.service.revoke_device(100, "dev-uuid")
        )
        self.assertTrue(revoke_ok)

        # 删除
        delete_ok = run_async(
            self.service.delete_device(100, "dev-uuid")
        )
        self.assertTrue(delete_ok)
