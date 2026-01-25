"""
国密算法封装

参考:
- constitution.md#4.2 数据保护
- behavior-model.md#1.2 用户登录 - SM4解密密码、SM3比对哈希流程
"""
import base64
import hashlib
import logging
import secrets
from typing import Optional

from django.conf import settings
from gmssl import sm3, sm4

logger = logging.getLogger(__name__)


# ============ SM3 哈希 ============

def sm3_hash(data: str | bytes) -> str:
    """
    SM3 哈希计算

    用于密码哈希存储
    参考: constitution.md#4.2 密码国密SM3哈希

    Args:
        data: 待哈希的数据

    Returns:
        64位十六进制哈希值字符串
    """
    if isinstance(data, str):
        data = data.encode("utf-8")

    # sm3.sm3_hash 接受整数列表
    data_list = list(data)
    hash_value = sm3.sm3_hash(data_list)

    return hash_value


def verify_password(password: str, password_hash: str) -> bool:
    """
    验证密码

    Args:
        password: 明文密码
        password_hash: 存储的SM3哈希值

    Returns:
        密码是否匹配
    """
    computed_hash = sm3_hash(password)
    # 使用安全的常量时间比较防止时序攻击
    return secrets.compare_digest(computed_hash, password_hash)


# ============ SM4 加密/解密 ============

def _get_sm4_key() -> bytes:
    """获取 SM4 密钥（16字节）"""
    key = settings.SM4_SECRET_KEY
    if isinstance(key, str):
        key = key.encode("utf-8")

    # 确保密钥长度为16字节
    if len(key) < 16:
        key = key.ljust(16, b"\0")
    elif len(key) > 16:
        key = key[:16]

    return key


def sm4_encrypt(plaintext: str) -> str:
    """
    SM4 加密

    用于 Token 生成
    参考: rule-model.md#R_TOKEN_001 Token生成规则

    Args:
        plaintext: 明文字符串

    Returns:
        Base64 编码的密文
    """
    key = _get_sm4_key()
    crypt_sm4 = sm4.CryptSM4()
    crypt_sm4.set_key(key, sm4.SM4_ENCRYPT)

    # 将明文转为字节
    plaintext_bytes = plaintext.encode("utf-8")

    # gmssl 的 crypt_ecb 会自动处理 PKCS7 填充，无需手动填充
    ciphertext = crypt_sm4.crypt_ecb(plaintext_bytes)

    # Base64 编码
    return base64.b64encode(ciphertext).decode("utf-8")


def sm4_decrypt(ciphertext: str) -> str:
    """
    SM4 解密

    用于 Token 验证和密码解密
    参考: behavior-model.md#1.2 用户登录 - SM4解密密码

    Args:
        ciphertext: Base64 编码的密文

    Returns:
        解密后的明文字符串

    Raises:
        ValueError: 密文格式错误或解密失败
    """
    try:
        key = _get_sm4_key()
        crypt_sm4 = sm4.CryptSM4()
        crypt_sm4.set_key(key, sm4.SM4_DECRYPT)

        # Base64 解码
        ciphertext_bytes = base64.b64decode(ciphertext)

        # gmssl 的 crypt_ecb 会自动处理 PKCS7 去填充
        plaintext_bytes = crypt_sm4.crypt_ecb(ciphertext_bytes)

        return plaintext_bytes.decode("utf-8")

    except Exception as e:
        logger.warning(f"SM4 decryption failed: {e}")
        raise ValueError("解密失败")


def sm4_encrypt_safe(plaintext: str) -> Optional[str]:
    """
    SM4 安全加密（不抛出异常）

    Args:
        plaintext: 明文字符串

    Returns:
        Base64 编码的密文，失败返回 None
    """
    try:
        return sm4_encrypt(plaintext)
    except Exception as e:
        logger.error(f"SM4 encryption failed: {e}")
        return None


def sm4_decrypt_safe(ciphertext: str) -> Optional[str]:
    """
    SM4 安全解密（不抛出异常）

    Args:
        ciphertext: Base64 编码的密文

    Returns:
        解密后的明文字符串，失败返回 None
    """
    try:
        return sm4_decrypt(ciphertext)
    except Exception as e:
        logger.warning(f"SM4 decryption failed: {e}")
        return None


# ============ Token 相关 ============

def generate_token_hash(token: str) -> str:
    """
    生成 Token 的 SHA256 哈希值

    用于 Redis 键名，避免存储原始 Token
    参考: data-model.md 术语定义 - token_hash

    Args:
        token: 原始 Token 字符串

    Returns:
        64位十六进制哈希值
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token(
    username: str,
    password: str,
    captcha_code: str,
    timestamp: int,
) -> str:
    """
    生成登录 Token

    参考: rule-model.md#R_TOKEN_001 Token生成规则
    格式: SM4({username}|{password}|{captcha}|{timestamp})

    Args:
        username: 用户名
        password: 加密后的密码
        captcha_code: 验证码（防重放）
        timestamp: 时间戳

    Returns:
        SM4 加密后的 Token
    """
    token_data = f"{username}|{password}|{captcha_code}|{timestamp}"
    return sm4_encrypt(token_data)


def parse_token(token: str) -> Optional[dict]:
    """
    解析 Token 内容

    Args:
        token: SM4 加密的 Token

    Returns:
        解析后的字典 {username, password, captcha_code, timestamp}，失败返回 None
    """
    try:
        decrypted = sm4_decrypt(token)
        parts = decrypted.split("|")
        if len(parts) != 4:
            return None

        return {
            "username": parts[0],
            "password": parts[1],
            "captcha_code": parts[2],
            "timestamp": int(parts[3]),
        }
    except Exception as e:
        logger.warning(f"Token parsing failed: {e}")
        return None
