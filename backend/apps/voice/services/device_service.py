"""设备管理服务

参考:
- specs/009-voice-interaction/data-model.md#2.3 RegisteredDevice
- 宪法 2.1：服务层封装所有业务逻辑
- 宪法 4.1：SM4 加密存储设备 API Token

职责：
- 外部设备注册与 Token 生成
- 设备停用 / 删除
- API Token 认证（SM4 解密 + 全量比对）
- 设备列表查询
"""

import logging
import secrets
import uuid
from typing import Optional

from apps.users.crypto import sm4_decrypt, sm4_encrypt
from apps.voice.repositories import registered_device_repo

logger = logging.getLogger(__name__)


class DeviceService:
    """外部设备管理服务"""

    async def register_device(self, user_id: int, name: str) -> dict:
        """注册新设备，生成 API Token

        Args:
            user_id: 用户 ID（隔离粒度）
            name: 设备显示名称

        Returns:
            包含 device_uuid、name、api_token（明文，仅此一次可见）的字典
        """
        device_uuid = str(uuid.uuid4())
        plain_token = secrets.token_urlsafe(32)
        token_prefix = plain_token[:8]
        api_token_encrypted = sm4_encrypt(plain_token)

        device = await registered_device_repo.create(
            device_uuid=device_uuid,
            user_id=user_id,
            name=name,
            api_token_encrypted=api_token_encrypted,
            token_prefix=token_prefix,
        )

        logger.info(
            "Device registered: user_id=%s, device_uuid=%s, name=%s",
            user_id,
            device_uuid,
            name,
        )

        return {
            "device_uuid": device.device_uuid,
            "name": device.name,
            "api_token": plain_token,
        }

    async def revoke_device(self, user_id: int, device_uuid: str) -> bool:
        """停用设备（设置 is_active=False）

        Args:
            user_id: 用户 ID（隔离粒度）
            device_uuid: 设备公开标识

        Returns:
            True 停用成功，False 设备不存在或已停用
        """
        count = await registered_device_repo.deactivate(device_uuid, user_id)
        if count > 0:
            logger.info(
                "Device revoked: user_id=%s, device_uuid=%s",
                user_id,
                device_uuid,
            )
            return True

        logger.warning(
            "Device revoke failed (not found or already inactive): "
            "user_id=%s, device_uuid=%s",
            user_id,
            device_uuid,
        )
        return False

    async def delete_device(self, user_id: int, device_uuid: str) -> bool:
        """删除设备（物理删除）

        Args:
            user_id: 用户 ID（隔离粒度）
            device_uuid: 设备公开标识

        Returns:
            True 删除成功，False 设备不存在
        """
        count = await registered_device_repo.delete_by_uuid(
            device_uuid, user_id
        )
        if count > 0:
            logger.info(
                "Device deleted: user_id=%s, device_uuid=%s",
                user_id,
                device_uuid,
            )
            return True

        logger.warning(
            "Device delete failed (not found): user_id=%s, device_uuid=%s",
            user_id,
            device_uuid,
        )
        return False

    async def authenticate_by_token(
        self, raw_token: str
    ) -> Optional[dict]:
        """通过 API Token 认证设备

        流程：取 Token 前 8 位作为前缀快速筛选候选设备，
        再逐一 SM4 解密全量比对，命中后更新 last_active_at。

        Args:
            raw_token: 客户端传入的明文 API Token

        Returns:
            认证成功返回 {"user_id", "device_uuid", "device_name"}，
            失败返回 None
        """
        if not raw_token or len(raw_token) < 8:
            logger.warning("Token authentication failed: token too short")
            return None

        prefix = raw_token[:8]
        candidates = await registered_device_repo.find_by_token_prefix(prefix)

        if not candidates:
            logger.warning(
                "Token authentication failed: no device with prefix=%s",
                prefix,
            )
            return None

        for device in candidates:
            try:
                decrypted = sm4_decrypt(device.api_token_encrypted)
            except (ValueError, Exception):
                logger.warning(
                    "SM4 decrypt failed for device: device_uuid=%s",
                    device.device_uuid,
                )
                continue

            if decrypted == raw_token:
                # 认证成功，更新最后活跃时间
                await registered_device_repo.update_last_active(device.pk)
                logger.info(
                    "Device authenticated: user_id=%s, device_uuid=%s",
                    device.user_id,
                    device.device_uuid,
                )
                return {
                    "user_id": device.user_id,
                    "device_uuid": device.device_uuid,
                    "device_name": device.name,
                }

        logger.warning(
            "Token authentication failed: no matching token, prefix=%s, "
            "candidates=%d",
            prefix,
            len(candidates),
        )
        return None

    async def list_devices(self, user_id: int) -> list[dict]:
        """列出用户的所有注册设备

        Args:
            user_id: 用户 ID（隔离粒度）

        Returns:
            设备基本信息列表（不含加密 Token）
        """
        devices = await registered_device_repo.find_by_user_id(user_id)
        return [
            {
                "device_uuid": d.device_uuid,
                "name": d.name,
                "is_active": d.is_active,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "last_active_at": (
                    d.last_active_at.isoformat() if d.last_active_at else None
                ),
            }
            for d in devices
        ]


# 全局单例
device_service = DeviceService()
