import logging
import secrets
import uuid
from typing import Optional

from apps.users.crypto import sm4_decrypt, sm4_encrypt
from apps.voice.repositories import registered_device_repo

logger = logging.getLogger(__name__)


class DeviceService:

    async def register_device(self, user_id: int, name: str) -> dict:
        device_uuid = str(uuid.uuid4())
        plain_token = secrets.token_urlsafe(32)
        token_prefix = plain_token[:8]
        api_token_encrypted = sm4_encrypt(plain_token)
        device = await registered_device_repo.create(
            device_uuid=device_uuid, user_id=user_id, name=name,
            api_token_encrypted=api_token_encrypted, token_prefix=token_prefix,
        )
        logger.info("Device registered: user_id=%s, device_uuid=%s, name=%s", user_id, device_uuid, name)
        return {"device_uuid": device.device_uuid, "name": device.name, "api_token": plain_token}

    async def revoke_device(self, user_id: int, device_uuid: str) -> bool:
        count = await registered_device_repo.deactivate(device_uuid, user_id)
        if count > 0:
            logger.info("Device revoked: user_id=%s, device_uuid=%s", user_id, device_uuid)
            return True
        logger.warning("Device revoke failed (not found or already inactive): user_id=%s, device_uuid=%s", user_id, device_uuid)
        return False

    async def delete_device(self, user_id: int, device_uuid: str) -> bool:
        count = await registered_device_repo.delete_by_uuid(device_uuid, user_id)
        if count > 0:
            logger.info("Device deleted: user_id=%s, device_uuid=%s", user_id, device_uuid)
            return True
        logger.warning("Device delete failed (not found): user_id=%s, device_uuid=%s", user_id, device_uuid)
        return False

    async def authenticate_by_token(self, raw_token: str) -> Optional[dict]:
        if not raw_token or len(raw_token) < 8:
            logger.warning("Token authentication failed: token too short")
            return None
        prefix = raw_token[:8]
        candidates = await registered_device_repo.find_by_token_prefix(prefix)
        if not candidates:
            logger.warning("Token authentication failed: no device with prefix=%s", prefix)
            return None
        for device in candidates:
            try:
                decrypted = sm4_decrypt(device.api_token_encrypted)
            except (ValueError, Exception):
                logger.warning("SM4 decrypt failed for device: device_uuid=%s", device.device_uuid)
                continue
            if decrypted == raw_token:
                await registered_device_repo.update_last_active(device.pk)
                logger.info("Device authenticated: user_id=%s, device_uuid=%s", device.user_id, device.device_uuid)
                return {"user_id": device.user_id, "device_uuid": device.device_uuid, "device_name": device.name}
        logger.warning("Token authentication failed: no matching token, prefix=%s, candidates=%d", prefix, len(candidates))
        return None

    async def list_devices(self, user_id: int) -> list[dict]:
        devices = await registered_device_repo.find_by_user_id(user_id)
        return [
            {
                "device_uuid": d.device_uuid, "name": d.name, "is_active": d.is_active,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "last_active_at": d.last_active_at.isoformat() if d.last_active_at else None,
            }
            for d in devices
        ]


device_service = DeviceService()
