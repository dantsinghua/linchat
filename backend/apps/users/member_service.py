import logging
import subprocess
import tempfile

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from apps.common.gateway_utils import build_gateway_headers, get_gateway_url
from apps.users.crypto import sm3_hash, sm4_decrypt
from apps.users.exceptions import UsernameExistsError, VoiceprintRegistrationError
from apps.users.models import SysUser
from apps.users.repositories import user_repo
from apps.voice.models import SpeakerProfile

logger = logging.getLogger(__name__)


def _convert_audio_to_wav16k(audio_data: bytes) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".input", delete=True) as inp, \
         tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as out:
        inp.write(audio_data)
        inp.flush()
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", inp.name,
                "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                "-f", "wav", out.name,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error("ffmpeg audio conversion failed: %s", result.stderr.decode()[-500:])
            raise ValueError(f"音频格式转换失败: ffmpeg exit {result.returncode}")
        return out.read()


class MemberService:
    """家庭成员管理服务"""

    @staticmethod
    async def list_members(include_expired: bool = False) -> list:
        return await user_repo.list_members(include_expired=include_expired)

    @staticmethod
    async def create_member(
        username: str,
        password_encrypted: str,
        member_type: str,
        audio_file,
        created_by_user_id: int,
    ) -> SysUser:
        # 1. 校验用户名唯一性
        existing = await user_repo.find_by_username(username)
        if existing:
            raise UsernameExistsError()

        # 2. SM4 解密密码 → SM3 哈希
        try:
            decrypted_password = sm4_decrypt(password_encrypted)
        except ValueError:
            raise ValueError("密码格式错误")
        password_hash = sm3_hash(decrypted_password)

        # 3. 调用 Gateway 声纹注册 API
        gateway_url = get_gateway_url()
        enroll_url = f"{gateway_url}/v1/voice/speakers/upload"
        headers = build_gateway_headers()

        audio_content = audio_file.read()
        audio_content_type = getattr(audio_file, "content_type", "audio/wav")

        # Gateway 要求 WAV PCM16 16kHz mono，前端 MediaRecorder 输出 webm/opus 需转换
        if audio_content_type != "audio/wav" or not audio_content[:4] == b"RIFF":
            audio_content = _convert_audio_to_wav16k(audio_content)
            audio_content_type = "audio/wav"
        audio_filename = "voiceprint.wav"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    enroll_url,
                    headers=headers,
                    files={"audio": (audio_filename, audio_content, audio_content_type)},
                    data={"name": username},
                )
            if resp.status_code in (200, 201):
                gateway_data = resp.json()
                gateway_speaker_id = gateway_data["speaker_id"]
                quality_score = gateway_data.get("quality_score")
                logger.info(
                    "Gateway voiceprint enrolled: username=%s, speaker_id=%s, quality=%s",
                    username, gateway_speaker_id, quality_score,
                )
            else:
                err_body = resp.json() if resp.content else {}
                err_code = err_body.get("error", {}).get("code", "unknown")
                err_msg = err_body.get("error", {}).get("message", resp.text)
                logger.error(
                    "Gateway voiceprint enroll failed: username=%s, status=%d, code=%s, msg=%s",
                    username, resp.status_code, err_code, err_msg,
                )
                raise VoiceprintRegistrationError(f"声纹注册失败: {err_code} - {err_msg}")
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            logger.error("Gateway voiceprint enroll HTTP error: username=%s, err=%s", username, e)
            raise VoiceprintRegistrationError(f"声纹注册网络错误: {e}")
        except VoiceprintRegistrationError:
            raise
        except Exception as e:
            logger.error("Gateway voiceprint enroll unexpected error: username=%s, err=%s", username, e)
            raise VoiceprintRegistrationError(f"声纹注册失败: {e}")

        # 4. 事务内创建用户 + SpeakerProfile
        guest_expires_at = None
        if member_type == "guest":
            guest_expires_at = timezone.now() + timedelta(days=7)

        @sync_to_async
        def _create_in_transaction() -> SysUser:
            with transaction.atomic():
                user = SysUser.objects.create(
                    username=username,
                    password_hash=password_hash,
                    status=1,
                    member_type=member_type,
                    guest_expires_at=guest_expires_at,
                )
                SpeakerProfile.objects.create(
                    user=user,
                    gateway_speaker_id=gateway_speaker_id,
                    name=username,
                    quality_score=quality_score,
                )
                return user

        user = await _create_in_transaction()

        # 5. 审计日志
        logger.info(
            "Member created: operator_user_id=%d, target_username=%s, target_user_id=%d, "
            "member_type=%s, action=create",
            created_by_user_id, username, user.user_id, member_type,
        )

        return user
