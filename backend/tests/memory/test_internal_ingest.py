"""内部摄入端点测试 — POST /api/v1/internal/ingest/

安全红线：/api/v1/internal/ 跳过 cookie 中间件，view 必须自行校验设备 token，
token 缺失/无效返回 401（测试 2/3 锁死）。
覆盖：设备 token 鉴权、同步 embed、name 幂等、embed 失败不阻断、绕 celery 门禁、source=oa。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rest_framework.test import APIClient

from apps.memory.models import UserMemory, UserMemoryEmbedding

pytestmark = pytest.mark.django_db

INGEST_URL = "/api/v1/internal/ingest/"
MOCK_EMBEDDING = [0.1] * 1024
USER_ID = 7


def _auth_ok():
    """mock device_service.authenticate_by_token → 认证通过（返回 user_id=7）。"""
    return patch(
        "apps.memory.internal_views.device_service.authenticate_by_token",
        new_callable=AsyncMock,
        return_value={"user_id": USER_ID, "device_uuid": "dev-uuid", "device_name": "wechat-narrator"},
    )


def _auth_fail():
    """mock device_service.authenticate_by_token → 认证失败（返回 None）。"""
    return patch(
        "apps.memory.internal_views.device_service.authenticate_by_token",
        new_callable=AsyncMock,
        return_value=None,
    )


def _mock_embed(return_value=MOCK_EMBEDDING, side_effect=None):
    return patch(
        "apps.memory.services.EmbeddingClient.generate_embedding",
        new_callable=AsyncMock,
        return_value=None if side_effect else return_value,
        side_effect=side_effect,
    )


class TestInternalIngest:
    @pytest.fixture(autouse=True)
    def cleanup(self):
        UserMemoryEmbedding.objects.all().delete()
        UserMemory.objects.all().delete()
        yield
        UserMemoryEmbedding.objects.all().delete()
        UserMemory.objects.all().delete()

    def test_valid_token_creates_wechat_memory(self):
        """1. 有效 token → 201，落库 type=wechat + status=done + 1 条 embedding。"""
        client = APIClient()
        with _auth_ok(), _mock_embed():
            resp = client.post(
                INGEST_URL,
                data={"content": "今天团子很乖", "name": "2026-07-18", "source": "wechat"},
                format="json", HTTP_X_DEVICE_TOKEN="valid-token-abc123",
            )
        assert resp.status_code == 201, resp.content
        body = resp.json()["data"]
        assert body["type"] == "wechat"
        assert body["name"] == "2026-07-18"
        assert body["embedding_status"] == UserMemory.EmbeddingStatus.DONE
        assert body["deduped"] is False
        mems = UserMemory.objects.filter(user_id=USER_ID, type="wechat", name="2026-07-18")
        assert mems.count() == 1
        assert mems[0].content == "今天团子很乖"
        assert UserMemoryEmbedding.objects.filter(memory_id=mems[0].id).count() == 1

    def test_invalid_token_returns_401_no_record(self):
        """2. 无效 token → 401，不落库（安全红线）。"""
        client = APIClient()
        with _auth_fail(), _mock_embed():
            resp = client.post(
                INGEST_URL,
                data={"content": "x", "name": "n", "source": "wechat"},
                format="json", HTTP_X_DEVICE_TOKEN="bad-token",
            )
        assert resp.status_code == 401
        assert UserMemory.objects.count() == 0

    def test_missing_token_returns_401(self):
        """3. 缺 token → 401，不落库（安全红线）。"""
        client = APIClient()
        with _auth_fail(), _mock_embed():
            resp = client.post(
                INGEST_URL,
                data={"content": "x", "name": "n", "source": "wechat"},
                format="json",
            )
        assert resp.status_code == 401
        assert UserMemory.objects.count() == 0

    def test_idempotent_same_name_updates_in_place(self):
        """4. 同 name 二次摄入 → deduped=True，UserMemory 仍 1 条（内容更新），embedding 仍 1 条。"""
        client = APIClient()
        with _auth_ok(), _mock_embed():
            r1 = client.post(
                INGEST_URL,
                data={"content": "第一次内容", "name": "2026-07-18", "source": "wechat"},
                format="json", HTTP_X_DEVICE_TOKEN="valid-token-abc123",
            )
            assert r1.status_code == 201
            assert r1.json()["data"]["deduped"] is False
            r2 = client.post(
                INGEST_URL,
                data={"content": "第二次内容", "name": "2026-07-18", "source": "wechat"},
                format="json", HTTP_X_DEVICE_TOKEN="valid-token-abc123",
            )
        assert r2.status_code == 201
        assert r2.json()["data"]["deduped"] is True
        mems = UserMemory.objects.filter(user_id=USER_ID, type="wechat", name="2026-07-18")
        assert mems.count() == 1
        assert mems[0].content == "第二次内容"
        assert UserMemoryEmbedding.objects.filter(memory_id=mems[0].id).count() == 1

    def test_embed_failure_still_201_status_failed(self):
        """5. embed 失败 → 仍 201，status=failed，memory 已落库（不阻断）。"""
        client = APIClient()
        with _auth_ok(), _mock_embed(side_effect=Exception("embedding down")):
            resp = client.post(
                INGEST_URL,
                data={"content": "内容", "name": "2026-07-18", "source": "wechat"},
                format="json", HTTP_X_DEVICE_TOKEN="valid-token-abc123",
            )
        assert resp.status_code == 201
        assert resp.json()["data"]["embedding_status"] == UserMemory.EmbeddingStatus.FAILED
        mems = UserMemory.objects.filter(user_id=USER_ID, type="wechat", name="2026-07-18")
        assert mems.count() == 1
        assert mems[0].embedding_status == UserMemory.EmbeddingStatus.FAILED
        assert mems[0].retry_count == 1

    def test_bypasses_active_users_celery_gate(self):
        """6. 摄入绕过 celery（has_active_users 门禁）：generate_embedding.delay 从不被调用。"""
        client = APIClient()
        with _auth_ok(), _mock_embed(), \
                patch("apps.memory.tasks.generate_embedding") as mock_task:
            resp = client.post(
                INGEST_URL,
                data={"content": "内容", "name": "2026-07-18", "source": "wechat"},
                format="json", HTTP_X_DEVICE_TOKEN="valid-token-abc123",
            )
        assert resp.status_code == 201
        mock_task.delay.assert_not_called()

    def test_source_oa_creates_oa_memory(self):
        """7. source=oa → type=oa。"""
        client = APIClient()
        with _auth_ok(), _mock_embed():
            resp = client.post(
                INGEST_URL,
                data={"content": "公众号文章要点", "name": "文章标题", "source": "oa"},
                format="json", HTTP_X_DEVICE_TOKEN="valid-token-abc123",
            )
        assert resp.status_code == 201
        assert resp.json()["data"]["type"] == "oa"
        assert UserMemory.objects.filter(user_id=USER_ID, type="oa", name="文章标题").count() == 1
