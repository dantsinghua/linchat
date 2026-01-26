"""
用户认证服务单元测试

覆盖:
- CaptchaService: 验证码生成、Redis存储TTL、一次性使用验证
- AuthService: 密码SM3哈希验证、Token SM4加密生成、双重过期机制
- 登录锁定: 5次失败锁定、15分钟解锁、成功后计数重置
- 单点登录: 新登录使旧Token失效、Token索引更新

覆盖率要求: 服务层 ≥ 95%
"""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from apps.common.exceptions import (
    AccountLockedException,
    AuthFailedException,
    CaptchaInvalidException,
    UserDisabledException,
)
from apps.users.crypto import (
    generate_token,
    generate_token_hash,
    parse_token,
    sm3_hash,
    sm4_decrypt,
    sm4_decrypt_safe,
    sm4_encrypt,
    sm4_encrypt_safe,
    verify_password,
)
from apps.users.models import SysUser
from apps.users.services import AuthService, CaptchaService, LoginResult, SSOService


# ============ 测试辅助函数 ============


def run_async(coro):
    """运行异步函数"""
    return asyncio.get_event_loop().run_until_complete(coro)


# ============ Crypto 模块测试 ============


class TestCryptoModule(TestCase):
    """国密算法测试"""

    def test_sm3_hash_string(self):
        """测试 SM3 哈希 - 字符串输入"""
        password = "!9871229Qing"
        hash_value = sm3_hash(password)

        # SM3 哈希值应为 64 位十六进制字符串
        self.assertEqual(len(hash_value), 64)
        # 相同输入应产生相同哈希
        self.assertEqual(hash_value, sm3_hash(password))

    def test_sm3_hash_bytes(self):
        """测试 SM3 哈希 - 字节输入"""
        password = b"!9871229Qing"
        hash_value = sm3_hash(password)
        self.assertEqual(len(hash_value), 64)

    def test_verify_password_success(self):
        """测试密码验证 - 成功"""
        password = "!9871229Qing"
        password_hash = sm3_hash(password)
        self.assertTrue(verify_password(password, password_hash))

    def test_verify_password_failure(self):
        """测试密码验证 - 失败"""
        password = "!9871229Qing"
        wrong_password = "wrong_password"
        password_hash = sm3_hash(password)
        self.assertFalse(verify_password(wrong_password, password_hash))

    def test_sm4_encrypt_decrypt(self):
        """测试 SM4 加密解密"""
        plaintext = "test_password_123"
        ciphertext = sm4_encrypt(plaintext)

        # 密文应为 Base64 字符串
        self.assertIsInstance(ciphertext, str)
        self.assertNotEqual(ciphertext, plaintext)

        # 解密应得到原文
        decrypted = sm4_decrypt(ciphertext)
        self.assertEqual(decrypted, plaintext)

    def test_sm4_decrypt_invalid(self):
        """测试 SM4 解密 - 无效密文"""
        with self.assertRaises(ValueError):
            sm4_decrypt("invalid_ciphertext")

    def test_generate_token(self):
        """测试 Token 生成"""
        username = "admin"
        password = sm4_encrypt("!9871229Qing")
        captcha = "ABCD"
        timestamp = int(timezone.now().timestamp())

        token = generate_token(username, password, captcha, timestamp)

        # Token 应为 SM4 加密的字符串
        self.assertIsInstance(token, str)

        # 解析 Token
        parsed = parse_token(token)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["username"], username)
        self.assertEqual(parsed["captcha_code"], captcha)
        self.assertEqual(parsed["timestamp"], timestamp)

    def test_generate_token_hash(self):
        """测试 Token 哈希生成"""
        token = "test_token_string"
        token_hash = generate_token_hash(token)

        # SHA256 哈希值应为 64 位十六进制字符串
        self.assertEqual(len(token_hash), 64)
        # 相同输入应产生相同哈希
        self.assertEqual(token_hash, generate_token_hash(token))

    def test_parse_token_invalid(self):
        """测试 Token 解析 - 无效 Token"""
        result = parse_token("invalid_token")
        self.assertIsNone(result)

    def test_parse_token_wrong_parts_count(self):
        """测试 Token 解析 - 部分数量不等于 4"""
        # 创建一个只有3个部分的加密token
        malformed_data = "part1|part2|part3"
        encrypted = sm4_encrypt(malformed_data)
        result = parse_token(encrypted)
        self.assertIsNone(result)

    def test_sm4_encrypt_safe_success(self):
        """测试 SM4 安全加密 - 成功"""
        plaintext = "test_data"
        result = sm4_encrypt_safe(plaintext)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)

    def test_sm4_decrypt_safe_success(self):
        """测试 SM4 安全解密 - 成功"""
        plaintext = "test_data"
        ciphertext = sm4_encrypt(plaintext)
        result = sm4_decrypt_safe(ciphertext)
        self.assertEqual(result, plaintext)

    def test_sm4_decrypt_safe_failure(self):
        """测试 SM4 安全解密 - 失败返回 None"""
        result = sm4_decrypt_safe("invalid_ciphertext")
        self.assertIsNone(result)

    @patch("apps.users.crypto.settings")
    def test_sm4_key_short(self, mock_settings):
        """测试 SM4 密钥 - 短密钥自动填充"""
        mock_settings.SM4_SECRET_KEY = "short"  # 少于16字节

        # 重新导入以使用新设置
        from apps.users.crypto import _get_sm4_key
        key = _get_sm4_key()
        self.assertEqual(len(key), 16)

    @patch("apps.users.crypto.settings")
    def test_sm4_key_long(self, mock_settings):
        """测试 SM4 密钥 - 长密钥截断"""
        mock_settings.SM4_SECRET_KEY = "this_is_a_very_long_key_more_than_16_bytes"

        from apps.users.crypto import _get_sm4_key
        key = _get_sm4_key()
        self.assertEqual(len(key), 16)


# ============ CaptchaService 测试 ============


class TestCaptchaService(TestCase):
    """验证码服务测试"""

    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.get_captcha_key")
    def test_generate_captcha(self, mock_key, mock_setex):
        """测试验证码生成"""
        mock_key.return_value = "auth:captcha:test-id"
        mock_setex.return_value = True

        result = run_async(CaptchaService.generate())

        self.assertIsNotNone(result.captcha_id)
        self.assertTrue(result.captcha_image.startswith("data:image/png;base64,"))
        mock_setex.assert_called_once()

    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.get_captcha_key")
    def test_generate_captcha_custom_size(self, mock_key, mock_setex):
        """测试验证码生成 - 自定义尺寸"""
        mock_key.return_value = "auth:captcha:test-id"
        mock_setex.return_value = True

        result = run_async(CaptchaService.generate(width=150, height=50))

        self.assertIsNotNone(result.captcha_id)
        self.assertTrue(result.captcha_image.startswith("data:image/png;base64,"))

    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_get")
    @patch("apps.users.services.get_captcha_key")
    def test_verify_captcha_success(self, mock_key, mock_get, mock_delete):
        """测试验证码验证 - 成功"""
        mock_key.return_value = "auth:captcha:test-id"
        mock_get.return_value = "ABCD"
        mock_delete.return_value = 1

        result = run_async(CaptchaService.verify("test-id", "abcd"))

        self.assertTrue(result)
        # 一次性使用，验证后应删除
        mock_delete.assert_called_once()

    @patch("apps.users.services.redis_get")
    @patch("apps.users.services.get_captcha_key")
    def test_verify_captcha_expired(self, mock_key, mock_get):
        """测试验证码验证 - 已过期"""
        mock_key.return_value = "auth:captcha:test-id"
        mock_get.return_value = None  # 过期后 Redis 返回 None

        with self.assertRaises(CaptchaInvalidException) as ctx:
            run_async(CaptchaService.verify("test-id", "ABCD"))

        self.assertIn("已过期", str(ctx.exception.message))

    @patch("apps.users.services.redis_get")
    @patch("apps.users.services.get_captcha_key")
    def test_verify_captcha_wrong_code(self, mock_key, mock_get):
        """测试验证码验证 - 错误验证码"""
        mock_key.return_value = "auth:captcha:test-id"
        mock_get.return_value = "ABCD"

        with self.assertRaises(CaptchaInvalidException) as ctx:
            run_async(CaptchaService.verify("test-id", "WXYZ"))

        self.assertIn("错误", str(ctx.exception.message))

    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_get")
    @patch("apps.users.services.get_captcha_key")
    def test_verify_captcha_case_insensitive(self, mock_key, mock_get, mock_delete):
        """测试验证码验证 - 大小写不敏感"""
        mock_key.return_value = "auth:captcha:test-id"
        mock_get.return_value = "ABCD"
        mock_delete.return_value = 1

        # 小写也应通过
        result = run_async(CaptchaService.verify("test-id", "abcd"))
        self.assertTrue(result)


# ============ AuthService 测试 ============


@pytest.mark.django_db(transaction=True)
class TestAuthService(TransactionTestCase):
    """认证服务测试"""

    def setUp(self):
        """初始化测试数据"""
        self.username = "testuser"
        self.password = "Test@123456"
        self.password_hash = sm3_hash(self.password)

        # 创建测试用户
        self.user = SysUser.objects.create(
            username=self.username,
            password_hash=self.password_hash,
            status=1,
        )

    def tearDown(self):
        """清理测试数据"""
        SysUser.objects.all().delete()

    @patch("apps.users.services.SSOService.invalidate_old_tokens")
    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_setex_json")
    @patch("apps.users.services.CaptchaService.verify")
    def test_login_success(self, mock_verify, mock_setex, mock_delete, mock_sso):
        """测试登录 - 成功"""
        mock_verify.return_value = True
        mock_setex.return_value = True
        mock_delete.return_value = 1
        mock_sso.return_value = None

        encrypted_password = sm4_encrypt(self.password)

        result = run_async(
            AuthService.login(
                username=self.username,
                encrypted_password=encrypted_password,
                captcha_id="test-captcha-id",
                captcha_code="ABCD",
                client_ip="127.0.0.1",
            )
        )

        self.assertIsInstance(result, LoginResult)
        self.assertEqual(result.username, self.username)
        self.assertIsNotNone(result.token)
        mock_verify.assert_called_once()
        mock_sso.assert_called_once()

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_user_not_found(self, mock_verify):
        """测试登录 - 用户不存在"""
        mock_verify.return_value = True

        encrypted_password = sm4_encrypt("password")

        with patch("apps.users.services.redis_setex") as mock_setex:
            mock_setex.return_value = True
            with self.assertRaises(AuthFailedException):
                run_async(
                    AuthService.login(
                        username="nonexistent",
                        encrypted_password=encrypted_password,
                        captcha_id="test-captcha-id",
                        captcha_code="ABCD",
                        client_ip="127.0.0.1",
                    )
                )

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_wrong_password(self, mock_verify):
        """测试登录 - 密码错误"""
        mock_verify.return_value = True

        encrypted_password = sm4_encrypt("wrong_password")

        with self.assertRaises(AuthFailedException):
            run_async(
                AuthService.login(
                    username=self.username,
                    encrypted_password=encrypted_password,
                    captcha_id="test-captcha-id",
                    captcha_code="ABCD",
                    client_ip="127.0.0.1",
                )
            )

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_disabled_user(self, mock_verify):
        """测试登录 - 账户已禁用"""
        mock_verify.return_value = True

        # 禁用用户
        self.user.status = 0
        self.user.save()

        encrypted_password = sm4_encrypt(self.password)

        with self.assertRaises(UserDisabledException):
            run_async(
                AuthService.login(
                    username=self.username,
                    encrypted_password=encrypted_password,
                    captcha_id="test-captcha-id",
                    captcha_code="ABCD",
                    client_ip="127.0.0.1",
                )
            )

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_locked_account(self, mock_verify):
        """测试登录 - 账户已锁定"""
        mock_verify.return_value = True

        # 锁定用户
        self.user.lock_until = timezone.now() + timedelta(minutes=10)
        self.user.save()

        encrypted_password = sm4_encrypt(self.password)

        with self.assertRaises(AccountLockedException) as ctx:
            run_async(
                AuthService.login(
                    username=self.username,
                    encrypted_password=encrypted_password,
                    captcha_id="test-captcha-id",
                    captcha_code="ABCD",
                    client_ip="127.0.0.1",
                )
            )

        self.assertIn("锁定", str(ctx.exception.message))

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_invalid_password_format(self, mock_verify):
        """测试登录 - 密码格式错误（非 SM4 加密）"""
        mock_verify.return_value = True

        with self.assertRaises(AuthFailedException) as ctx:
            run_async(
                AuthService.login(
                    username=self.username,
                    encrypted_password="not_encrypted",
                    captcha_id="test-captcha-id",
                    captcha_code="ABCD",
                    client_ip="127.0.0.1",
                )
            )

        self.assertIn("格式错误", str(ctx.exception.message))

    @patch("apps.users.services.redis_delete")
    def test_logout(self, mock_delete):
        """测试登出"""
        mock_delete.return_value = 1

        result = run_async(AuthService.logout(self.user.user_id, "test_token_hash"))

        self.assertTrue(result)
        # 应删除 Token 和用户 Token 索引
        self.assertEqual(mock_delete.call_count, 2)


# ============ 登录失败锁定测试 ============


@pytest.mark.django_db(transaction=True)
class TestLoginLockout(TransactionTestCase):
    """登录失败锁定测试

    规则: R_LOGIN_001 - 5次失败锁定15分钟
    """

    def setUp(self):
        """初始化测试数据"""
        self.username = "locktest"
        self.password = "Test@123456"
        self.password_hash = sm3_hash(self.password)

        self.user = SysUser.objects.create(
            username=self.username,
            password_hash=self.password_hash,
            status=1,
        )

    def tearDown(self):
        """清理测试数据"""
        SysUser.objects.all().delete()

    @patch("apps.users.services.CaptchaService.verify")
    def test_fail_count_increment(self, mock_verify):
        """测试登录失败计数递增"""
        mock_verify.return_value = True
        encrypted_password = sm4_encrypt("wrong_password")

        # 失败 3 次
        for i in range(3):
            try:
                run_async(
                    AuthService.login(
                        username=self.username,
                        encrypted_password=encrypted_password,
                        captcha_id=f"test-captcha-{i}",
                        captcha_code="ABCD",
                        client_ip="127.0.0.1",
                    )
                )
            except AuthFailedException:
                pass

        # 刷新用户数据
        self.user.refresh_from_db()
        # 检查失败计数
        self.assertEqual(self.user.login_fail_count, 3)
        self.assertIsNone(self.user.lock_until)

    @patch("apps.users.services.CaptchaService.verify")
    def test_account_lock_after_5_failures(self, mock_verify):
        """测试 5 次失败后锁定账户"""
        mock_verify.return_value = True
        encrypted_password = sm4_encrypt("wrong_password")

        # 失败 5 次
        for i in range(5):
            try:
                run_async(
                    AuthService.login(
                        username=self.username,
                        encrypted_password=encrypted_password,
                        captcha_id=f"test-captcha-{i}",
                        captcha_code="ABCD",
                        client_ip="127.0.0.1",
                    )
                )
            except AuthFailedException:
                pass

        # 刷新用户数据
        self.user.refresh_from_db()
        # 应该被锁定（计数重置为 0）
        self.assertEqual(self.user.login_fail_count, 0)
        self.assertIsNotNone(self.user.lock_until)
        # 锁定时间应在未来 15 分钟左右
        self.assertTrue(self.user.lock_until > timezone.now())

    @patch("apps.users.services.CaptchaService.verify")
    def test_locked_account_cannot_login(self, mock_verify):
        """测试锁定账户无法登录"""
        mock_verify.return_value = True

        # 直接设置锁定状态
        self.user.lock_until = timezone.now() + timedelta(minutes=15)
        self.user.save()

        encrypted_password = sm4_encrypt(self.password)

        with self.assertRaises(AccountLockedException):
            run_async(
                AuthService.login(
                    username=self.username,
                    encrypted_password=encrypted_password,
                    captcha_id="test-captcha",
                    captcha_code="ABCD",
                    client_ip="127.0.0.1",
                )
            )

    @patch("apps.users.services.SSOService.invalidate_old_tokens")
    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_setex_json")
    @patch("apps.users.services.CaptchaService.verify")
    def test_fail_count_reset_on_success(
        self, mock_verify, mock_setex, mock_delete, mock_sso
    ):
        """测试登录成功后重置失败计数"""
        mock_verify.return_value = True
        mock_setex.return_value = True
        mock_delete.return_value = 1
        mock_sso.return_value = None

        # 先设置一些失败计数
        self.user.login_fail_count = 3
        self.user.save()

        encrypted_password = sm4_encrypt(self.password)

        run_async(
            AuthService.login(
                username=self.username,
                encrypted_password=encrypted_password,
                captcha_id="test-captcha",
                captcha_code="ABCD",
                client_ip="127.0.0.1",
            )
        )

        # 刷新用户数据
        self.user.refresh_from_db()
        # 失败计数应重置为 0
        self.assertEqual(self.user.login_fail_count, 0)
        self.assertIsNone(self.user.lock_until)

    @patch("apps.users.services.CaptchaService.verify")
    def test_lock_expires_after_15_minutes(self, mock_verify):
        """测试锁定 15 分钟后自动解除"""
        mock_verify.return_value = True

        # 设置锁定时间为过去
        self.user.lock_until = timezone.now() - timedelta(minutes=1)
        self.user.save()

        # 检查 is_locked 方法
        self.assertFalse(self.user.is_locked())


# ============ 单点登录测试 ============


@pytest.mark.django_db(transaction=True)
class TestSSOService(TransactionTestCase):
    """单点登录服务测试

    规则: R_SSO_001 - 新登录使旧 Token 失效
    """

    def setUp(self):
        """初始化测试数据"""
        self.user_id = 1

    @patch("apps.users.services.EventService.publish_logout_event")
    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_get")
    def test_invalidate_old_token(
        self, mock_get, mock_delete, mock_setex, mock_publish
    ):
        """测试使旧 Token 失效"""
        old_token_hash = "old_token_hash"
        new_token_hash = "new_token_hash"

        mock_get.return_value = old_token_hash  # 存在旧 Token
        mock_delete.return_value = 1
        mock_setex.return_value = True
        mock_publish.return_value = True

        run_async(SSOService.invalidate_old_tokens(self.user_id, new_token_hash))

        # 应删除旧 Token
        mock_delete.assert_called_once()
        # 应发送登出事件
        mock_publish.assert_called_once()
        # 应更新 Token 索引
        mock_setex.assert_called_once()

    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.redis_get")
    def test_no_old_token(self, mock_get, mock_setex):
        """测试无旧 Token 时不触发失效"""
        mock_get.return_value = None  # 无旧 Token
        mock_setex.return_value = True

        run_async(SSOService.invalidate_old_tokens(self.user_id, "new_token_hash"))

        # 应更新 Token 索引
        mock_setex.assert_called_once()

    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.redis_get")
    def test_same_token_no_invalidate(self, mock_get, mock_setex):
        """测试相同 Token 不触发失效"""
        token_hash = "same_token_hash"
        mock_get.return_value = token_hash
        mock_setex.return_value = True

        run_async(SSOService.invalidate_old_tokens(self.user_id, token_hash))

        # 应更新 Token 索引但不发送事件
        mock_setex.assert_called_once()


# ============ Token 双重过期机制测试 ============


class TestTokenExpiration(TestCase):
    """Token 双重过期机制测试

    规则: R_TOKEN_003 - 24小时绝对过期 + 1小时无操作过期
    """

    def test_idle_ttl_config(self):
        """测试无操作过期配置"""
        self.assertEqual(settings.AUTH_TOKEN_IDLE_TTL, 3600)  # 1小时

    def test_absolute_ttl_config(self):
        """测试绝对过期配置"""
        self.assertEqual(settings.AUTH_TOKEN_ABSOLUTE_TTL, 86400)  # 24小时

    def test_captcha_ttl_config(self):
        """测试验证码过期配置"""
        self.assertEqual(settings.AUTH_CAPTCHA_TTL, 120)  # 2分钟

    def test_fail_count_ttl_config(self):
        """测试失败计数过期配置"""
        self.assertEqual(settings.AUTH_FAIL_COUNT_TTL, 900)  # 15分钟

    def test_max_fail_count_config(self):
        """测试最大失败次数配置"""
        self.assertEqual(settings.AUTH_MAX_FAIL_COUNT, 5)

    def test_lock_duration_config(self):
        """测试锁定时间配置"""
        self.assertEqual(settings.AUTH_LOCK_DURATION, 900)  # 15分钟


# ============ 用户模型测试 ============


@pytest.mark.django_db(transaction=True)
class TestSysUserModel(TransactionTestCase):
    """用户模型测试"""

    def test_is_locked_true(self):
        """测试 is_locked - 已锁定"""
        user = SysUser(
            username="test",
            password_hash="hash",
            lock_until=timezone.now() + timedelta(minutes=10),
        )
        self.assertTrue(user.is_locked())

    def test_is_locked_false_expired(self):
        """测试 is_locked - 锁定已过期"""
        user = SysUser(
            username="test",
            password_hash="hash",
            lock_until=timezone.now() - timedelta(minutes=1),
        )
        self.assertFalse(user.is_locked())

    def test_is_locked_false_no_lock(self):
        """测试 is_locked - 未锁定"""
        user = SysUser(
            username="test",
            password_hash="hash",
            lock_until=None,
        )
        self.assertFalse(user.is_locked())

    def test_is_active_true(self):
        """测试 is_active - 已启用"""
        user = SysUser(username="test", password_hash="hash", status=1)
        self.assertTrue(user.is_active())

    def test_is_active_false(self):
        """测试 is_active - 已禁用"""
        user = SysUser(username="test", password_hash="hash", status=0)
        self.assertFalse(user.is_active())
