"""
用户认证服务

参考:
- behavior-model.md#1.1 获取验证码（B_AUTH_001）
- behavior-model.md#1.2 用户登录（B_AUTH_002）
- behavior-model.md#1.4 单点登录Token失效（B_AUTH_004）
"""
import base64
import logging
import random
import string
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO

from captcha.image import ImageCaptcha
from django.conf import settings
from django.utils import timezone

from apps.common.event_service import EventService, LogoutReason
from apps.common.exceptions import (
    AccountLockedException,
    AuthFailedException,
    CaptchaInvalidException,
    UserDisabledException,
)  # UserDisabledException 用于账户禁用场景
from apps.users.crypto import (
    generate_token,
    generate_token_hash,
    sm3_hash,
    sm4_decrypt,
    verify_password,
)
from apps.users.models import SysUser
from apps.users.repositories import user_repo
from core.redis import (
    get_captcha_key,
    get_login_fail_key,
    get_token_key,
    get_user_token_key,
    redis_delete,
    redis_get,
    redis_set_json,
    redis_setex,
    redis_setex_json,
)

logger = logging.getLogger(__name__)


# ============ 数据类 ============


@dataclass
class CaptchaResult:
    """验证码生成结果"""

    captcha_id: str
    captcha_image: str  # Base64 编码的图片


@dataclass
class LoginResult:
    """登录结果"""

    token: str
    user_id: int
    username: str
    expire_time: datetime


# ============ 验证码服务 ============


class CaptchaService:
    """
    验证码服务

    参考: behavior-model.md#1.1 获取验证码（B_AUTH_001）
    """

    @staticmethod
    async def generate(width: int = 120, height: int = 40) -> CaptchaResult:
        """
        生成图形验证码

        参考: behavior-model.md#1.1
        规则: R_CAPTCHA_001 - 验证码有效期2分钟

        Args:
            width: 图片宽度
            height: 图片高度

        Returns:
            CaptchaResult: 包含 captcha_id 和 base64 图片
        """
        # 1. 生成4位随机验证码文本
        captcha_text = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=4)
        )

        # 2. 生成UUID作为captcha_id
        captcha_id = str(uuid.uuid4())

        # 3. 存入Redis，设置2分钟过期 [R_CAPTCHA_001]
        captcha_key = get_captcha_key(captcha_id)
        await redis_setex(captcha_key, settings.AUTH_CAPTCHA_TTL, captcha_text)

        # 4. 渲染图片并转Base64
        image = ImageCaptcha(width=width, height=height)
        buffer = BytesIO()
        image.write(captcha_text, buffer)
        base64_image = base64.b64encode(buffer.getvalue()).decode("utf-8")

        # 5. 返回结果
        return CaptchaResult(
            captcha_id=captcha_id,
            captcha_image=f"data:image/png;base64,{base64_image}",
        )

    @staticmethod
    async def verify(captcha_id: str, captcha_code: str) -> bool:
        """
        验证验证码

        参考: rule-model.md#R_CAPTCHA_002 - 验证码校验规则（一次性使用）

        Args:
            captcha_id: 验证码ID
            captcha_code: 用户输入的验证码

        Returns:
            bool: 验证是否通过

        Raises:
            CaptchaInvalidException: 验证码错误或已过期
        """
        captcha_key = get_captcha_key(captcha_id)
        cached = await redis_get(captcha_key)

        if not cached:
            raise CaptchaInvalidException("验证码已过期，请刷新")

        if cached.upper() != captcha_code.upper():
            raise CaptchaInvalidException("验证码错误")

        # [R_CAPTCHA_002] 一次性使用，验证后立即删除
        await redis_delete(captcha_key)
        return True


# ============ 认证服务 ============


class AuthService:
    """
    认证服务

    参考: behavior-model.md#1.2 用户登录（B_AUTH_002）
    """

    @staticmethod
    async def login(
        username: str,
        encrypted_password: str,
        captcha_id: str,
        captcha_code: str,
        client_ip: str,
    ) -> LoginResult:
        """
        用户登录

        参考: behavior-model.md#1.2
        规则:
        - R_CAPTCHA_002: 验证码一次性使用
        - R_LOGIN_001: 5次失败锁定15分钟
        - R_TOKEN_001: SM4加密Token
        - R_TOKEN_003: 双重过期机制

        Args:
            username: 用户名
            encrypted_password: SM4加密的密码
            captcha_id: 验证码ID
            captcha_code: 验证码
            client_ip: 客户端IP

        Returns:
            LoginResult: 登录结果

        Raises:
            CaptchaInvalidException: 验证码错误
            AccountLockedException: 账户已锁定
            AuthFailedException: 认证失败
        """
        # 1. [R_CAPTCHA_002] 验证码校验（一次性使用）
        await CaptchaService.verify(captcha_id, captcha_code)

        # 2. [R_LOGIN_001] 检查账户锁定状态
        user = await user_repo.find_by_username(username)
        if user and user.is_locked():
            remaining = int((user.lock_until - timezone.now()).total_seconds())
            remaining_minutes = remaining // 60 + 1
            raise AccountLockedException(
                f"账户已锁定，请{remaining_minutes}分钟后重试",
                remaining_seconds=remaining,
            )

        # 3. 查询用户
        if not user:
            await AuthService._handle_login_failure(username)
            raise AuthFailedException("用户名或密码错误")

        # 4. 检查用户状态
        # 参考: behavior-model.md#1.2 用户登录 - 账户禁用检查
        if not user.is_active():
            raise UserDisabledException("账户已被禁用")

        # 5. SM4解密密码并验证
        try:
            decrypted_password = sm4_decrypt(encrypted_password)
        except ValueError:
            raise AuthFailedException("密码格式错误")

        if not verify_password(decrypted_password, user.password_hash):
            await AuthService._handle_login_failure_with_user(user)
            raise AuthFailedException("用户名或密码错误")

        # 6. [R_TOKEN_001] 生成Token
        timestamp = int(timezone.now().timestamp())
        token = generate_token(username, encrypted_password, captcha_code, timestamp)
        token_hash = generate_token_hash(token)

        # 7. [R_SSO_001] 单点登录：使旧Token失效
        await SSOService.invalidate_old_tokens(user.user_id, token_hash)

        # 8. [R_TOKEN_003] Token存Redis，双重过期机制
        login_time = timezone.now()
        token_data = {
            "user_id": user.user_id,
            "username": user.username,
            "user_type": user.type,
            "login_time": login_time.isoformat(),
            "last_active_time": login_time.isoformat(),
            "login_ip": client_ip,
        }
        token_key = get_token_key(token_hash)
        await redis_setex_json(token_key, settings.AUTH_TOKEN_IDLE_TTL, token_data)

        # 9. 重置失败计数，更新登录信息（使用 UserRepository）
        await user_repo.update_login_info(user, login_time, client_ip)

        # 清除失败计数
        await redis_delete(get_login_fail_key(username))

        logger.info(f"User {username} logged in successfully from {client_ip}")

        return LoginResult(
            token=token,
            user_id=user.user_id,
            username=user.username,
            expire_time=login_time + timedelta(seconds=settings.AUTH_TOKEN_IDLE_TTL),
        )

    @staticmethod
    async def logout(user_id: int, token_hash: str) -> bool:
        """
        用户登出

        Args:
            user_id: 用户ID
            token_hash: Token哈希

        Returns:
            bool: 是否成功
        """
        # 删除Token
        token_key = get_token_key(token_hash)
        await redis_delete(token_key)

        # 清除用户Token索引
        user_token_key = get_user_token_key(user_id)
        await redis_delete(user_token_key)

        logger.info(f"User {user_id} logged out")
        return True

    @staticmethod
    async def _handle_login_failure(username: str) -> None:
        """
        处理登录失败（用户不存在的情况）

        使用Redis计数，防止用户名枚举
        """
        fail_key = get_login_fail_key(username)
        count = await redis_get(fail_key)
        count = int(count) + 1 if count else 1
        await redis_setex(fail_key, settings.AUTH_FAIL_COUNT_TTL, str(count))

    @staticmethod
    async def _handle_login_failure_with_user(user: SysUser) -> None:
        """
        处理登录失败（用户存在的情况）

        参考: rule-model.md#R_LOGIN_001 - 5次失败锁定15分钟
        使用 UserRepository 保持逻辑一致性
        """
        # 计算是否需要锁定
        lock_until = None
        if user.login_fail_count + 1 >= settings.AUTH_MAX_FAIL_COUNT:
            lock_until = timezone.now() + timedelta(
                seconds=settings.AUTH_LOCK_DURATION
            )
            logger.warning(f"User {user.username} account locked due to too many failed attempts")

        # 使用 UserRepository 更新失败计数
        await user_repo.increment_fail_count(user, lock_until)


# ============ 单点登录服务 ============


class SSOService:
    """
    单点登录服务

    参考: behavior-model.md#1.4 单点登录Token失效（B_AUTH_004）
    规则: R_SSO_001 - 单点登录机制
    """

    @staticmethod
    async def invalidate_old_tokens(user_id: int, new_token_hash: str) -> None:
        """
        使用户的旧Token失效

        新登录时调用，实现单点登录

        Args:
            user_id: 用户ID
            new_token_hash: 新Token的哈希值
        """
        # 1. 查询用户当前活跃Token
        index_key = get_user_token_key(user_id)
        old_token_hash = await redis_get(index_key)

        # 2. 如果存在旧Token且不同于新Token，删除旧Token并发送SSE通知
        if old_token_hash and old_token_hash != new_token_hash:
            # 删除旧Token
            await redis_delete(get_token_key(old_token_hash))
            logger.info(f"Invalidated old token for user {user_id}")

            # 发送SSE登出事件通知旧会话
            await EventService.publish_logout_event(user_id, LogoutReason.SSO_CONFLICT)

        # 3. 更新Token索引为新Token（TTL与Token绝对过期一致：24小时）
        await redis_setex(index_key, settings.AUTH_TOKEN_ABSOLUTE_TTL, new_token_hash)
