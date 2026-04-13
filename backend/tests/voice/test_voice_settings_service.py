"""VoiceSettingsService 单元测试

覆盖:
- get_settings: 已有设置直接返回 / 不存在时自动创建并记日志 (lines 31-34)
- update_settings: 完整更新流程 — get_or_create + update + get_or_create + log (lines 47-61)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.voice.services.voice_settings_service import VoiceSettingsService

_MODULE = "apps.voice.services.voice_settings_service"


@pytest.fixture
def service():
    return VoiceSettingsService()


@pytest.fixture
def mock_settings_obj():
    obj = MagicMock()
    obj.wake_words = []
    obj.recording_mode = "hold"
    obj.vad_sensitivity = 0.5
    return obj


# ===========================================================================
# get_settings
# ===========================================================================


class TestGetSettings:
    """get_settings — 获取或自动创建语音设置。"""

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.voice_settings_repo")
    async def test_get_settings_existing(self, mock_repo, service, mock_settings_obj):
        """已有设置时直接返回，created=False，不记 info 日志。"""
        mock_repo.get_or_create = AsyncMock(return_value=(mock_settings_obj, False))

        result = await service.get_settings(user_id=42)

        assert result is mock_settings_obj
        mock_repo.get_or_create.assert_called_once_with(42)

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.voice_settings_repo")
    async def test_get_settings_auto_created(self, mock_repo, service, mock_settings_obj):
        """不存在时自动创建，created=True，logger.info 被调用 (line 33)。"""
        mock_repo.get_or_create = AsyncMock(return_value=(mock_settings_obj, True))

        with patch(f"{_MODULE}.logger") as mock_logger:
            result = await service.get_settings(user_id=99)

        assert result is mock_settings_obj
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0]
        assert "auto-created" in call_args[0]
        assert 99 in call_args or "99" in str(call_args)

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.voice_settings_repo")
    async def test_get_settings_not_created_no_log(self, mock_repo, service, mock_settings_obj):
        """created=False 时 logger.info 不被调用。"""
        mock_repo.get_or_create = AsyncMock(return_value=(mock_settings_obj, False))

        with patch(f"{_MODULE}.logger") as mock_logger:
            await service.get_settings(user_id=42)

        mock_logger.info.assert_not_called()


# ===========================================================================
# update_settings
# ===========================================================================


class TestUpdateSettings:
    """update_settings — 确保存在 + 更新字段 + 返回最新设置。"""

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.voice_settings_repo")
    async def test_update_settings_calls_repo_sequence(
        self, mock_repo, service, mock_settings_obj
    ):
        """update_settings 调用顺序: get_or_create → update → get_or_create。"""
        updated_obj = MagicMock()
        call_seq = []

        async def fake_get_or_create(uid):
            call_seq.append("get_or_create")
            return (mock_settings_obj, False)

        async def fake_update(uid, **kwargs):
            call_seq.append(f"update:{list(kwargs.keys())}")

        mock_repo.get_or_create = AsyncMock(side_effect=fake_get_or_create)
        mock_repo.update = AsyncMock(side_effect=fake_update)

        await service.update_settings(user_id=42, vad_sensitivity=0.8)

        assert call_seq[0] == "get_or_create"
        assert "update:['vad_sensitivity']" in call_seq[1]
        assert call_seq[2] == "get_or_create"

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.voice_settings_repo")
    async def test_update_settings_returns_latest(self, mock_repo, service):
        """update_settings 返回第二次 get_or_create 拿到的对象。"""
        first_obj = MagicMock()
        second_obj = MagicMock()
        call_count = [0]

        async def fake_get_or_create(uid):
            call_count[0] += 1
            if call_count[0] == 1:
                return (first_obj, False)
            return (second_obj, False)

        mock_repo.get_or_create = AsyncMock(side_effect=fake_get_or_create)
        mock_repo.update = AsyncMock()

        result = await service.update_settings(user_id=42, recording_mode="toggle")

        assert result is second_obj

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.voice_settings_repo")
    async def test_update_settings_passes_kwargs_to_update(
        self, mock_repo, service, mock_settings_obj
    ):
        """kwargs 原样传给 repo.update。"""
        mock_repo.get_or_create = AsyncMock(return_value=(mock_settings_obj, False))
        mock_repo.update = AsyncMock()

        await service.update_settings(user_id=42, wake_words=["hey"], vad_sensitivity=0.9)

        mock_repo.update.assert_called_once_with(42, wake_words=["hey"], vad_sensitivity=0.9)

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.voice_settings_repo")
    async def test_update_settings_logs_info(self, mock_repo, service, mock_settings_obj):
        """update_settings 执行后记录 info 日志 (lines 55-59)。"""
        mock_repo.get_or_create = AsyncMock(return_value=(mock_settings_obj, False))
        mock_repo.update = AsyncMock()

        with patch(f"{_MODULE}.logger") as mock_logger:
            await service.update_settings(user_id=7, vad_sensitivity=0.3)

        mock_logger.info.assert_called_once()
        log_args = mock_logger.info.call_args[0]
        assert "updated" in log_args[0]

    @pytest.mark.asyncio
    @patch(f"{_MODULE}.voice_settings_repo")
    async def test_update_settings_multiple_fields(self, mock_repo, service, mock_settings_obj):
        """多字段更新时 fields 列表在日志中反映。"""
        mock_repo.get_or_create = AsyncMock(return_value=(mock_settings_obj, False))
        mock_repo.update = AsyncMock()

        logged_fields = []

        def capture_log(msg, *args, **kwargs):
            if len(args) >= 2:
                logged_fields.extend(args[1])

        with patch(f"{_MODULE}.logger") as mock_logger:
            mock_logger.info.side_effect = capture_log
            await service.update_settings(
                user_id=5, vad_sensitivity=0.7, recording_mode="toggle"
            )

        assert "vad_sensitivity" in logged_fields
        assert "recording_mode" in logged_fields
