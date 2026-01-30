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
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import TestCase, TransactionTestCase
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
    sm3_hash,
    sm4_decrypt,
    sm4_decrypt_safe,
    sm4_encrypt,
    verify_password,
)
from apps.users.models import SysUser
from apps.users.services import AuthService, CaptchaService


def run_async(coro):
    """运行异步函数"""
    return asyncio.get_event_loop().run_until_complete(coro)


# ============ Crypto 模块测试 ============


class TestCryptoModule(TestCase):
    """国密算法测试"""

    def test_sm3_hash_string(self):
        password = "!9871229Qing"
        hash_value = sm3_hash(password)
        self.assertEqual(len(hash_value), 64)
        self.assertEqual(hash_value, sm3_hash(password))

    def test_sm3_hash_bytes(self):
        hash_value = sm3_hash(b"!9871229Qing")
        self.assertEqual(len(hash_value), 64)

    def test_verify_password_success(self):
        password = "!9871229Qing"
        self.assertTrue(verify_password(password, sm3_hash(password)))

    def test_verify_password_failure(self):
        self.assertFalse(verify_password("wrong", sm3_hash("!9871229Qing")))

    def test_sm4_encrypt_decrypt(self):
        plaintext = "test_password_123"
        ciphertext = sm4_encrypt(plaintext)
        self.assertNotEqual(ciphertext, plaintext)
        self.assertEqual(sm4_decrypt(ciphertext), plaintext)

    def test_sm4_decrypt_invalid(self):
        with self.assertRaises(ValueError):
            sm4_decrypt("invalid_ciphertext")

    def test_sm4_decrypt_safe_success(self):
        plaintext = "test_data"
        self.assertEqual(sm4_decrypt_safe(sm4_encrypt(plaintext)), plaintext)

    def test_sm4_decrypt_safe_failure(self):
        self.assertIsNone(sm4_decrypt_safe("invalid_ciphertext"))

    def test_generate_token(self):
        username = "admin"
        password = sm4_encrypt("!9871229Qing")
        captcha = "ABCD"
        timestamp = int(timezone.now().timestamp())
        token = generate_token(username, password, captcha, timestamp)
        self.assertIsInstance(token, str)

    def test_generate_token_hash(self):
        token_hash = generate_token_hash("test_token_string")
        self.assertEqual(len(token_hash), 64)
        self.assertEqual(token_hash, generate_token_hash("test_token_string"))

    @patch("apps.users.crypto.settings")
    def test_sm4_key_short(self, mock_settings):
        mock_settings.SM4_SECRET_KEY = "short"
        from apps.users.crypto import _get_sm4_key
        self.assertEqual(len(_get_sm4_key()), 16)

    @patch("apps.users.crypto.settings")
    def test_sm4_key_long(self, mock_settings):
        mock_settings.SM4_SECRET_KEY = "this_is_a_very_long_key_more_than_16_bytes"
        from apps.users.crypto import _get_sm4_key
        self.assertEqual(len(_get_sm4_key()), 16)


# ============ CaptchaService 测试 ============


class TestCaptchaService(TestCase):

    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.get_captcha_key")
    def test_generate_captcha(self, mock_key, mock_setex):
        mock_key.return_value = "auth:captcha:test-id"
        mock_setex.return_value = True
        result = run_async(CaptchaService.generate())
        self.assertIn("captcha_id", result)
        self.assertTrue(result["captcha_image"].startswith("data:image/png;base64,"))
        mock_setex.assert_called_once()

    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.get_captcha_key")
    def test_generate_captcha_custom_size(self, mock_key, mock_setex):
        mock_key.return_value = "auth:captcha:test-id"
        mock_setex.return_value = True
        result = run_async(CaptchaService.generate(width=150, height=50))
        self.assertIn("captcha_id", result)

    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_get")
    @patch("apps.users.services.get_captcha_key")
    def test_verify_captcha_success(self, mock_key, mock_get, mock_delete):
        mock_key.return_value = "auth:captcha:test-id"
        mock_get.return_value = "ABCD"
        mock_delete.return_value = 1
        result = run_async(CaptchaService.verify("test-id", "abcd"))
        self.assertTrue(result)
        mock_delete.assert_called_once()

    @patch("apps.users.services.redis_get")
    @patch("apps.users.services.get_captcha_key")
    def test_verify_captcha_expired(self, mock_key, mock_get):
        mock_key.return_value = "auth:captcha:test-id"
        mock_get.return_value = None
        with self.assertRaises(CaptchaInvalidException) as ctx:
            run_async(CaptchaService.verify("test-id", "ABCD"))
        self.assertIn("已过期", str(ctx.exception.message))

    @patch("apps.users.services.redis_get")
    @patch("apps.users.services.get_captcha_key")
    def test_verify_captcha_wrong_code(self, mock_key, mock_get):
        mock_key.return_value = "auth:captcha:test-id"
        mock_get.return_value = "ABCD"
        with self.assertRaises(CaptchaInvalidException) as ctx:
            run_async(CaptchaService.verify("test-id", "WXYZ"))
        self.assertIn("错误", str(ctx.exception.message))

    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_get")
    @patch("apps.users.services.get_captcha_key")
    def test_verify_captcha_case_insensitive(self, mock_key, mock_get, mock_delete):
        mock_key.return_value = "auth:captcha:test-id"
        mock_get.return_value = "ABCD"
        mock_delete.return_value = 1
        result = run_async(CaptchaService.verify("test-id", "abcd"))
        self.assertTrue(result)


# ============ AuthService 测试 ============


@pytest.mark.django_db(transaction=True)
class TestAuthService(TransactionTestCase):

    def setUp(self):
        self.username = "testuser"
        self.password = "Test@123456"
        self.password_hash = sm3_hash(self.password)
        self.user = SysUser.objects.create(
            username=self.username, password_hash=self.password_hash, status=1
        )

    def tearDown(self):
        SysUser.objects.all().delete()

    @patch("apps.users.services.AuthService._invalidate_old_tokens")
    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_setex_json")
    @patch("apps.users.services.CaptchaService.verify")
    def test_login_success(self, mock_verify, mock_setex, mock_delete, mock_sso):
        mock_verify.return_value = True
        mock_setex.return_value = True
        mock_delete.return_value = 1
        mock_sso.return_value = None

        result = run_async(
            AuthService.login(
                username=self.username,
                encrypted_password=sm4_encrypt(self.password),
                captcha_id="test-captcha-id",
                captcha_code="ABCD",
                client_ip="127.0.0.1",
            )
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result["username"], self.username)
        self.assertIn("token", result)
        mock_verify.assert_called_once()
        mock_sso.assert_called_once()

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_user_not_found(self, mock_verify):
        mock_verify.return_value = True
        with patch("apps.users.services.redis_setex") as mock_setex:
            mock_setex.return_value = True
            with self.assertRaises(AuthFailedException):
                run_async(
                    AuthService.login(
                        username="nonexistent",
                        encrypted_password=sm4_encrypt("password"),
                        captcha_id="test-captcha-id",
                        captcha_code="ABCD",
                        client_ip="127.0.0.1",
                    )
                )

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_wrong_password(self, mock_verify):
        mock_verify.return_value = True
        with self.assertRaises(AuthFailedException):
            run_async(
                AuthService.login(
                    username=self.username,
                    encrypted_password=sm4_encrypt("wrong_password"),
                    captcha_id="test-captcha-id",
                    captcha_code="ABCD",
                    client_ip="127.0.0.1",
                )
            )

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_disabled_user(self, mock_verify):
        mock_verify.return_value = True
        self.user.status = 0
        self.user.save()
        with self.assertRaises(UserDisabledException):
            run_async(
                AuthService.login(
                    username=self.username,
                    encrypted_password=sm4_encrypt(self.password),
                    captcha_id="test-captcha-id",
                    captcha_code="ABCD",
                    client_ip="127.0.0.1",
                )
            )

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_locked_account(self, mock_verify):
        mock_verify.return_value = True
        self.user.lock_until = timezone.now() + timedelta(minutes=10)
        self.user.save()
        with self.assertRaises(AccountLockedException) as ctx:
            run_async(
                AuthService.login(
                    username=self.username,
                    encrypted_password=sm4_encrypt(self.password),
                    captcha_id="test-captcha-id",
                    captcha_code="ABCD",
                    client_ip="127.0.0.1",
                )
            )
        self.assertIn("锁定", str(ctx.exception.message))

    @patch("apps.users.services.CaptchaService.verify")
    def test_login_invalid_password_format(self, mock_verify):
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
        mock_delete.return_value = 1
        result = run_async(AuthService.logout(self.user.user_id, "test_token_hash"))
        self.assertTrue(result)
        self.assertEqual(mock_delete.call_count, 2)


# ============ 登录失败锁定测试 ============


@pytest.mark.django_db(transaction=True)
class TestLoginLockout(TransactionTestCase):

    def setUp(self):
        self.username = "locktest"
        self.password = "Test@123456"
        self.user = SysUser.objects.create(
            username=self.username, password_hash=sm3_hash(self.password), status=1
        )

    def tearDown(self):
        SysUser.objects.all().delete()

    @patch("apps.users.services.CaptchaService.verify")
    def test_fail_count_increment(self, mock_verify):
        mock_verify.return_value = True
        encrypted = sm4_encrypt("wrong_password")
        for i in range(3):
            try:
                run_async(
                    AuthService.login(
                        username=self.username, encrypted_password=encrypted,
                        captcha_id=f"test-captcha-{i}", captcha_code="ABCD",
                        client_ip="127.0.0.1",
                    )
                )
            except AuthFailedException:
                pass
        self.user.refresh_from_db()
        self.assertEqual(self.user.login_fail_count, 3)
        self.assertIsNone(self.user.lock_until)

    @patch("apps.users.services.CaptchaService.verify")
    def test_account_lock_after_5_failures(self, mock_verify):
        mock_verify.return_value = True
        encrypted = sm4_encrypt("wrong_password")
        for i in range(5):
            try:
                run_async(
                    AuthService.login(
                        username=self.username, encrypted_password=encrypted,
                        captcha_id=f"test-captcha-{i}", captcha_code="ABCD",
                        client_ip="127.0.0.1",
                    )
                )
            except AuthFailedException:
                pass
        self.user.refresh_from_db()
        self.assertEqual(self.user.login_fail_count, 0)
        self.assertIsNotNone(self.user.lock_until)
        self.assertTrue(self.user.lock_until > timezone.now())

    @patch("apps.users.services.CaptchaService.verify")
    def test_locked_account_cannot_login(self, mock_verify):
        mock_verify.return_value = True
        self.user.lock_until = timezone.now() + timedelta(minutes=15)
        self.user.save()
        with self.assertRaises(AccountLockedException):
            run_async(
                AuthService.login(
                    username=self.username,
                    encrypted_password=sm4_encrypt(self.password),
                    captcha_id="test-captcha", captcha_code="ABCD",
                    client_ip="127.0.0.1",
                )
            )

    @patch("apps.users.services.AuthService._invalidate_old_tokens")
    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_setex_json")
    @patch("apps.users.services.CaptchaService.verify")
    def test_fail_count_reset_on_success(self, mock_verify, mock_setex, mock_delete, mock_sso):
        mock_verify.return_value = True
        mock_setex.return_value = True
        mock_delete.return_value = 1
        mock_sso.return_value = None
        self.user.login_fail_count = 3
        self.user.save()
        run_async(
            AuthService.login(
                username=self.username,
                encrypted_password=sm4_encrypt(self.password),
                captcha_id="test-captcha", captcha_code="ABCD",
                client_ip="127.0.0.1",
            )
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.login_fail_count, 0)
        self.assertIsNone(self.user.lock_until)

    @patch("apps.users.services.CaptchaService.verify")
    def test_lock_expires_after_15_minutes(self, mock_verify):
        mock_verify.return_value = True
        self.user.lock_until = timezone.now() - timedelta(minutes=1)
        self.user.save()
        self.assertFalse(self.user.is_locked())


# ============ SSO (内联到 AuthService) 测试 ============


@pytest.mark.django_db(transaction=True)
class TestSSOInvalidation(TransactionTestCase):

    def setUp(self):
        self.user_id = 1

    @patch("apps.users.services.EventService.publish_logout_event")
    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.redis_delete")
    @patch("apps.users.services.redis_get")
    def test_invalidate_old_token(self, mock_get, mock_delete, mock_setex, mock_publish):
        mock_get.return_value = "old_token_hash"
        mock_delete.return_value = 1
        mock_setex.return_value = True
        mock_publish.return_value = True
        run_async(AuthService._invalidate_old_tokens(self.user_id, "new_token_hash"))
        mock_delete.assert_called_once()
        mock_publish.assert_called_once()
        mock_setex.assert_called_once()

    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.redis_get")
    def test_no_old_token(self, mock_get, mock_setex):
        mock_get.return_value = None
        mock_setex.return_value = True
        run_async(AuthService._invalidate_old_tokens(self.user_id, "new_token_hash"))
        mock_setex.assert_called_once()

    @patch("apps.users.services.redis_setex")
    @patch("apps.users.services.redis_get")
    def test_same_token_no_invalidate(self, mock_get, mock_setex):
        token_hash = "same_token_hash"
        mock_get.return_value = token_hash
        mock_setex.return_value = True
        run_async(AuthService._invalidate_old_tokens(self.user_id, token_hash))
        mock_setex.assert_called_once()


# ============ Token 配置测试 ============


class TestTokenExpiration(TestCase):

    def test_idle_ttl_config(self):
        self.assertEqual(settings.AUTH_TOKEN_IDLE_TTL, 3600)

    def test_absolute_ttl_config(self):
        self.assertEqual(settings.AUTH_TOKEN_ABSOLUTE_TTL, 86400)

    def test_captcha_ttl_config(self):
        self.assertEqual(settings.AUTH_CAPTCHA_TTL, 120)

    def test_fail_count_ttl_config(self):
        self.assertEqual(settings.AUTH_FAIL_COUNT_TTL, 900)

    def test_max_fail_count_config(self):
        self.assertEqual(settings.AUTH_MAX_FAIL_COUNT, 5)

    def test_lock_duration_config(self):
        self.assertEqual(settings.AUTH_LOCK_DURATION, 900)


# ============ 用户模型测试 ============


@pytest.mark.django_db(transaction=True)
class TestSysUserModel(TransactionTestCase):

    def test_is_locked_true(self):
        user = SysUser(username="test", password_hash="h", lock_until=timezone.now() + timedelta(minutes=10))
        self.assertTrue(user.is_locked())

    def test_is_locked_false_expired(self):
        user = SysUser(username="test", password_hash="h", lock_until=timezone.now() - timedelta(minutes=1))
        self.assertFalse(user.is_locked())

    def test_is_locked_false_no_lock(self):
        user = SysUser(username="test", password_hash="h", lock_until=None)
        self.assertFalse(user.is_locked())

    def test_is_active_true(self):
        self.assertTrue(SysUser(username="t", password_hash="h", status=1).is_active())

    def test_is_active_false(self):
        self.assertFalse(SysUser(username="t", password_hash="h", status=0).is_active())
