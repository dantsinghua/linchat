"""语音会话服务测试

参考:
- specs/009-voice-interaction/tasks.md#T061
- apps/voice/services/voice_session_service.py

覆盖:
1. Redis 会话状态创建/读取/删除
2. 单会话强制（新会话覆盖旧会话）
3. TTL 过期验证
4. 活跃对话标记设置与过期
5. 音频帧缓存累积
6. 消息持久化（创建 Message + MediaAttachment + assistant Message）
7. 音频文件 MinIO 上传
8. STT 转写相关方法
9. LLM 频率限制检查 check_llm_rate_limit

Mock 策略: Mock Redis（core.redis 的异步方法）和 MinIO。使用真实数据库写入 Message/MediaAttachment。
覆盖率要求: >= 95%
"""

import json
import struct
import uuid
import wave
from datetime import timedelta
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.conf import settings
from django.utils import timezone

from apps.chat.models import MediaAttachment, Message
from apps.voice.services.voice_session_service import (
    ACTIVE_CONV_KEY,
    AUDIO_CHUNKS_KEY,
    LLM_RATE_KEY,
    SESSION_KEY,
    STT_PENDING_KEY,
    STT_RESULT_KEY,
    VoiceSessionService,
)
from tests.helpers import run_async


# ========== 通用 fixture ==========


@pytest.fixture
def service():
    """创建 VoiceSessionService 实例"""
    return VoiceSessionService()


@pytest.fixture
def user_id():
    """测试用户 ID"""
    return 42


@pytest.fixture
def segment_id():
    """测试 segment ID"""
    return "seg_abc123"


def _make_pcm_chunk(num_samples: int = 160) -> bytes:
    """生成 PCM16 16kHz mono 测试音频帧

    Args:
        num_samples: 采样点数量（默认 160 = 10ms @16kHz）
    """
    return struct.pack(f"<{num_samples}h", *([1000] * num_samples))


# ========== 会话状态管理测试 ==========


class TestCreateSession:
    """create_session 测试"""

    @patch("apps.voice.services.voice_session_service.redis_setex", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_create_session_success(self, mock_get, mock_setex, service, user_id):
        """创建新会话：无已有会话时返回 True"""
        mock_get.return_value = None
        mock_setex.return_value = True

        result = run_async(service.create_session(user_id))

        assert result is True
        mock_get.assert_called_once_with(SESSION_KEY.format(user_id=user_id))
        mock_setex.assert_called_once()
        call_args = mock_setex.call_args
        assert call_args[0][0] == SESSION_KEY.format(user_id=user_id)
        assert call_args[0][1] == settings.VOICE_SESSION_TTL
        session_data = json.loads(call_args[0][2])
        assert session_data["state"] == "active"
        assert "started_at" in session_data
        assert session_data["upstream_connected"] is False

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_create_session_already_exists(self, mock_get, service, user_id):
        """单会话强制（FR-034）：已有会话时返回 False"""
        mock_get.return_value = json.dumps({
            "state": "active",
            "started_at": 1700000000.0,
            "upstream_connected": True,
        })

        result = run_async(service.create_session(user_id))

        assert result is False


class TestGetSession:
    """get_session 测试"""

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_get_session_exists(self, mock_get, service, user_id):
        """获取已存在的会话状态"""
        session_data = {
            "state": "active",
            "started_at": 1700000000.0,
            "upstream_connected": True,
        }
        mock_get.return_value = json.dumps(session_data)

        result = run_async(service.get_session(user_id))

        assert result == session_data
        mock_get.assert_called_once_with(SESSION_KEY.format(user_id=user_id))

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_get_session_not_exists(self, mock_get, service, user_id):
        """获取不存在的会话返回 None"""
        mock_get.return_value = None

        result = run_async(service.get_session(user_id))

        assert result is None


class TestRefreshSession:
    """refresh_session 测试"""

    @patch("core.redis.redis_expire", new_callable=AsyncMock)
    def test_refresh_session_ttl(self, mock_expire, service, user_id):
        """刷新会话 TTL"""
        mock_expire.return_value = True

        run_async(service.refresh_session(user_id))

        mock_expire.assert_called_once_with(
            SESSION_KEY.format(user_id=user_id),
            settings.VOICE_SESSION_TTL,
        )


class TestUpdateSession:
    """update_session 测试"""

    @patch("apps.voice.services.voice_session_service.redis_setex", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_update_session_fields(self, mock_get, mock_setex, service, user_id):
        """更新会话状态字段"""
        original_data = {
            "state": "active",
            "started_at": 1700000000.0,
            "upstream_connected": False,
        }
        mock_get.return_value = json.dumps(original_data)
        mock_setex.return_value = True

        run_async(service.update_session(user_id, upstream_connected=True))

        mock_setex.assert_called_once()
        call_args = mock_setex.call_args
        updated_data = json.loads(call_args[0][2])
        assert updated_data["upstream_connected"] is True
        assert updated_data["state"] == "active"

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_update_session_not_exists(self, mock_get, service, user_id):
        """更新不存在的会话（无操作）"""
        mock_get.return_value = None

        # 应该不抛异常
        run_async(service.update_session(user_id, state="idle"))


class TestCloseSession:
    """close_session 测试"""

    @patch("apps.voice.services.voice_session_service.redis_delete", new_callable=AsyncMock)
    def test_close_session(self, mock_delete, service, user_id):
        """关闭会话，清理所有相关 Redis 键"""
        mock_delete.return_value = 1

        run_async(service.close_session(user_id))

        assert mock_delete.call_count == 2
        deleted_keys = [call[0][0] for call in mock_delete.call_args_list]
        assert SESSION_KEY.format(user_id=user_id) in deleted_keys
        assert ACTIVE_CONV_KEY.format(user_id=user_id) in deleted_keys


# ========== 活跃对话标记测试 ==========


class TestActiveConversation:
    """活跃对话标记测试"""

    @patch("apps.voice.services.voice_session_service.redis_setex", new_callable=AsyncMock)
    def test_set_active_conversation(self, mock_setex, service, user_id):
        """标记活跃对话"""
        mock_setex.return_value = True

        run_async(service.set_active_conversation(user_id))

        mock_setex.assert_called_once_with(
            ACTIVE_CONV_KEY.format(user_id=user_id),
            settings.VOICE_ACTIVE_CONV_TTL,
            "1",
        )

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_is_active_conversation_true(self, mock_get, service, user_id):
        """检查有活跃对话"""
        mock_get.return_value = "1"

        result = run_async(service.is_active_conversation(user_id))

        assert result is True
        mock_get.assert_called_once_with(ACTIVE_CONV_KEY.format(user_id=user_id))

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_is_active_conversation_false(self, mock_get, service, user_id):
        """检查无活跃对话（TTL 过期）"""
        mock_get.return_value = None

        result = run_async(service.is_active_conversation(user_id))

        assert result is False


# ========== 音频帧缓存测试 ==========


class TestAudioChunks:
    """音频帧缓存测试"""

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_cache_audio_chunk(self, mock_get_client, service, user_id, segment_id):
        """缓存单个音频帧到 Redis List（base64 编码存储）"""
        import base64
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis
        pcm_data = _make_pcm_chunk()

        run_async(service.cache_audio_chunk(user_id, segment_id, pcm_data))

        expected_key = AUDIO_CHUNKS_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        expected_encoded = base64.b64encode(pcm_data).decode("ascii")
        mock_redis.rpush.assert_called_once_with(expected_key, expected_encoded)
        mock_redis.expire.assert_called_once_with(
            expected_key, settings.VOICE_AUDIO_CACHE_TTL
        )
        mock_redis.aclose.assert_called_once()

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_cache_audio_chunk_multiple(self, mock_get_client, service, user_id, segment_id):
        """多次缓存音频帧，验证累积调用"""
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis

        chunk1 = _make_pcm_chunk(160)
        chunk2 = _make_pcm_chunk(320)

        run_async(service.cache_audio_chunk(user_id, segment_id, chunk1))
        run_async(service.cache_audio_chunk(user_id, segment_id, chunk2))

        assert mock_redis.rpush.call_count == 2

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_get_audio_chunks(self, mock_get_client, service, user_id, segment_id):
        """获取缓存的所有音频帧（base64 解码还原）"""
        import base64
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis
        chunk1 = _make_pcm_chunk(160)
        chunk2 = _make_pcm_chunk(320)
        # Redis 返回的是 base64 编码的字符串
        encoded1 = base64.b64encode(chunk1).decode("ascii")
        encoded2 = base64.b64encode(chunk2).decode("ascii")
        mock_redis.lrange.return_value = [encoded1, encoded2]

        result = run_async(service.get_audio_chunks(user_id, segment_id))

        assert len(result) == 2
        assert result[0] == chunk1
        assert result[1] == chunk2
        expected_key = AUDIO_CHUNKS_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        mock_redis.lrange.assert_called_once_with(expected_key, 0, -1)
        mock_redis.aclose.assert_called_once()

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_get_audio_chunks_empty(self, mock_get_client, service, user_id, segment_id):
        """获取空音频帧列表"""
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis
        mock_redis.lrange.return_value = []

        result = run_async(service.get_audio_chunks(user_id, segment_id))

        assert result == []

    @patch("apps.voice.services.voice_session_service.redis_delete", new_callable=AsyncMock)
    def test_clear_audio_chunks(self, mock_delete, service, user_id, segment_id):
        """清理音频帧缓存"""
        mock_delete.return_value = 1

        run_async(service.clear_audio_chunks(user_id, segment_id))

        expected_key = AUDIO_CHUNKS_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        mock_delete.assert_called_once_with(expected_key)


# ========== WAV 文件生成测试 ==========


class TestMergePcmToWav:
    """merge_pcm_to_wav 测试"""

    def test_merge_single_chunk(self):
        """合并单个 PCM 帧为 WAV"""
        pcm = _make_pcm_chunk(160)
        wav_data = VoiceSessionService.merge_pcm_to_wav([pcm])

        # 验证 WAV 头
        buf = BytesIO(wav_data)
        with wave.open(buf, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 160

    def test_merge_multiple_chunks(self):
        """合并多个 PCM 帧为 WAV"""
        chunk1 = _make_pcm_chunk(160)
        chunk2 = _make_pcm_chunk(320)
        wav_data = VoiceSessionService.merge_pcm_to_wav([chunk1, chunk2])

        buf = BytesIO(wav_data)
        with wave.open(buf, "rb") as wf:
            assert wf.getnframes() == 480  # 160 + 320

    def test_merge_empty_chunks(self):
        """合并空 PCM 列表"""
        wav_data = VoiceSessionService.merge_pcm_to_wav([])

        buf = BytesIO(wav_data)
        with wave.open(buf, "rb") as wf:
            assert wf.getnframes() == 0

    def test_wav_starts_with_riff(self):
        """验证 WAV 文件以 RIFF 头开始"""
        pcm = _make_pcm_chunk(160)
        wav_data = VoiceSessionService.merge_pcm_to_wav([pcm])

        assert wav_data[:4] == b"RIFF"


class TestCalculateDuration:
    """calculate_duration 测试"""

    def test_duration_16khz_mono(self):
        """16kHz 单声道时长计算"""
        # 16000 samples = 1 second, 每个 sample 2 bytes
        one_second_pcm = _make_pcm_chunk(16000)
        duration = VoiceSessionService.calculate_duration([one_second_pcm])
        assert abs(duration - 1.0) < 1e-6

    def test_duration_half_second(self):
        """半秒音频时长计算"""
        half_second_pcm = _make_pcm_chunk(8000)
        duration = VoiceSessionService.calculate_duration([half_second_pcm])
        assert abs(duration - 0.5) < 1e-6

    def test_duration_multiple_chunks(self):
        """多帧累加时长计算"""
        chunk1 = _make_pcm_chunk(16000)  # 1s
        chunk2 = _make_pcm_chunk(8000)  # 0.5s
        duration = VoiceSessionService.calculate_duration([chunk1, chunk2])
        assert abs(duration - 1.5) < 1e-6

    def test_duration_empty(self):
        """空音频时长为 0"""
        duration = VoiceSessionService.calculate_duration([])
        assert duration == 0.0


# ========== 消息持久化测试 ==========


class TestPersistVoiceMessage:
    """persist_voice_message 测试（Mock _atomic_persist 避免数据库依赖）"""

    @patch.object(VoiceSessionService, "clear_audio_chunks", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.message_repo")
    @patch.object(VoiceSessionService, "_atomic_persist", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "_upload_to_minio", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_persist_voice_message_success(
        self,
        mock_get_chunks,
        mock_upload,
        mock_atomic,
        mock_message_repo,
        mock_redis_get,
        mock_clear,
        service,
        user_id,
        segment_id,
    ):
        """完整消息持久化流程：user Message + MediaAttachment + assistant Message"""
        pcm_chunks = [_make_pcm_chunk(16000)]  # 1 秒音频
        mock_get_chunks.return_value = pcm_chunks
        mock_upload.return_value = None
        mock_message_repo.get_next_sequence = AsyncMock(return_value=10)
        mock_clear.return_value = None

        # STT 转写结果
        mock_redis_get.return_value = "你好，请帮我查一下天气"

        # Mock _atomic_persist 返回值
        mock_atomic.return_value = {
            "user_message_id": 100,
            "user_message_uuid": "uuid-user-100",
            "assistant_message_id": 101,
            "assistant_message_uuid": "uuid-asst-101",
        }

        result = run_async(
            service.persist_voice_message(
                user_id=user_id,
                segment_id=segment_id,
                assistant_content="好的，正在为您查询天气。",
                speaker_id="speaker_001",
                response_usage={"input_tokens": 50, "output_tokens": 30},
                response_time_ms=500,
            )
        )

        assert result is not None
        assert result["user_message_id"] == 100
        assert result["assistant_message_id"] == 101
        assert result["user_message_uuid"] == "uuid-user-100"
        assert result["assistant_message_uuid"] == "uuid-asst-101"

        # 验证 MinIO 上传被调用
        mock_upload.assert_called_once()
        upload_args = mock_upload.call_args
        assert upload_args[0][0].endswith(".wav")  # storage_path
        assert isinstance(upload_args[0][1], bytes)  # wav_data

        # 验证 _atomic_persist 被调用并传入正确参数
        mock_atomic.assert_called_once()
        persist_kwargs = mock_atomic.call_args[1]
        assert persist_kwargs["user_id"] == user_id
        assert persist_kwargs["user_content"] == "你好，请帮我查一下天气"
        assert persist_kwargs["assistant_content"] == "好的，正在为您查询天气。"
        assert persist_kwargs["speaker_id"] == "speaker_001"
        assert persist_kwargs["next_seq"] == 10
        assert persist_kwargs["response_usage"] == {"input_tokens": 50, "output_tokens": 30}
        assert persist_kwargs["response_time_ms"] == 500
        assert persist_kwargs["is_interrupted"] is False
        # WAV 文件大小 > 0
        assert persist_kwargs["wav_size"] > 0
        # duration 约 1.0 秒
        assert abs(persist_kwargs["duration"] - 1.0) < 0.01

        # 验证音频缓存被清理
        mock_clear.assert_called_once_with(user_id, segment_id)

    @patch.object(VoiceSessionService, "clear_audio_chunks", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.message_repo")
    @patch.object(VoiceSessionService, "_atomic_persist", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "_upload_to_minio", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_persist_interrupted_message(
        self,
        mock_get_chunks,
        mock_upload,
        mock_atomic,
        mock_message_repo,
        mock_redis_get,
        mock_clear,
        service,
        user_id,
        segment_id,
    ):
        """持久化中断的消息：is_interrupted=True 传入 _atomic_persist"""
        mock_get_chunks.return_value = [_make_pcm_chunk(8000)]
        mock_upload.return_value = None
        mock_message_repo.get_next_sequence = AsyncMock(return_value=20)
        mock_redis_get.return_value = None  # 无 STT 结果
        mock_clear.return_value = None
        mock_atomic.return_value = {
            "user_message_id": 200,
            "user_message_uuid": "uuid-user-200",
            "assistant_message_id": 201,
            "assistant_message_uuid": "uuid-asst-201",
        }

        result = run_async(
            service.persist_voice_message(
                user_id=user_id,
                segment_id=segment_id,
                assistant_content="好的...",
                is_interrupted=True,
            )
        )

        assert result is not None
        persist_kwargs = mock_atomic.call_args[1]
        assert persist_kwargs["is_interrupted"] is True
        assert persist_kwargs["user_content"] == ""  # 无 STT 结果时为空

    @patch.object(VoiceSessionService, "clear_audio_chunks", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.message_repo")
    @patch.object(VoiceSessionService, "_atomic_persist", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "_upload_to_minio", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_persist_without_usage(
        self,
        mock_get_chunks,
        mock_upload,
        mock_atomic,
        mock_message_repo,
        mock_redis_get,
        mock_clear,
        service,
        user_id,
        segment_id,
    ):
        """持久化无 response_usage 的消息"""
        mock_get_chunks.return_value = [_make_pcm_chunk(8000)]
        mock_upload.return_value = None
        mock_message_repo.get_next_sequence = AsyncMock(return_value=1)
        mock_redis_get.return_value = "测试内容"
        mock_clear.return_value = None
        mock_atomic.return_value = {
            "user_message_id": 300,
            "user_message_uuid": "uuid-user-300",
            "assistant_message_id": 301,
            "assistant_message_uuid": "uuid-asst-301",
        }

        result = run_async(
            service.persist_voice_message(
                user_id=user_id,
                segment_id=segment_id,
                assistant_content="回复内容",
                response_usage=None,
            )
        )

        assert result is not None
        persist_kwargs = mock_atomic.call_args[1]
        assert persist_kwargs["response_usage"] is None

    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_persist_no_audio_chunks(
        self, mock_get_chunks, service, user_id, segment_id
    ):
        """无音频帧时返回 None"""
        mock_get_chunks.return_value = []

        result = run_async(
            service.persist_voice_message(
                user_id=user_id,
                segment_id=segment_id,
                assistant_content="回复",
            )
        )

        assert result is None

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.message_repo")
    @patch.object(VoiceSessionService, "_upload_to_minio", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_persist_upload_failure(
        self,
        mock_get_chunks,
        mock_upload,
        mock_message_repo,
        mock_redis_get,
        service,
        user_id,
        segment_id,
    ):
        """MinIO 上传失败时返回 None"""
        mock_get_chunks.return_value = [_make_pcm_chunk(160)]
        mock_upload.side_effect = Exception("MinIO connection failed")
        mock_message_repo.get_next_sequence = AsyncMock(return_value=1)
        mock_redis_get.return_value = "转写文本"

        result = run_async(
            service.persist_voice_message(
                user_id=user_id,
                segment_id=segment_id,
                assistant_content="回复",
            )
        )

        assert result is None

    @patch.object(VoiceSessionService, "clear_audio_chunks", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.message_repo")
    @patch.object(VoiceSessionService, "_atomic_persist", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "_upload_to_minio", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_persist_atomic_persist_failure(
        self,
        mock_get_chunks,
        mock_upload,
        mock_atomic,
        mock_message_repo,
        mock_redis_get,
        mock_clear,
        service,
        user_id,
        segment_id,
    ):
        """_atomic_persist 抛出异常时返回 None"""
        mock_get_chunks.return_value = [_make_pcm_chunk(160)]
        mock_upload.return_value = None
        mock_message_repo.get_next_sequence = AsyncMock(return_value=1)
        mock_redis_get.return_value = "转写"
        mock_atomic.side_effect = Exception("DB connection error")

        result = run_async(
            service.persist_voice_message(
                user_id=user_id,
                segment_id=segment_id,
                assistant_content="回复",
            )
        )

        assert result is None

    @patch.object(VoiceSessionService, "clear_audio_chunks", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.message_repo")
    @patch.object(VoiceSessionService, "_atomic_persist", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "_upload_to_minio", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_persist_storage_path_format(
        self,
        mock_get_chunks,
        mock_upload,
        mock_atomic,
        mock_message_repo,
        mock_redis_get,
        mock_clear,
        service,
        user_id,
        segment_id,
    ):
        """验证存储路径格式: media/{user_id}/{date}/{uuid}.wav"""
        mock_get_chunks.return_value = [_make_pcm_chunk(160)]
        mock_upload.return_value = None
        mock_message_repo.get_next_sequence = AsyncMock(return_value=1)
        mock_redis_get.return_value = None
        mock_clear.return_value = None
        mock_atomic.return_value = {
            "user_message_id": 400,
            "user_message_uuid": "uuid-400",
            "assistant_message_id": 401,
            "assistant_message_uuid": "uuid-401",
        }

        run_async(
            service.persist_voice_message(
                user_id=user_id,
                segment_id=segment_id,
                assistant_content="回复",
            )
        )

        persist_kwargs = mock_atomic.call_args[1]
        storage_path = persist_kwargs["storage_path"]
        # 格式: media/{user_id}/{YYYY-MM-DD}/{uuid}.wav
        assert storage_path.startswith(f"media/{user_id}/")
        assert storage_path.endswith(".wav")
        parts = storage_path.split("/")
        assert len(parts) == 4  # media, user_id, date, filename


# ========== _atomic_persist 单元测试（Mock ORM） ==========


class TestAtomicPersist:
    """_atomic_persist 原子写入测试（Mock 数据库操作）"""

    @patch("apps.voice.services.voice_session_service.MediaAttachment")
    @patch("apps.voice.services.voice_session_service.Message")
    @patch("apps.voice.services.voice_session_service.transaction")
    def test_atomic_persist_creates_all_records(
        self, mock_tx, mock_message_cls, mock_attachment_cls, service, user_id
    ):
        """原子写入：创建 user Message + MediaAttachment + assistant Message"""
        now = timezone.now()
        audio_uuid = str(uuid.uuid4())
        storage_path = f"media/{user_id}/2026-02-24/{audio_uuid}.wav"

        # Mock Message.objects.create
        mock_user_msg = MagicMock()
        mock_user_msg.message_id = 100
        mock_user_msg.message_uuid = "uuid-user-100"
        mock_asst_msg = MagicMock()
        mock_asst_msg.message_id = 101
        mock_asst_msg.message_uuid = "uuid-asst-101"
        mock_message_cls.objects.create.side_effect = [mock_user_msg, mock_asst_msg]
        mock_message_cls.ROLE_USER = "user"
        mock_message_cls.ROLE_ASSISTANT = "assistant"
        mock_message_cls.STATUS_NORMAL = 1
        mock_message_cls.STATUS_INTERRUPTED = 3
        mock_attachment_cls.TYPE_AUDIO = "audio"

        result = run_async(
            service._atomic_persist(
                user_id=user_id,
                user_content="你好世界",
                assistant_content="你好！有什么可以帮你的？",
                speaker_id="spk_001",
                audio_uuid=audio_uuid,
                storage_path=storage_path,
                wav_size=32044,
                duration=1.0,
                next_seq=10,
                now=now,
                response_usage={"input_tokens": 50, "output_tokens": 30},
                response_time_ms=500,
                is_interrupted=False,
            )
        )

        assert result["user_message_id"] == 100
        assert result["assistant_message_id"] == 101

        # 验证 user 消息创建参数
        user_create_call = mock_message_cls.objects.create.call_args_list[0]
        assert user_create_call[1]["role"] == "user"
        assert user_create_call[1]["content"] == "你好世界"
        assert user_create_call[1]["is_voice"] is True
        assert user_create_call[1]["speaker_id"] == "spk_001"
        assert user_create_call[1]["sequence"] == 10

        # 验证 MediaAttachment 创建
        mock_attachment_cls.objects.create.assert_called_once()
        attach_kwargs = mock_attachment_cls.objects.create.call_args[1]
        assert attach_kwargs["media_type"] == "audio"
        assert attach_kwargs["mime_type"] == "audio/wav"
        assert attach_kwargs["file_size"] == 32044
        assert attach_kwargs["duration_seconds"] == 1.0

        # 验证 assistant 消息创建参数
        asst_create_call = mock_message_cls.objects.create.call_args_list[1]
        assert asst_create_call[1]["role"] == "assistant"
        assert asst_create_call[1]["content"] == "你好！有什么可以帮你的？"
        assert asst_create_call[1]["sequence"] == 11  # next_seq + 1
        assert asst_create_call[1]["prompt_tokens"] == 50
        assert asst_create_call[1]["completion_tokens"] == 30
        assert asst_create_call[1]["model_name"] == "minicpm-o"
        assert asst_create_call[1]["response_time_ms"] == 500
        assert asst_create_call[1]["status"] == 1  # STATUS_NORMAL

    @patch("apps.voice.services.voice_session_service.MediaAttachment")
    @patch("apps.voice.services.voice_session_service.Message")
    @patch("apps.voice.services.voice_session_service.transaction")
    def test_atomic_persist_interrupted(
        self, mock_tx, mock_message_cls, mock_attachment_cls, service, user_id
    ):
        """中断消息：assistant status = STATUS_INTERRUPTED"""
        now = timezone.now()
        audio_uuid = str(uuid.uuid4())

        mock_user_msg = MagicMock()
        mock_user_msg.message_id = 200
        mock_user_msg.message_uuid = "uuid-200"
        mock_asst_msg = MagicMock()
        mock_asst_msg.message_id = 201
        mock_asst_msg.message_uuid = "uuid-201"
        mock_message_cls.objects.create.side_effect = [mock_user_msg, mock_asst_msg]
        mock_message_cls.ROLE_USER = "user"
        mock_message_cls.ROLE_ASSISTANT = "assistant"
        mock_message_cls.STATUS_NORMAL = 1
        mock_message_cls.STATUS_INTERRUPTED = 3
        mock_attachment_cls.TYPE_AUDIO = "audio"

        result = run_async(
            service._atomic_persist(
                user_id=user_id,
                user_content="",
                assistant_content="好的...",
                speaker_id=None,
                audio_uuid=audio_uuid,
                storage_path=f"media/{user_id}/2026-02-24/{audio_uuid}.wav",
                wav_size=1000,
                duration=0.5,
                next_seq=20,
                now=now,
                response_usage=None,
                response_time_ms=None,
                is_interrupted=True,
            )
        )

        asst_create_call = mock_message_cls.objects.create.call_args_list[1]
        assert asst_create_call[1]["status"] == 3  # STATUS_INTERRUPTED

    @patch("apps.voice.services.voice_session_service.MediaAttachment")
    @patch("apps.voice.services.voice_session_service.Message")
    @patch("apps.voice.services.voice_session_service.transaction")
    def test_atomic_persist_no_usage(
        self, mock_tx, mock_message_cls, mock_attachment_cls, service, user_id
    ):
        """无 response_usage：tokens 为 0，model_name 为 None"""
        now = timezone.now()
        audio_uuid = str(uuid.uuid4())

        mock_user_msg = MagicMock()
        mock_user_msg.message_id = 300
        mock_user_msg.message_uuid = "uuid-300"
        mock_asst_msg = MagicMock()
        mock_asst_msg.message_id = 301
        mock_asst_msg.message_uuid = "uuid-301"
        mock_message_cls.objects.create.side_effect = [mock_user_msg, mock_asst_msg]
        mock_message_cls.ROLE_USER = "user"
        mock_message_cls.ROLE_ASSISTANT = "assistant"
        mock_message_cls.STATUS_NORMAL = 1
        mock_message_cls.STATUS_INTERRUPTED = 3
        mock_attachment_cls.TYPE_AUDIO = "audio"

        result = run_async(
            service._atomic_persist(
                user_id=user_id,
                user_content="测试",
                assistant_content="回复",
                speaker_id=None,
                audio_uuid=audio_uuid,
                storage_path=f"media/{user_id}/2026-02-24/{audio_uuid}.wav",
                wav_size=500,
                duration=0.1,
                next_seq=1,
                now=now,
                response_usage=None,
                response_time_ms=None,
                is_interrupted=False,
            )
        )

        asst_create_call = mock_message_cls.objects.create.call_args_list[1]
        assert asst_create_call[1]["prompt_tokens"] == 0
        assert asst_create_call[1]["completion_tokens"] == 0
        assert asst_create_call[1]["model_name"] is None

    @patch("apps.voice.services.voice_session_service.MediaAttachment")
    @patch("apps.voice.services.voice_session_service.Message")
    @patch("apps.voice.services.voice_session_service.transaction")
    def test_atomic_persist_expires_at(
        self, mock_tx, mock_message_cls, mock_attachment_cls, service, user_id
    ):
        """验证附件过期时间 = now + MEDIA_EXPIRY_DAYS"""
        now = timezone.now()
        audio_uuid = str(uuid.uuid4())

        mock_user_msg = MagicMock()
        mock_user_msg.message_id = 400
        mock_user_msg.message_uuid = "uuid-400"
        mock_asst_msg = MagicMock()
        mock_asst_msg.message_id = 401
        mock_asst_msg.message_uuid = "uuid-401"
        mock_message_cls.objects.create.side_effect = [mock_user_msg, mock_asst_msg]
        mock_message_cls.ROLE_USER = "user"
        mock_message_cls.ROLE_ASSISTANT = "assistant"
        mock_message_cls.STATUS_NORMAL = 1
        mock_message_cls.STATUS_INTERRUPTED = 3
        mock_attachment_cls.TYPE_AUDIO = "audio"

        run_async(
            service._atomic_persist(
                user_id=user_id,
                user_content="测试",
                assistant_content="回复",
                speaker_id=None,
                audio_uuid=audio_uuid,
                storage_path=f"media/{user_id}/2026-02-24/{audio_uuid}.wav",
                wav_size=500,
                duration=0.1,
                next_seq=1,
                now=now,
                response_usage=None,
                response_time_ms=None,
                is_interrupted=False,
            )
        )

        attach_kwargs = mock_attachment_cls.objects.create.call_args[1]
        expected_expires = now + timedelta(days=settings.MEDIA_EXPIRY_DAYS)
        assert attach_kwargs["expires_at"] == expected_expires


# ========== MinIO 上传测试 ==========


class TestUploadToMinio:
    """_upload_to_minio 测试"""

    @patch("apps.chat.services.minio_service.minio_service")
    def test_upload_to_minio(self, mock_minio, service):
        """验证 MinIO 上传参数"""
        mock_minio.upload_bytes = MagicMock()
        wav_data = VoiceSessionService.merge_pcm_to_wav([_make_pcm_chunk(160)])

        run_async(
            service._upload_to_minio("media/42/2026-02-24/test.wav", wav_data)
        )

        mock_minio.upload_bytes.assert_called_once_with(
            bucket=settings.MINIO_BUCKET_MEDIA,
            object_name="media/42/2026-02-24/test.wav",
            data=wav_data,
            content_type="audio/wav",
        )


# ========== STT 转写测试 ==========


class TestSttTranscription:
    """STT 转写相关测试"""

    @patch("apps.voice.services.voice_session_service.redis_setex", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.asyncio.create_task")
    def test_start_stt_transcription(
        self, mock_create_task, mock_setex, service, user_id, segment_id
    ):
        """启动 STT 转写：设置 pending 状态并创建后台任务"""
        mock_setex.return_value = True

        run_async(service.start_stt_transcription(user_id, segment_id))

        # 验证 pending 状态设置
        pending_key = STT_PENDING_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        mock_setex.assert_called_once_with(pending_key, 60, "pending")

        # 验证后台任务创建
        mock_create_task.assert_called_once()

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_get_stt_result(self, mock_get, service, user_id, segment_id):
        """获取 STT 转写结果"""
        mock_get.return_value = "你好世界"

        result = run_async(service.get_stt_result(user_id, segment_id))

        assert result == "你好世界"
        result_key = STT_RESULT_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        mock_get.assert_called_once_with(result_key)

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_get_stt_result_none(self, mock_get, service, user_id, segment_id):
        """STT 结果不存在时返回 None"""
        mock_get.return_value = None

        result = run_async(service.get_stt_result(user_id, segment_id))

        assert result is None

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_get_stt_status_pending(self, mock_get, service, user_id, segment_id):
        """获取 STT 状态：pending"""
        mock_get.return_value = "pending"

        result = run_async(service.get_stt_status(user_id, segment_id))

        assert result == "pending"

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_get_stt_status_completed(self, mock_get, service, user_id, segment_id):
        """获取 STT 状态：completed"""
        mock_get.return_value = "completed"

        result = run_async(service.get_stt_status(user_id, segment_id))

        assert result == "completed"

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_get_stt_status_failed(self, mock_get, service, user_id, segment_id):
        """获取 STT 状态：failed"""
        mock_get.return_value = "failed"

        result = run_async(service.get_stt_status(user_id, segment_id))

        assert result == "failed"

    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_get_stt_status_none(self, mock_get, service, user_id, segment_id):
        """STT 状态不存在"""
        mock_get.return_value = None

        result = run_async(service.get_stt_status(user_id, segment_id))

        assert result is None


class TestDoStt:
    """_do_stt 内部方法测试"""

    @patch("apps.voice.services.voice_session_service.redis_setex", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.httpx.AsyncClient")
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_do_stt_success(
        self, mock_get_chunks, mock_client_cls, mock_setex, service, user_id, segment_id
    ):
        """STT 转写成功：发送 HTTP 请求并缓存结果"""
        pcm_chunks = [_make_pcm_chunk(16000)]
        mock_get_chunks.return_value = pcm_chunks

        # Mock httpx 响应
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "你好，请查询天气"
                    }
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        mock_setex.return_value = True

        run_async(service._do_stt(user_id, segment_id))

        # 验证结果已缓存
        result_key = STT_RESULT_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        pending_key = STT_PENDING_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        # 应有两次 setex 调用：result + pending=completed
        setex_calls = mock_setex.call_args_list
        result_call = [c for c in setex_calls if c[0][0] == result_key]
        completed_call = [c for c in setex_calls if c[0][0] == pending_key and c[0][2] == "completed"]
        assert len(result_call) == 1
        assert result_call[0][0][2] == "你好，请查询天气"
        assert len(completed_call) == 1

    @patch("apps.voice.services.voice_session_service.redis_setex", new_callable=AsyncMock)
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_do_stt_no_chunks(
        self, mock_get_chunks, mock_setex, service, user_id, segment_id
    ):
        """STT 无音频帧：设置 failed 状态"""
        mock_get_chunks.return_value = []
        mock_setex.return_value = True

        run_async(service._do_stt(user_id, segment_id))

        pending_key = STT_PENDING_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        mock_setex.assert_called_once_with(pending_key, 60, "failed")

    @patch("apps.voice.services.voice_session_service.redis_setex", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.httpx.AsyncClient")
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_do_stt_timeout(
        self, mock_get_chunks, mock_client_cls, mock_setex, service, user_id, segment_id
    ):
        """STT 超时：设置 failed 状态"""
        import httpx

        mock_get_chunks.return_value = [_make_pcm_chunk(160)]

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        mock_setex.return_value = True

        run_async(service._do_stt(user_id, segment_id))

        pending_key = STT_PENDING_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        # 最后一次 setex 调用应为 failed
        last_call = mock_setex.call_args_list[-1]
        assert last_call[0][0] == pending_key
        assert last_call[0][2] == "failed"

    @patch("apps.voice.services.voice_session_service.redis_setex", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.httpx.AsyncClient")
    @patch.object(VoiceSessionService, "get_audio_chunks", new_callable=AsyncMock)
    def test_do_stt_general_exception(
        self, mock_get_chunks, mock_client_cls, mock_setex, service, user_id, segment_id
    ):
        """STT 通用异常：设置 failed 状态"""
        mock_get_chunks.return_value = [_make_pcm_chunk(160)]

        mock_client = AsyncMock()
        mock_client.post.side_effect = RuntimeError("connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        mock_setex.return_value = True

        run_async(service._do_stt(user_id, segment_id))

        pending_key = STT_PENDING_KEY.format(
            user_id=user_id, segment_id=segment_id
        )
        last_call = mock_setex.call_args_list[-1]
        assert last_call[0][0] == pending_key
        assert last_call[0][2] == "failed"


class TestUpdateMessageContent:
    """update_message_content 测试"""

    @patch("apps.voice.services.voice_session_service.message_repo")
    def test_update_message_content(self, mock_message_repo, service):
        """更新消息内容"""
        mock_message_repo.update_content = AsyncMock(return_value=True)

        run_async(service.update_message_content(100, "新的转写内容"))

        mock_message_repo.update_content.assert_called_once_with(100, "新的转写内容")


# ========== LLM 频率限制测试 ==========


class TestCheckLlmRateLimit:
    """check_llm_rate_limit 测试"""

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_rate_limit_first_call(self, mock_get_client, service, user_id):
        """首次调用：计数为 1，设置 TTL，返回 True"""
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis
        mock_redis.incr.return_value = 1

        result = run_async(service.check_llm_rate_limit(user_id))

        assert result is True
        expected_key = LLM_RATE_KEY.format(user_id=user_id)
        mock_redis.incr.assert_called_once_with(expected_key)
        mock_redis.expire.assert_called_once_with(expected_key, 60)
        mock_redis.aclose.assert_called_once()

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_rate_limit_within_limit(self, mock_get_client, service, user_id):
        """在限制内：计数 <= 60 返回 True"""
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis
        mock_redis.incr.return_value = 30

        result = run_async(service.check_llm_rate_limit(user_id))

        assert result is True
        # 非首次调用，不应设置 expire
        mock_redis.expire.assert_not_called()

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_rate_limit_at_boundary(self, mock_get_client, service, user_id):
        """边界值：计数 = 60 返回 True"""
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis
        mock_redis.incr.return_value = 60

        result = run_async(service.check_llm_rate_limit(user_id))

        assert result is True

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_rate_limit_exceeded(self, mock_get_client, service, user_id):
        """超过限制：计数 = 61 返回 False"""
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis
        mock_redis.incr.return_value = 61

        result = run_async(service.check_llm_rate_limit(user_id))

        assert result is False

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_rate_limit_far_exceeded(self, mock_get_client, service, user_id):
        """远超限制：计数 = 200 返回 False"""
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis
        mock_redis.incr.return_value = 200

        result = run_async(service.check_llm_rate_limit(user_id))

        assert result is False

    @patch("core.redis.RedisClient.get_client", new_callable=AsyncMock)
    def test_rate_limit_redis_close(self, mock_get_client, service, user_id):
        """确保 Redis 连接被关闭（finally 块）"""
        mock_redis = AsyncMock()
        mock_get_client.return_value = mock_redis
        mock_redis.incr.return_value = 1

        run_async(service.check_llm_rate_limit(user_id))

        mock_redis.aclose.assert_called_once()


# ========== 单会话强制（集成场景）测试 ==========


class TestSingleSessionEnforcement:
    """FR-034 单会话强制场景测试"""

    @patch("apps.voice.services.voice_session_service.redis_setex", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.redis_delete", new_callable=AsyncMock)
    @patch("apps.voice.services.voice_session_service.redis_get", new_callable=AsyncMock)
    def test_create_then_close_then_create(
        self, mock_get, mock_delete, mock_setex, service, user_id
    ):
        """创建 -> 关闭 -> 再创建：模拟完整生命周期"""
        # 第一次创建：无已有会话
        mock_get.return_value = None
        mock_setex.return_value = True
        result1 = run_async(service.create_session(user_id))
        assert result1 is True

        # 第二次创建：已有会话
        mock_get.return_value = json.dumps({"state": "active"})
        result2 = run_async(service.create_session(user_id))
        assert result2 is False

        # 关闭会话
        mock_delete.return_value = 1
        run_async(service.close_session(user_id))

        # 再次创建：会话已关闭
        mock_get.return_value = None
        result3 = run_async(service.create_session(user_id))
        assert result3 is True


# ========== Redis Key 格式验证测试 ==========


class TestRedisKeyFormat:
    """Redis Key 格式验证"""

    def test_session_key_format(self):
        """会话状态键格式"""
        key = SESSION_KEY.format(user_id=42)
        assert key == "voice:session:42"

    def test_active_conv_key_format(self):
        """活跃对话键格式"""
        key = ACTIVE_CONV_KEY.format(user_id=42)
        assert key == "voice:active_conv:42"

    def test_audio_chunks_key_format(self):
        """音频帧缓存键格式"""
        key = AUDIO_CHUNKS_KEY.format(user_id=42, segment_id="seg_001")
        assert key == "voice:audio_chunks:42:seg_001"

    def test_stt_pending_key_format(self):
        """STT pending 键格式"""
        key = STT_PENDING_KEY.format(user_id=42, segment_id="seg_001")
        assert key == "voice:stt_pending:42:seg_001"

    def test_stt_result_key_format(self):
        """STT result 键格式"""
        key = STT_RESULT_KEY.format(user_id=42, segment_id="seg_001")
        assert key == "voice:stt_result:42:seg_001"

    def test_llm_rate_key_format(self):
        """LLM 频率限制键格式"""
        key = LLM_RATE_KEY.format(user_id=42)
        assert key == "voice:llm_rate:42"
