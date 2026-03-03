"""
SpeakerService 单元测试

参考: specs/009-voice-interaction/tasks.md#T058

覆盖:
- register_speaker: 声纹注册（新用户 / 已有声纹覆盖 / gateway 错误 / 超时 / HTTP 异常）
- delete_speaker: 声纹删除（正常删除 / 无声纹记录 / gateway 404 容错）
- identify_speaker: 声纹识别（已注册→返回用户信息、未注册→返回 None）
- list_speakers: 声纹查询（有记录→返回字典、无记录→返回 None）
- _delete_gateway_speaker: 内部 gateway 删除（204成功 / 404容错 / 其他状态 / 超时 / HTTP 错误）

Mock 策略: Mock httpx.AsyncClient 外部调用（llmgateway HTTP），
         Mock speaker_profile_repo 数据层调用

覆盖率要求: ≥ 95%
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.voice.services.speaker_service import (
    SpeakerRegistrationError,
    SpeakerService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    """创建 SpeakerService 实例"""
    return SpeakerService()


@pytest.fixture
def sample_audio_data():
    """示例 WAV 音频字节数据"""
    return b"RIFF" + b"\x00" * 100  # 模拟 WAV 头


@pytest.fixture
def mock_speaker_profile():
    """模拟 SpeakerProfile 对象"""
    profile = MagicMock()
    profile.pk = 1
    profile.user_id = 42
    profile.gateway_speaker_id = "gw-speaker-001"
    profile.name = "安琳的声纹"
    profile.quality_score = 0.92
    profile.enrolled_at = datetime(2026, 2, 24, 10, 0, 0)
    # 模拟关联的 user 对象
    profile.user = MagicMock()
    profile.user.username = "anlin"
    return profile


@pytest.fixture
def mock_gateway_settings():
    """Mock Django settings 中的 gateway 配置"""
    with patch(
        "apps.voice.services.speaker_service.settings"
    ) as mock_settings:
        mock_settings.LLM_GATEWAY_URL = "http://test-gateway:8889"
        mock_settings.LLM_GATEWAY_API_KEY = "test-api-key-123"
        yield mock_settings


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_mock_response(status_code: int, json_data: dict = None, text: str = ""):
    """创建模拟的 httpx.Response"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
        resp.content = b"has content"
    else:
        resp.content = b""
        resp.json.side_effect = Exception("No JSON content")
    return resp


# ===========================================================================
# TestRegisterSpeaker
# ===========================================================================


class TestRegisterSpeaker:
    """register_speaker 声纹注册测试"""

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_register_new_speaker_success(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        sample_audio_data,
        mock_gateway_settings,
    ):
        """测试新用户首次注册声纹成功"""
        # 用户无已有声纹
        mock_repo.find_by_user_id = AsyncMock(return_value=None)

        # Mock gateway POST 返回 201
        mock_response = _make_mock_response(
            201,
            json_data={
                "speaker_id": "gw-new-speaker-001",
                "quality_score": 0.88,
            },
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        # Mock 本地创建
        created_profile = MagicMock()
        created_profile.pk = 10
        mock_repo.create = AsyncMock(return_value=created_profile)

        result = await service.register_speaker(
            user_id=42, name="测试声纹", audio_data=sample_audio_data
        )

        assert result["speaker_id"] == "gw-new-speaker-001"
        assert result["quality_score"] == 0.88
        assert result["name"] == "测试声纹"

        # 验证 repo.create 被调用
        mock_repo.create.assert_called_once_with(
            user_id=42,
            gateway_speaker_id="gw-new-speaker-001",
            name="测试声纹",
            quality_score=0.88,
        )

        # 验证没有调用删除（无旧声纹）
        mock_repo.delete_by_user_id.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_register_speaker_replaces_existing(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        sample_audio_data,
        mock_speaker_profile,
        mock_gateway_settings,
    ):
        """测试已有声纹时先删除旧的再创建新的"""
        # 用户已有声纹
        mock_repo.find_by_user_id = AsyncMock(
            return_value=mock_speaker_profile
        )
        mock_repo.delete_by_user_id = AsyncMock(return_value=1)

        # Mock gateway DELETE（删除旧声纹）成功
        mock_delete_response = _make_mock_response(204)

        # Mock gateway POST（注册新声纹）成功
        mock_post_response = _make_mock_response(
            201,
            json_data={
                "speaker_id": "gw-new-speaker-002",
                "quality_score": 0.95,
            },
        )

        # 设置 mock client 根据调用方法返回不同响应
        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_delete_response
        mock_client.post.return_value = mock_post_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        # Mock 本地创建
        created_profile = MagicMock()
        created_profile.pk = 11
        mock_repo.create = AsyncMock(return_value=created_profile)

        result = await service.register_speaker(
            user_id=42, name="新声纹", audio_data=sample_audio_data
        )

        assert result["speaker_id"] == "gw-new-speaker-002"
        assert result["quality_score"] == 0.95
        assert result["name"] == "新声纹"

        # 验证删除旧声纹
        mock_repo.delete_by_user_id.assert_called_once_with(42)

        # 验证创建新声纹
        mock_repo.create.assert_called_once_with(
            user_id=42,
            gateway_speaker_id="gw-new-speaker-002",
            name="新声纹",
            quality_score=0.95,
        )

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_register_speaker_gateway_error_response(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        sample_audio_data,
        mock_gateway_settings,
    ):
        """测试 gateway 返回非 201 错误时抛出 SpeakerRegistrationError"""
        mock_repo.find_by_user_id = AsyncMock(return_value=None)

        # Mock gateway POST 返回 400 错误
        mock_response = _make_mock_response(
            400,
            json_data={
                "error": {
                    "code": "E6001",
                    "message": "音频质量不足",
                }
            },
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        with pytest.raises(SpeakerRegistrationError) as exc_info:
            await service.register_speaker(
                user_id=42, name="测试", audio_data=sample_audio_data
            )

        assert "E6001" in str(exc_info.value)
        assert "音频质量不足" in str(exc_info.value)

        # 验证没有创建本地映射
        mock_repo.create.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_register_speaker_gateway_error_no_json_body(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        sample_audio_data,
        mock_gateway_settings,
    ):
        """测试 gateway 返回非 201 且无 JSON 内容时的错误处理"""
        mock_repo.find_by_user_id = AsyncMock(return_value=None)

        # Mock gateway POST 返回 500，无 content
        mock_response = _make_mock_response(500, json_data=None, text="")
        mock_response.content = b""
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        with pytest.raises(SpeakerRegistrationError) as exc_info:
            await service.register_speaker(
                user_id=42, name="测试", audio_data=sample_audio_data
            )

        assert "unknown" in str(exc_info.value)
        mock_repo.create.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_register_speaker_timeout(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        sample_audio_data,
        mock_gateway_settings,
    ):
        """测试 gateway 请求超时时抛出 SpeakerRegistrationError"""
        mock_repo.find_by_user_id = AsyncMock(return_value=None)

        # Mock client.post 抛出超时异常
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("连接超时")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        with pytest.raises(SpeakerRegistrationError) as exc_info:
            await service.register_speaker(
                user_id=42, name="测试", audio_data=sample_audio_data
            )

        assert "超时" in str(exc_info.value)
        mock_repo.create.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_register_speaker_http_error(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        sample_audio_data,
        mock_gateway_settings,
    ):
        """测试 gateway 网络错误时抛出 SpeakerRegistrationError"""
        mock_repo.find_by_user_id = AsyncMock(return_value=None)

        # Mock client.post 抛出 HTTP 连接错误
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("连接被拒绝")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        with pytest.raises(SpeakerRegistrationError) as exc_info:
            await service.register_speaker(
                user_id=42, name="测试", audio_data=sample_audio_data
            )

        assert "网络错误" in str(exc_info.value)
        mock_repo.create.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_register_speaker_no_quality_score(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        sample_audio_data,
        mock_gateway_settings,
    ):
        """测试 gateway 返回无 quality_score 时正常处理"""
        mock_repo.find_by_user_id = AsyncMock(return_value=None)

        # Gateway 返回不含 quality_score
        mock_response = _make_mock_response(
            201,
            json_data={"speaker_id": "gw-speaker-003"},
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        created_profile = MagicMock()
        created_profile.pk = 12
        mock_repo.create = AsyncMock(return_value=created_profile)

        result = await service.register_speaker(
            user_id=42, name="无评分声纹", audio_data=sample_audio_data
        )

        assert result["speaker_id"] == "gw-speaker-003"
        assert result["quality_score"] is None
        assert result["name"] == "无评分声纹"

        mock_repo.create.assert_called_once_with(
            user_id=42,
            gateway_speaker_id="gw-speaker-003",
            name="无评分声纹",
            quality_score=None,
        )

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_register_speaker_audio_base64_encoding(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        mock_gateway_settings,
    ):
        """测试音频数据正确进行 base64 编码传输"""
        import base64

        audio_data = b"\x00\x01\x02\x03\xff\xfe\xfd"
        expected_b64 = base64.b64encode(audio_data).decode("ascii")

        mock_repo.find_by_user_id = AsyncMock(return_value=None)

        mock_response = _make_mock_response(
            201,
            json_data={"speaker_id": "gw-test", "quality_score": 0.5},
        )
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        created_profile = MagicMock()
        created_profile.pk = 13
        mock_repo.create = AsyncMock(return_value=created_profile)

        await service.register_speaker(
            user_id=42, name="编码测试", audio_data=audio_data
        )

        # 验证 POST 请求的 JSON body 包含正确的 base64 编码
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"]["audio"] == expected_b64
        assert call_kwargs.kwargs["json"]["speaker_id"] is None

        # 验证使用了正确的 URL 和 Authorization header
        assert "/v1/voice/speakers" in call_kwargs.args[0]
        assert (
            call_kwargs.kwargs["headers"]["Authorization"]
            == "Bearer test-api-key-123"
        )


# ===========================================================================
# TestDeleteSpeaker
# ===========================================================================


class TestDeleteSpeaker:
    """delete_speaker 声纹删除测试"""

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_delete_speaker_success(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        mock_speaker_profile,
        mock_gateway_settings,
    ):
        """测试正常删除声纹：调用 gateway DELETE + 删除本地映射"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=mock_speaker_profile
        )
        mock_repo.delete_by_user_id = AsyncMock(return_value=1)

        # Mock gateway DELETE 返回 204
        mock_response = _make_mock_response(204)
        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        result = await service.delete_speaker(user_id=42)

        assert result is True
        mock_repo.delete_by_user_id.assert_called_once_with(42)

        # 验证调用了 gateway DELETE
        mock_client.delete.assert_called_once()
        delete_url = mock_client.delete.call_args.args[0]
        assert "gw-speaker-001" in delete_url

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    async def test_delete_speaker_no_profile(
        self,
        mock_repo,
        service,
        mock_gateway_settings,
    ):
        """测试删除不存在的声纹返回 False"""
        mock_repo.find_by_user_id = AsyncMock(return_value=None)

        result = await service.delete_speaker(user_id=999)

        assert result is False
        mock_repo.delete_by_user_id.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_delete_speaker_gateway_404_still_deletes_local(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        mock_speaker_profile,
        mock_gateway_settings,
    ):
        """测试 gateway 返回 404 时仍然删除本地映射"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=mock_speaker_profile
        )
        mock_repo.delete_by_user_id = AsyncMock(return_value=1)

        # Mock gateway DELETE 返回 404（声纹已不存在）
        mock_response = _make_mock_response(404)
        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        result = await service.delete_speaker(user_id=42)

        assert result is True
        # 即使 gateway 404，本地映射也应被删除
        mock_repo.delete_by_user_id.assert_called_once_with(42)

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_delete_speaker_gateway_timeout_still_deletes_local(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        mock_speaker_profile,
        mock_gateway_settings,
    ):
        """测试 gateway 超时时仍然删除本地映射（不抛出异常）"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=mock_speaker_profile
        )
        mock_repo.delete_by_user_id = AsyncMock(return_value=1)

        # Mock gateway DELETE 超时
        mock_client = AsyncMock()
        mock_client.delete.side_effect = httpx.TimeoutException("超时")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        result = await service.delete_speaker(user_id=42)

        assert result is True
        mock_repo.delete_by_user_id.assert_called_once_with(42)

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_delete_speaker_gateway_http_error_still_deletes_local(
        self,
        mock_async_client_cls,
        mock_repo,
        service,
        mock_speaker_profile,
        mock_gateway_settings,
    ):
        """测试 gateway HTTP 错误时仍然删除本地映射（不抛出异常）"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=mock_speaker_profile
        )
        mock_repo.delete_by_user_id = AsyncMock(return_value=1)

        # Mock gateway DELETE 网络错误
        mock_client = AsyncMock()
        mock_client.delete.side_effect = httpx.ConnectError("连接断开")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        result = await service.delete_speaker(user_id=42)

        assert result is True
        mock_repo.delete_by_user_id.assert_called_once_with(42)


# ===========================================================================
# TestIdentifySpeaker
# ===========================================================================


class TestIdentifySpeaker:
    """identify_speaker 声纹识别测试"""

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    async def test_identify_speaker_found(
        self,
        mock_repo,
        service,
        mock_speaker_profile,
    ):
        """测试已注册的 gateway_speaker_id 返回用户信息"""
        mock_repo.find_by_gateway_speaker_id = AsyncMock(
            return_value=mock_speaker_profile
        )

        result = await service.identify_speaker("gw-speaker-001")

        assert result is not None
        assert result["user_id"] == 42
        assert result["username"] == "anlin"
        assert result["speaker_name"] == "安琳的声纹"

        mock_repo.find_by_gateway_speaker_id.assert_called_once_with(
            "gw-speaker-001"
        )

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    async def test_identify_speaker_not_found(
        self,
        mock_repo,
        service,
    ):
        """测试未注册的 gateway_speaker_id 返回 None"""
        mock_repo.find_by_gateway_speaker_id = AsyncMock(return_value=None)

        result = await service.identify_speaker("gw-unknown-999")

        assert result is None
        mock_repo.find_by_gateway_speaker_id.assert_called_once_with(
            "gw-unknown-999"
        )


# ===========================================================================
# TestListSpeakers
# ===========================================================================


class TestListSpeakers:
    """list_speakers 声纹查询测试"""

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    async def test_list_speakers_found(
        self,
        mock_repo,
        service,
        mock_speaker_profile,
    ):
        """测试有声纹记录时返回信息字典"""
        mock_repo.find_by_user_id = AsyncMock(
            return_value=mock_speaker_profile
        )

        result = await service.list_speakers(user_id=42)

        assert result is not None
        assert result["speaker_id"] == "gw-speaker-001"
        assert result["name"] == "安琳的声纹"
        assert result["quality_score"] == 0.92
        assert result["enrolled_at"] == "2026-02-24T10:00:00"

        mock_repo.find_by_user_id.assert_called_once_with(42)

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    async def test_list_speakers_not_found(
        self,
        mock_repo,
        service,
    ):
        """测试无声纹记录时返回 None"""
        mock_repo.find_by_user_id = AsyncMock(return_value=None)

        result = await service.list_speakers(user_id=999)

        assert result is None
        mock_repo.find_by_user_id.assert_called_once_with(999)

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    async def test_list_speakers_null_enrolled_at(
        self,
        mock_repo,
        service,
    ):
        """测试 enrolled_at 为 None 时正确返回 null"""
        profile = MagicMock()
        profile.gateway_speaker_id = "gw-speaker-002"
        profile.name = "无时间声纹"
        profile.quality_score = 0.75
        profile.enrolled_at = None
        mock_repo.find_by_user_id = AsyncMock(return_value=profile)

        result = await service.list_speakers(user_id=50)

        assert result is not None
        assert result["enrolled_at"] is None
        assert result["speaker_id"] == "gw-speaker-002"
        assert result["quality_score"] == 0.75

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.speaker_profile_repo")
    async def test_list_speakers_null_quality_score(
        self,
        mock_repo,
        service,
    ):
        """测试 quality_score 为 None 时正确返回"""
        profile = MagicMock()
        profile.gateway_speaker_id = "gw-speaker-003"
        profile.name = "无评分声纹"
        profile.quality_score = None
        profile.enrolled_at = datetime(2026, 1, 1, 0, 0, 0)
        mock_repo.find_by_user_id = AsyncMock(return_value=profile)

        result = await service.list_speakers(user_id=51)

        assert result is not None
        assert result["quality_score"] is None
        assert result["name"] == "无评分声纹"


# ===========================================================================
# TestDeleteGatewaySpeaker (内部方法)
# ===========================================================================


class TestDeleteGatewaySpeaker:
    """_delete_gateway_speaker 内部 gateway 删除测试"""

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_delete_gateway_speaker_204_success(
        self,
        mock_async_client_cls,
        service,
        mock_gateway_settings,
    ):
        """测试 gateway 返回 204 成功删除"""
        mock_response = _make_mock_response(204)
        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        # 不应抛出异常
        await service._delete_gateway_speaker("gw-speaker-to-delete")

        mock_client.delete.assert_called_once()
        delete_url = mock_client.delete.call_args.args[0]
        assert "gw-speaker-to-delete" in delete_url
        assert (
            mock_client.delete.call_args.kwargs["headers"]["Authorization"]
            == "Bearer test-api-key-123"
        )

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_delete_gateway_speaker_404_tolerated(
        self,
        mock_async_client_cls,
        service,
        mock_gateway_settings,
    ):
        """测试 gateway 返回 404 时不抛出异常（容错）"""
        mock_response = _make_mock_response(404)
        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        # 不应抛出异常
        await service._delete_gateway_speaker("gw-already-deleted")

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_delete_gateway_speaker_unexpected_status(
        self,
        mock_async_client_cls,
        service,
        mock_gateway_settings,
    ):
        """测试 gateway 返回其他状态码时仅记录日志，不抛出异常"""
        mock_response = _make_mock_response(500, text="Internal Server Error")
        mock_client = AsyncMock()
        mock_client.delete.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        # 不应抛出异常
        await service._delete_gateway_speaker("gw-speaker-500")

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_delete_gateway_speaker_timeout(
        self,
        mock_async_client_cls,
        service,
        mock_gateway_settings,
    ):
        """测试 gateway 超时时仅记录日志，不抛出异常"""
        mock_client = AsyncMock()
        mock_client.delete.side_effect = httpx.TimeoutException("超时")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        # 不应抛出异常
        await service._delete_gateway_speaker("gw-speaker-timeout")

    @pytest.mark.asyncio
    @patch("apps.voice.services.speaker_service.httpx.AsyncClient")
    async def test_delete_gateway_speaker_http_error(
        self,
        mock_async_client_cls,
        service,
        mock_gateway_settings,
    ):
        """测试 gateway HTTP 错误时仅记录日志，不抛出异常"""
        mock_client = AsyncMock()
        mock_client.delete.side_effect = httpx.ConnectError("连接被拒绝")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_async_client_cls.return_value = mock_client

        # 不应抛出异常
        await service._delete_gateway_speaker("gw-speaker-conn-error")


# ===========================================================================
# TestSpeakerRegistrationError
# ===========================================================================


class TestSpeakerRegistrationError:
    """SpeakerRegistrationError 异常测试"""

    def test_error_is_exception(self):
        """测试 SpeakerRegistrationError 是 Exception 子类"""
        error = SpeakerRegistrationError("测试错误消息")
        assert isinstance(error, Exception)
        assert str(error) == "测试错误消息"

    def test_error_can_be_raised_and_caught(self):
        """测试异常可以被正确捕获"""
        with pytest.raises(SpeakerRegistrationError):
            raise SpeakerRegistrationError("声纹注册失败")


# ===========================================================================
# TestGetGatewayUrlAndApiKey
# ===========================================================================


class TestGatewayConfig:
    """Gateway 配置读取测试（通过 mock settings 验证）"""

    def test_gateway_url_from_settings(self, mock_gateway_settings):
        """测试 speaker_service 模块内的 settings 包含正确的 gateway URL"""
        assert mock_gateway_settings.LLM_GATEWAY_URL == "http://test-gateway:8889"

    def test_api_key_from_settings(self, mock_gateway_settings):
        """测试 speaker_service 模块内的 settings 包含正确的 API key"""
        assert mock_gateway_settings.LLM_GATEWAY_API_KEY == "test-api-key-123"
