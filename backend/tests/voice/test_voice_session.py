"""语音会话服务测试

参考:
- specs/010-voice-agent-pipeline/tasks.md
- apps/voice/services/voice_session_service.py

覆盖:
1. Redis 会话状态创建/读取/删除
2. 单会话强制（新会话覆盖旧会话）
3. TTL 过期验证
4. 活跃对话标记设置与过期
5. 音频帧缓存累积
6. LLM 频率限制检查 check_llm_rate_limit
7. PCM→WAV 转换 + 时长计算（VoicePersistService）
8. MinIO 上传/删除（VoicePersistService）

Mock 策略: Mock Redis（core.redis 的异步方法）和 MinIO。
覆盖率要求: >= 95%
"""

import json
import struct
import wave
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.conf import settings

from apps.voice.services.voice_persist_service import VoicePersistService
from apps.voice.services.voice_session_service import (
    _A as ACTIVE_CONV_KEY,
    _AC as AUDIO_CHUNKS_KEY,
    _LR as LLM_RATE_KEY,
    _S as SESSION_KEY,
    VoiceSessionService,
)
from tests.helpers import run_async


# ========== 通用 fixture ==========


@pytest.fixture
def service():
    """创建 VoiceSessionService 实例"""
    return VoiceSessionService()


@pytest.fixture
def persist_service():
    """创建 VoicePersistService 实例"""
    return VoicePersistService()


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
        wav_data = VoicePersistService.merge_pcm_to_wav([pcm])

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
        wav_data = VoicePersistService.merge_pcm_to_wav([chunk1, chunk2])

        buf = BytesIO(wav_data)
        with wave.open(buf, "rb") as wf:
            assert wf.getnframes() == 480  # 160 + 320

    def test_merge_empty_chunks(self):
        """合并空 PCM 列表"""
        wav_data = VoicePersistService.merge_pcm_to_wav([])

        buf = BytesIO(wav_data)
        with wave.open(buf, "rb") as wf:
            assert wf.getnframes() == 0

    def test_wav_starts_with_riff(self):
        """验证 WAV 文件以 RIFF 头开始"""
        pcm = _make_pcm_chunk(160)
        wav_data = VoicePersistService.merge_pcm_to_wav([pcm])

        assert wav_data[:4] == b"RIFF"


class TestCalculateDuration:
    """calculate_duration 测试"""

    def test_duration_16khz_mono(self):
        """16kHz 单声道时长计算"""
        # 16000 samples = 1 second, 每个 sample 2 bytes
        one_second_pcm = _make_pcm_chunk(16000)
        duration = VoicePersistService.calculate_duration([one_second_pcm])
        assert abs(duration - 1.0) < 1e-6

    def test_duration_half_second(self):
        """半秒音频时长计算"""
        half_second_pcm = _make_pcm_chunk(8000)
        duration = VoicePersistService.calculate_duration([half_second_pcm])
        assert abs(duration - 0.5) < 1e-6

    def test_duration_multiple_chunks(self):
        """多帧累加时长计算"""
        chunk1 = _make_pcm_chunk(16000)  # 1s
        chunk2 = _make_pcm_chunk(8000)  # 0.5s
        duration = VoicePersistService.calculate_duration([chunk1, chunk2])
        assert abs(duration - 1.5) < 1e-6

    def test_duration_empty(self):
        """空音频时长为 0"""
        duration = VoicePersistService.calculate_duration([])
        assert duration == 0.0


# ========== MinIO 上传/删除测试 ==========


class TestUploadToMinio:
    """upload_to_minio / delete_from_minio 测试"""

    @patch("apps.voice.services.voice_persist_service.sync_to_async")
    def test_upload_to_minio(self, mock_sync_to_async, persist_service):
        """验证 MinIO 上传参数"""
        mock_upload_fn = MagicMock()
        mock_sync_to_async.return_value = AsyncMock(side_effect=mock_upload_fn)
        wav_data = VoicePersistService.merge_pcm_to_wav([_make_pcm_chunk(160)])

        run_async(
            VoicePersistService.upload_to_minio(
                "media/42/2026-02-24/test.wav", wav_data
            )
        )

        mock_sync_to_async.assert_called_once()

    @patch("apps.voice.services.voice_persist_service.sync_to_async")
    def test_delete_from_minio(self, mock_sync_to_async, persist_service):
        """验证 MinIO 删除（补偿删除）"""
        mock_sync_to_async.return_value = AsyncMock()

        run_async(
            VoicePersistService.delete_from_minio("media/42/2026-02-24/test.wav")
        )

        mock_sync_to_async.assert_called_once()

    @patch("apps.voice.services.voice_persist_service.sync_to_async")
    def test_delete_from_minio_swallows_exception(self, mock_sync_to_async, persist_service):
        """MinIO 删除失败时不抛出异常（补偿操作容错）"""
        mock_sync_to_async.return_value = AsyncMock(
            side_effect=Exception("MinIO unreachable")
        )

        # 不应抛出异常
        run_async(
            VoicePersistService.delete_from_minio("media/42/2026-02-24/test.wav")
        )


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

    def test_llm_rate_key_format(self):
        """LLM 频率限制键格式"""
        key = LLM_RATE_KEY.format(user_id=42)
        assert key == "voice:llm_rate:42"
