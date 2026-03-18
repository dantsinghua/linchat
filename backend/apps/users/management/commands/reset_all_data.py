"""
全量清库脚本 — 清空所有业务数据并初始化管理员账户

用途: 开发/测试环境全量重置，或生产环境迁移前准备。

用法:
    python manage.py reset_all_data --password <明文密码> --audio <音频文件路径>
    python manage.py reset_all_data --password <明文密码> --audio <音频文件路径> --yes
"""
import logging
from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.common.gateway_utils import build_gateway_headers, get_gateway_url
from apps.common.storage import minio_service
from apps.users.crypto import sm3_hash
from apps.users.models import SysUser

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "清空所有业务数据并初始化管理员账户（anlin）"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--password",
            type=str,
            required=True,
            help="管理员明文密码（命令内执行 SM3 哈希）",
        )
        parser.add_argument(
            "--audio",
            type=str,
            required=True,
            help="管理员声纹预录音频文件路径（WAV 格式）",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            default=False,
            help="跳过确认提示，直接执行",
        )

    def handle(self, *args, **options) -> None:
        password: str = options["password"]
        audio_path: str = options["audio"]
        skip_confirm: bool = options["yes"]

        # 校验音频文件
        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise CommandError(f"音频文件不存在: {audio_path}")

        # 确认提示
        if not skip_confirm:
            self.stdout.write(self.style.WARNING(
                "\n⚠️  警告：此操作将清空所有业务数据！\n"
                "  - PostgreSQL: SysUser, Message, LangGraphExecution, "
                "MediaAttachment, DocumentChunkEmbedding, "
                "UserMemory, UserMemoryEmbedding, "
                "SpeakerProfile, RegisteredDevice, VoiceSettings\n"
                "  - MinIO: linchat-media, linchat-thumbnails 存储桶\n"
                "  - 重建管理员账户: anlin\n"
            ))
            confirm = input("确认执行？输入 'yes' 继续: ")
            if confirm.strip().lower() != "yes":
                self.stdout.write(self.style.NOTICE("已取消"))
                return

        # 1. 清空 PostgreSQL 业务表（尊重外键顺序）
        self._clear_database()

        # 2. 清空 MinIO 存储桶
        self._clear_minio()

        # 3. 初始化管理员账户
        self._init_admin(password, audio_file)

        self.stdout.write(self.style.SUCCESS("\n✅ 全量清库完成，管理员账户 anlin 已就绪"))

    def _clear_database(self) -> None:
        """清空所有业务表，按外键依赖顺序删除"""
        self.stdout.write("\n📦 清空 PostgreSQL 业务表...")

        # 延迟导入避免循环依赖
        from apps.chat.models import LangGraphExecution, Message
        from apps.media.models import DocumentChunkEmbedding, MediaAttachment
        from apps.memory.models import UserMemory, UserMemoryEmbedding
        from apps.voice.models import RegisteredDevice, SpeakerProfile, VoiceSettings

        # 按外键依赖顺序删除（子表先删）
        delete_order = [
            ("DocumentChunkEmbedding", DocumentChunkEmbedding),
            ("UserMemoryEmbedding", UserMemoryEmbedding),
            ("UserMemory", UserMemory),
            ("MediaAttachment", MediaAttachment),
            ("LangGraphExecution", LangGraphExecution),
            ("Message", Message),
            ("SpeakerProfile", SpeakerProfile),
            ("RegisteredDevice", RegisteredDevice),
            ("VoiceSettings", VoiceSettings),
            ("SysUser", SysUser),
        ]

        with transaction.atomic():
            for name, model in delete_order:
                count, _ = model.objects.all().delete()
                self.stdout.write(f"  {name}: 删除 {count} 条")
                logger.info("reset_all_data: deleted %d rows from %s", count, name)

        self.stdout.write(self.style.SUCCESS("  PostgreSQL 清空完成"))

    def _clear_minio(self) -> None:
        """清空 MinIO 存储桶中的所有对象"""
        self.stdout.write("\n🗂️  清空 MinIO 存储桶...")

        buckets = [
            settings.MINIO_BUCKET_MEDIA,
            settings.MINIO_BUCKET_THUMBNAILS,
        ]

        for bucket_name in buckets:
            try:
                if not minio_service.client.bucket_exists(bucket_name):
                    self.stdout.write(f"  {bucket_name}: 不存在，跳过")
                    continue

                objects = minio_service.client.list_objects(bucket_name, recursive=True)
                count = 0
                for obj in objects:
                    minio_service.client.remove_object(bucket_name, obj.object_name)
                    count += 1

                self.stdout.write(f"  {bucket_name}: 删除 {count} 个对象")
                logger.info("reset_all_data: deleted %d objects from MinIO bucket %s", count, bucket_name)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  {bucket_name}: 清空失败 - {e}"))
                logger.warning("reset_all_data: failed to clear MinIO bucket %s: %s", bucket_name, e)

        self.stdout.write(self.style.SUCCESS("  MinIO 清空完成"))

    def _init_admin(self, password: str, audio_file: Path) -> None:
        """初始化管理员账户 anlin，包含声纹注册"""
        from apps.voice.models import SpeakerProfile

        self.stdout.write("\n👤 初始化管理员账户 anlin...")

        username = "anlin"
        password_hash = sm3_hash(password)

        # 调用 Gateway 声纹注册 API
        gateway_speaker_id, quality_score = self._register_voiceprint(username, audio_file)

        # 事务内创建用户 + SpeakerProfile
        with transaction.atomic():
            user = SysUser.objects.create(
                username=username,
                password_hash=password_hash,
                type="admin",
                member_type="member",
                status=1,
            )
            SpeakerProfile.objects.create(
                user=user,
                gateway_speaker_id=gateway_speaker_id,
                name=username,
                quality_score=quality_score,
            )

        self.stdout.write(f"  用户创建: user_id={user.user_id}, username={username}")
        self.stdout.write(f"  声纹注册: speaker_id={gateway_speaker_id}, quality={quality_score}")
        logger.info(
            "reset_all_data: admin created user_id=%d, speaker_id=%s",
            user.user_id, gateway_speaker_id,
        )

    def _register_voiceprint(self, username: str, audio_file: Path) -> tuple[str, float | None]:
        """调用 Gateway 声纹注册 API，返回 (speaker_id, quality_score)"""
        import subprocess as sp
        import tempfile

        gateway_url = get_gateway_url()
        enroll_url = f"{gateway_url}/v1/voice/speakers/upload"
        headers = build_gateway_headers()

        audio_content = audio_file.read_bytes()
        content_type = "audio/wav"

        # Gateway 要求 WAV PCM16 16kHz mono，检测并转换
        if not audio_content[:4] == b"RIFF":
            self.stdout.write("  音频格式非 WAV，执行 ffmpeg 转换...")
            with tempfile.NamedTemporaryFile(suffix=audio_file.suffix, delete=True) as inp, \
                 tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as out:
                inp.write(audio_content)
                inp.flush()
                result = sp.run(
                    ["ffmpeg", "-y", "-i", inp.name, "-ar", "16000", "-ac", "1",
                     "-sample_fmt", "s16", "-f", "wav", out.name],
                    capture_output=True, timeout=30,
                )
                if result.returncode != 0:
                    raise CommandError(f"音频格式转换失败: {result.stderr.decode()[-300:]}")
                audio_content = out.read()
            self.stdout.write(f"  转换完成: {len(audio_content)} bytes")

        self.stdout.write(f"  调用 Gateway 声纹注册: {enroll_url}")

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    enroll_url,
                    headers=headers,
                    files={"audio": ("voiceprint.wav", audio_content, content_type)},
                    data={"name": username},
                )

            if resp.status_code in (200, 201):
                data = resp.json()
                speaker_id = data["speaker_id"]
                quality_score = data.get("quality_score")
                return speaker_id, quality_score
            else:
                err_body = resp.json() if resp.content else {}
                err_code = err_body.get("error", {}).get("code", "unknown")
                err_msg = err_body.get("error", {}).get("message", resp.text)
                raise CommandError(
                    f"Gateway 声纹注册失败: status={resp.status_code}, "
                    f"code={err_code}, message={err_msg}"
                )
        except httpx.TimeoutException:
            raise CommandError("Gateway 声纹注册超时")
        except httpx.HTTPError as e:
            raise CommandError(f"Gateway 声纹注册网络错误: {e}")
        except CommandError:
            raise
        except Exception as e:
            raise CommandError(f"Gateway 声纹注册失败: {e}")
