"""VoicePersistService 测试（batch-26）

覆盖 apps/voice/services/voice_persist_service.py 未测分支：
- persist_audio_attachment 主流程 / 空缓存早退 / 原子失败补偿 / cache_user_id 分支
- _atomic_mark_voice DB 落库（user_msg + asst_msg / 无匹配消息）
- _count_and_delete_excess 清理上限（超限删除 / 未超限 / 排除已回复）

F 组 mock 依赖；G/H 组用真实 ORM + django_db（比 mock 更省成本）。
"""

import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.utils import timezone

from apps.chat.models import Message
from apps.chat.repositories import message_repo
from apps.media.models import MediaAttachment
from apps.media.repositories import media_attachment_repo
from apps.voice.services.voice_persist_service import VoicePersistService

_VPS = "apps.voice.services.voice_persist_service"
_VSS = "apps.voice.services.voice_session_service"


def _mock_persist():
    """构造 voice_persist_service 单例 mock（sync 合成方法返回真实值）。"""
    m = MagicMock()
    m.merge_pcm_to_wav = MagicMock(return_value=b"RIFFwavdata")
    m.calculate_duration = MagicMock(return_value=0.04)
    m.upload_to_minio = AsyncMock()
    m._atomic_mark_voice = AsyncMock()
    m.delete_from_minio = AsyncMock()
    return m


def _mock_session(chunks):
    m = MagicMock()
    m.get_audio_chunks = AsyncMock(return_value=chunks)
    m.clear_audio_chunks = AsyncMock()
    return m


# ────────────────────────────────
# 分组 F — persist_audio_attachment
# ────────────────────────────────
@pytest.mark.asyncio(loop_scope="function")
class TestPersistAudioAttachment:
    async def test_persist_happy_path(self):
        mock_persist = _mock_persist()
        mock_session = _mock_session([b"\x00" * 640])
        with (
            patch(f"{_VPS}.voice_persist_service", mock_persist),
            patch(f"{_VSS}.voice_session_service", mock_session),
        ):
            await VoicePersistService.persist_audio_attachment(
                user_id=1, segment_id="seg-1", request_id="req-1"
            )
        mock_persist.upload_to_minio.assert_awaited_once()
        mock_persist._atomic_mark_voice.assert_awaited_once()
        mock_session.clear_audio_chunks.assert_awaited_once()
        storage_path = mock_persist.upload_to_minio.call_args[0][0]
        assert re.match(r"^media/1/\d{4}-\d{2}-\d{2}/[0-9a-f-]{36}\.wav$", storage_path)

    async def test_persist_empty_chunks_returns_early(self):
        mock_persist = _mock_persist()
        mock_session = _mock_session([])
        with (
            patch(f"{_VPS}.voice_persist_service", mock_persist),
            patch(f"{_VSS}.voice_session_service", mock_session),
        ):
            await VoicePersistService.persist_audio_attachment(
                user_id=1, segment_id="seg-1", request_id="req-1"
            )
        mock_persist.upload_to_minio.assert_not_awaited()
        mock_persist._atomic_mark_voice.assert_not_awaited()

    async def test_persist_atomic_failure_compensates(self):
        mock_persist = _mock_persist()
        mock_persist._atomic_mark_voice = AsyncMock(side_effect=Exception("db fail"))
        mock_session = _mock_session([b"\x00" * 640])
        with (
            patch(f"{_VPS}.voice_persist_service", mock_persist),
            patch(f"{_VSS}.voice_session_service", mock_session),
        ):
            # 外层 except 兜底：不向外抛
            await VoicePersistService.persist_audio_attachment(
                user_id=1, segment_id="seg-1", request_id="req-1"
            )
        mock_persist.delete_from_minio.assert_awaited_once()
        # 补偿删除后不再清理缓存
        mock_session.clear_audio_chunks.assert_not_awaited()

    async def test_persist_uses_cache_user_id(self):
        mock_persist = _mock_persist()
        mock_session = _mock_session([b"\x00" * 640])
        with (
            patch(f"{_VPS}.voice_persist_service", mock_persist),
            patch(f"{_VSS}.voice_session_service", mock_session),
        ):
            await VoicePersistService.persist_audio_attachment(
                user_id=1, segment_id="seg-9", request_id="req-9", cache_user_id=9
            )
        # 缓存读写用 cache_uid=9
        assert mock_session.get_audio_chunks.call_args[0][0] == 9
        assert mock_session.clear_audio_chunks.call_args[0][0] == 9
        # storage_path 用 user_id=1
        storage_path = mock_persist.upload_to_minio.call_args[0][0]
        assert storage_path.startswith("media/1/")


# ────────────────────────────────
# 分组 G — _atomic_mark_voice DB 落库
# ────────────────────────────────
@pytest.mark.django_db
class TestAtomicMarkVoice:
    def _make_message(self, request_id, role, user_id=1):
        return Message.objects.create(
            message_uuid=str(uuid.uuid4()),
            user_id=user_id,
            role=role,
            content="hi",
            request_id=request_id,
            is_voice=False,
            sequence=Message.objects.filter(user_id=user_id).count() + 1,
            status=Message.STATUS_NORMAL,
            created_time=timezone.now(),
        )

    def test_atomic_mark_creates_attachment(self):
        req = "req-atomic-1"
        user_msg = self._make_message(req, "user")
        asst_msg = self._make_message(req, "assistant")
        now = timezone.now()
        audio_uuid = str(uuid.uuid4())

        VoicePersistService._atomic_mark_voice.func(
            1, req, audio_uuid, f"media/1/x/{audio_uuid}.wav", 1024, 0.5, now
        )

        user_msg.refresh_from_db()
        asst_msg.refresh_from_db()
        assert user_msg.is_voice is True
        assert asst_msg.is_voice is True
        att = MediaAttachment.objects.get(attachment_uuid=audio_uuid)
        assert att.media_type == MediaAttachment.TYPE_AUDIO
        assert att.message_id == user_msg.message_id
        assert att.file_size == 1024
        assert att.duration_seconds == 0.5

    def test_atomic_mark_no_matching_message(self):
        now = timezone.now()
        audio_uuid = str(uuid.uuid4())
        # request_id 不存在任何 Message → 无附件、不抛异常
        VoicePersistService._atomic_mark_voice.func(
            1, "req-nomatch", audio_uuid, "media/1/x.wav", 512, 0.3, now
        )
        assert not MediaAttachment.objects.filter(attachment_uuid=audio_uuid).exists()


# ────────────────────────────────
# 分组 H — _count_and_delete_excess 清理上限
# ────────────────────────────────
@pytest.mark.django_db
class TestCountAndDeleteExcess:
    def _make_voice_user_msg(self, user_id, request_id, seq):
        from datetime import timedelta

        return Message.objects.create(
            message_uuid=str(uuid.uuid4()),
            user_id=user_id,
            role="user",
            content="ambient",
            request_id=request_id,
            is_voice=True,
            sequence=seq,
            status=Message.STATUS_NORMAL,
            created_time=timezone.now() + timedelta(seconds=seq),
        )

    def _make_voice_asst_msg(self, user_id, request_id, seq):
        return Message.objects.create(
            message_uuid=str(uuid.uuid4()),
            user_id=user_id,
            role="assistant",
            content="reply",
            request_id=request_id,
            is_voice=True,
            sequence=seq,
            status=Message.STATUS_NORMAL,
            created_time=timezone.now(),
        )

    def test_count_delete_excess_over_limit(self):
        uid = 8001
        for i in range(5):
            self._make_voice_user_msg(uid, f"h1-req-{i}", i)

        deleted = message_repo.delete_excess_record_only.func(uid, 2)

        assert deleted == 3
        remaining = list(
            Message.objects.filter(user_id=uid, role="user").order_by("created_time")
        )
        assert len(remaining) == 2
        # 最旧 3 条被删，剩下最新的 2 条（seq 3,4）
        assert {m.request_id for m in remaining} == {"h1-req-3", "h1-req-4"}

    def test_count_delete_below_limit_noop(self):
        uid = 8002
        for i in range(3):
            self._make_voice_user_msg(uid, f"h2-req-{i}", i)

        deleted = message_repo.delete_excess_record_only.func(uid, 10)

        assert deleted == 0
        assert Message.objects.filter(user_id=uid, role="user").count() == 3

    def test_count_delete_excludes_replied(self):
        uid = 8003
        # 2 条 user 语音，其中 h3-req-0 有 assistant 语音回复 → 被 Subquery 排除
        self._make_voice_user_msg(uid, "h3-req-0", 0)
        self._make_voice_user_msg(uid, "h3-req-1", 1)
        self._make_voice_asst_msg(uid, "h3-req-0", 2)

        # 未回复的 record-only 只有 1 条 <= limit=1 → 返回 0
        deleted = message_repo.delete_excess_record_only.func(uid, 1)

        assert deleted == 0
        assert Message.objects.filter(user_id=uid, role="user").count() == 2


# ────────────────────────────────
# 分组 I — batch-33 收敛后新增 repo 方法直接单测（等价语义护栏）
# ────────────────────────────────
@pytest.mark.django_db
class TestVoiceRepoMethods:
    def _mk(self, user_id, role, request_id, is_voice=False, speaker_id=None, content="hi", seq=1):
        return Message.objects.create(
            message_uuid=str(uuid.uuid4()), user_id=user_id, role=role, content=content,
            request_id=request_id, is_voice=is_voice, speaker_id=speaker_id,
            sequence=seq, status=Message.STATUS_NORMAL, created_time=timezone.now(),
        )

    def test_get_by_request_id_sync_role_filter(self):
        u = self._mk(1, "user", "gr-1", seq=1)
        self._mk(1, "assistant", "gr-1", seq=2)
        got_user = message_repo.get_by_request_id_sync("gr-1", 1, role="user")
        got_asst = message_repo.get_by_request_id_sync("gr-1", 1, role="assistant")
        assert got_user.message_id == u.message_id
        assert got_asst.role == "assistant"
        # 隔离粒度 user_id：跨用户不可见
        assert message_repo.get_by_request_id_sync("gr-1", 999, role="user") is None
        # role=None 不过滤 role
        assert message_repo.get_by_request_id_sync("gr-1", 1, role=None) is not None

    def test_set_voice_flag_sync(self):
        m = self._mk(1, "user", "sv-1", is_voice=False, seq=1)
        message_repo.set_voice_flag_sync(m)
        m.refresh_from_db()
        assert m.is_voice is True

    def test_reassign_speaker_messages(self):
        # 未知标签的语音消息改归属；非语音/别标签不受影响
        self._mk(1, "user", "rs-1", is_voice=True, speaker_id="unknown-x", seq=1)
        self._mk(1, "user", "rs-2", is_voice=True, speaker_id="unknown-x", seq=2)
        self._mk(1, "user", "rs-3", is_voice=False, speaker_id="unknown-x", seq=3)
        self._mk(1, "user", "rs-4", is_voice=True, speaker_id="other", seq=4)
        count = message_repo.reassign_speaker_messages.func("unknown-x", 7)
        assert count == 2
        assert Message.objects.filter(speaker_id="7", user_id=7, is_voice=True).count() == 2
        # 非语音的同标签不动
        assert Message.objects.filter(request_id="rs-3", speaker_id="unknown-x").exists()

    def test_update_content_by_request_id(self):
        self._mk(1, "user", "uc-1", content="[语音对话] 原始", seq=1)
        self._mk(1, "assistant", "uc-1", content="回复", seq=2)
        n = message_repo.update_content_by_request_id.func("uc-1", 1, "纯ASR原文", role="user")
        assert n == 1
        assert message_repo.get_by_request_id_sync("uc-1", 1, role="user").content == "纯ASR原文"
        # assistant 消息不受影响
        assert message_repo.get_by_request_id_sync("uc-1", 1, role="assistant").content == "回复"

    def test_create_audio_attachment_sync(self):
        from datetime import timedelta
        msg = self._mk(1, "user", "ca-1", is_voice=True, seq=1)
        now = timezone.now()
        att = media_attachment_repo.create_audio_attachment_sync(
            attachment_uuid=str(uuid.uuid4()), message=msg, user_id=1,
            mime_type="audio/wav", file_name="voice_x.wav", file_size=2048,
            storage_path="media/1/x.wav", duration_seconds=1.5,
            created_at=now, expires_at=now + timedelta(days=7),
        )
        assert att.media_type == MediaAttachment.TYPE_AUDIO
        assert att.message_id == msg.message_id
        assert att.file_size == 2048
        assert att.duration_seconds == 1.5
