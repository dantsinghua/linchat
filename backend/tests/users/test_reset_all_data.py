"""
reset_all_data management command 测试（015-family-multiuser）

覆盖:
- 空库执行不报错
- 管理员账户创建成功（username=anlin, member_type=member, status=1）

注意: Gateway 声纹注册 API 使用 mock。
"""
import tempfile
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from apps.users.crypto import sm3_hash
from apps.users.models import SysUser
from apps.voice.models import SpeakerProfile


def _mock_httpx_client_success():
    """mock httpx.Client，声纹注册成功"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "speaker_id": "spk_reset_admin",
        "quality_score": 0.92,
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    return mock_client


@pytest.mark.django_db
class TestResetAllData:
    """reset_all_data 命令测试"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        yield
        # 清理测试产生的数据
        SpeakerProfile.objects.filter(user__username="anlin").delete()
        SysUser.objects.filter(username="anlin").delete()

    @patch("apps.users.management.commands.reset_all_data.minio_service")
    @patch("apps.users.management.commands.reset_all_data.httpx.Client")
    def test_empty_db_no_error(self, mock_client_cls, mock_minio):
        """空库执行不报错"""
        mock_client_cls.return_value = _mock_httpx_client_success()
        mock_minio.client.bucket_exists.return_value = False

        # 创建临时音频文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"RIFF" + b"\x00" * 100)
            audio_path = f.name

        out = StringIO()
        call_command(
            "reset_all_data",
            password="Test@123456",
            audio=audio_path,
            yes=True,
            stdout=out,
        )
        output = out.getvalue()
        assert "全量清库完成" in output

    @patch("apps.users.management.commands.reset_all_data.minio_service")
    @patch("apps.users.management.commands.reset_all_data.httpx.Client")
    def test_admin_created_successfully(self, mock_client_cls, mock_minio):
        """管理员账户创建成功"""
        mock_client_cls.return_value = _mock_httpx_client_success()
        mock_minio.client.bucket_exists.return_value = False

        password = "MyAdm1n@Pass"
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"RIFF" + b"\x00" * 100)
            audio_path = f.name

        out = StringIO()
        call_command(
            "reset_all_data",
            password=password,
            audio=audio_path,
            yes=True,
            stdout=out,
        )

        # 验证管理员账户
        admin = SysUser.objects.get(username="anlin")
        assert admin.member_type == "member"
        assert admin.type == "admin"
        assert admin.status == 1
        assert admin.password_hash == sm3_hash(password)

        # 验证 SpeakerProfile
        profile = SpeakerProfile.objects.get(user=admin)
        assert profile.gateway_speaker_id == "spk_reset_admin"
