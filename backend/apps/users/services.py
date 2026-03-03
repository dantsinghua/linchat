import base64
import logging
import random
import string
import uuid
from datetime import timedelta
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
)
from apps.users.crypto import (
    generate_token,
    generate_token_hash,
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
    redis_setex,
    redis_setex_json,
)

logger = logging.getLogger(__name__)


class CaptchaService:

    @staticmethod
    async def generate(width: int = 120, height: int = 40) -> dict:
        text = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        captcha_id = str(uuid.uuid4())
        await redis_setex(get_captcha_key(captcha_id), settings.AUTH_CAPTCHA_TTL, text)
        buf = BytesIO()
        ImageCaptcha(width=width, height=height).write(text, buf)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return {"captcha_id": captcha_id, "captcha_image": f"data:image/png;base64,{b64}"}

    @staticmethod
    async def verify(captcha_id: str, captcha_code: str) -> bool:
        key = get_captcha_key(captcha_id)
        cached = await redis_get(key)
        if not cached: raise CaptchaInvalidException("验证码已过期，请刷新")
        if cached.upper() != captcha_code.upper(): raise CaptchaInvalidException("验证码错误")
        await redis_delete(key)
        return True


class AuthService:

    @staticmethod
    async def login(
        username: str, encrypted_password: str,
        captcha_id: str, captcha_code: str, client_ip: str,
    ) -> dict:
        await CaptchaService.verify(captcha_id, captcha_code)
        user = await user_repo.find_by_username(username)
        if user and user.is_locked():
            remaining = int((user.lock_until - timezone.now()).total_seconds())
            raise AccountLockedException(
                f"账户已锁定，请{remaining // 60 + 1}分钟后重试", remaining_seconds=remaining,
            )
        if not user:
            await AuthService._handle_login_failure(username)
            raise AuthFailedException("用户名或密码错误")
        if not user.is_active(): raise UserDisabledException("账户已被禁用")
        try:
            decrypted = sm4_decrypt(encrypted_password)
        except ValueError:
            raise AuthFailedException("密码格式错误")
        if not verify_password(decrypted, user.password_hash):
            await AuthService._handle_login_failure_with_user(user)
            raise AuthFailedException("用户名或密码错误")
        ts = int(timezone.now().timestamp())
        token = generate_token(username, encrypted_password, captcha_code, ts)
        token_hash = generate_token_hash(token)
        await AuthService._invalidate_old_tokens(user.user_id, token_hash)
        login_time = timezone.now()
        token_data = {
            "user_id": user.user_id, "username": user.username,
            "user_type": user.type, "login_time": login_time.isoformat(),
            "last_active_time": login_time.isoformat(), "login_ip": client_ip,
        }
        await redis_setex_json(get_token_key(token_hash), settings.AUTH_TOKEN_IDLE_TTL, token_data)
        await user_repo.update_login_info(user, login_time, client_ip)
        await redis_delete(get_login_fail_key(username))
        logger.info(f"User {username} logged in successfully from {client_ip}")
        return {
            "token": token, "user_id": user.user_id, "username": user.username,
            "expire_time": login_time + timedelta(seconds=settings.AUTH_TOKEN_IDLE_TTL),
        }

    @staticmethod
    async def logout(user_id: int, token_hash: str) -> bool:
        await redis_delete(get_token_key(token_hash))
        await redis_delete(get_user_token_key(user_id))
        logger.info(f"User {user_id} logged out")
        return True

    @staticmethod
    async def _handle_login_failure(username: str) -> None:
        fail_key = get_login_fail_key(username)
        count = await redis_get(fail_key)
        count = int(count) + 1 if count else 1
        await redis_setex(fail_key, settings.AUTH_FAIL_COUNT_TTL, str(count))

    @staticmethod
    async def _handle_login_failure_with_user(user: SysUser) -> None:
        lock_until = None
        if user.login_fail_count + 1 >= settings.AUTH_MAX_FAIL_COUNT:
            lock_until = timezone.now() + timedelta(seconds=settings.AUTH_LOCK_DURATION)
            logger.warning(f"User {user.username} account locked")
        await user_repo.increment_fail_count(user, lock_until)

    @staticmethod
    async def _invalidate_old_tokens(user_id: int, new_token_hash: str) -> None:
        index_key = get_user_token_key(user_id)
        old_hash = await redis_get(index_key)
        if old_hash and old_hash != new_token_hash:
            await redis_delete(get_token_key(old_hash))
            logger.info(f"Invalidated old token for user {user_id}")
            await EventService.publish_logout_event(user_id, LogoutReason.SSO_CONFLICT)
        await redis_setex(index_key, settings.AUTH_TOKEN_ABSOLUTE_TTL, new_token_hash)
