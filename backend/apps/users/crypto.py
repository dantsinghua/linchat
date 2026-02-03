"""国密算法封装 — SM3 哈希 / SM4 加密 / Token 工具"""
import base64
import hashlib
import logging
import secrets

from django.conf import settings
from gmssl import sm3, sm4

logger = logging.getLogger(__name__)


# ============ SM3 ============

def sm3_hash(data: str | bytes) -> str:
    """SM3 哈希，返回 64 位十六进制字符串"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return sm3.sm3_hash(list(data))


def verify_password(password: str, password_hash: str) -> bool:
    """常量时间密码比对"""
    return secrets.compare_digest(sm3_hash(password), password_hash)


# ============ SM4 ============

def _get_sm4_key() -> bytes:
    """获取 SM4 密钥（16 字节）"""
    key = settings.SM4_SECRET_KEY
    if isinstance(key, str):
        key = key.encode("utf-8")
    if len(key) < 16:
        key = key.ljust(16, b"\0")
    elif len(key) > 16:
        key = key[:16]
    return key


def sm4_encrypt(plaintext: str) -> str:
    """SM4-ECB 加密，返回 Base64 密文"""
    crypt = sm4.CryptSM4()
    crypt.set_key(_get_sm4_key(), sm4.SM4_ENCRYPT)
    ciphertext = crypt.crypt_ecb(plaintext.encode("utf-8"))
    return base64.b64encode(ciphertext).decode("utf-8")


def sm4_decrypt(ciphertext: str) -> str:
    """SM4-ECB 解密，失败抛 ValueError"""
    try:
        crypt = sm4.CryptSM4()
        crypt.set_key(_get_sm4_key(), sm4.SM4_DECRYPT)
        plaintext = crypt.crypt_ecb(base64.b64decode(ciphertext))
        return plaintext.decode("utf-8")
    except Exception as e:
        logger.warning(f"SM4 decryption failed: {e}")
        raise ValueError("解密失败")


def sm4_decrypt_safe(ciphertext: str) -> str | None:
    """SM4 安全解密（失败返回 None）"""
    try:
        return sm4_decrypt(ciphertext)
    except Exception:
        return None


# ============ Token ============

def generate_token_hash(token: str) -> str:
    """Token SHA256 哈希，用于 Redis 键名"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token(
    username: str, password: str, captcha_code: str, timestamp: int
) -> str:
    """生成 SM4 加密 Token: {username}|{password}|{captcha}|{timestamp}"""
    return sm4_encrypt(f"{username}|{password}|{captcha_code}|{timestamp}")
