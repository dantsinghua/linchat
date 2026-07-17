"""AmbientLightPipeline 单元测试（batch-08）。

覆盖：
- _build_messages：system + 最近 N 轮历史（reverse/过滤/去 [已中断]）+ 当前用户消息
- stream：首 token 建消息、持久化、request_id 首块、done 收尾
- 无 content：不建消息、不持久化
- LLM 异常分类（map_llm_exception）→ error chunk
- 无激活模型 → error chunk
"""

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from apps.chat.models import Message
from apps.chat.services.types import StreamChunk

_ALS = "apps.voice.services.ambient_light_service"
_MODEL_CONFIG = {
    "url": "http://gw.local", "name": "kimi-k2.5",
    "api_key": "plain-key", "max_output_tokens": 512,
}


def _msg(role: str, content: str) -> MagicMock:
    m = MagicMock()
    m.role = role
    m.content = content
    return m


async def _agen_from(items):
    for it in items:
        yield it


async def _agen_raises(exc):
    raise exc
    yield  # pragma: no cover - 使函数成为 async generator


async def _collect(agen):
    return [c async for c in agen]


# ──────────────────────────────────────────────
# _build_messages
# ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestBuildMessages:

    async def test_structure_and_history_filtering(self):
        # find_latest_by_user 按 -created_time 倒序（最新在前）
        history_desc = [
            _msg("assistant", "回复B[已中断]"),
            _msg("user", "问B"),
            _msg("assistant", "回复A"),
            _msg("user", "问A"),
        ]
        with (
            patch(f"{_ALS}.message_repo") as repo,
            patch(f"{_ALS}.settings") as st,
        ):
            st.VOICE_AMBIENT_LIGHT_HISTORY_ROUNDS = 3
            st.TIME_ZONE = "Asia/Shanghai"
            repo.find_latest_by_user = AsyncMock(return_value=history_desc)

            from apps.voice.services.ambient_light_service import AmbientLightPipeline
            messages = await AmbientLightPipeline._build_messages(1, "帮我开灯")

        # limit = rounds * 2 = 6
        repo.find_latest_by_user.assert_awaited_once_with(1, limit=6)
        assert messages[0]["role"] == "system"
        # 历史按时间正序展开，[已中断] 后缀被去除
        assert messages[1:] == [
            {"role": "user", "content": "问A"},
            {"role": "assistant", "content": "回复A"},
            {"role": "user", "content": "问B"},
            {"role": "assistant", "content": "回复B"},
            {"role": "user", "content": "帮我开灯"},
        ]

    async def test_empty_content_history_skipped(self):
        history_desc = [_msg("assistant", ""), _msg("user", "")]
        with (
            patch(f"{_ALS}.message_repo") as repo,
            patch(f"{_ALS}.settings") as st,
        ):
            st.VOICE_AMBIENT_LIGHT_HISTORY_ROUNDS = 3
            st.TIME_ZONE = "Asia/Shanghai"
            repo.find_latest_by_user = AsyncMock(return_value=history_desc)

            from apps.voice.services.ambient_light_service import AmbientLightPipeline
            messages = await AmbientLightPipeline._build_messages(1, "你好")

        assert messages[0]["role"] == "system"
        assert messages[-1] == {"role": "user", "content": "你好"}
        # 空 content 历史被过滤，只剩 system + 当前 user
        assert len(messages) == 2


# ──────────────────────────────────────────────
# stream
# ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestStream:

    async def test_creates_and_persists_messages(self):
        assistant_msg = MagicMock(spec=Message)
        assistant_msg.message_id = 7
        user_msg = MagicMock(spec=Message)

        with (
            patch(f"{_ALS}.model_service") as ms,
            patch(f"{_ALS}.message_repo") as repo,
            patch(f"{_ALS}.user_repo") as urepo,
            patch(f"{_ALS}.create_first_token_messages",
                  new=AsyncMock(return_value=(user_msg, assistant_msg))) as cft,
            patch(f"{_ALS}.AmbientLightPipeline._build_messages",
                  new=AsyncMock(return_value=[{"role": "user", "content": "帮我开灯"}])),
            patch(f"{_ALS}.AmbientLightPipeline._call_gateway") as cg,
        ):
            ms.get_active_model = MagicMock(return_value=_MODEL_CONFIG)
            repo.get_max_sequence = AsyncMock(return_value=4)
            repo.update = AsyncMock()
            urepo.add_message_count = AsyncMock()
            urepo.add_tokens = AsyncMock()
            cg.return_value = _agen_from([
                ("好", None),
                ("的", {"prompt_tokens": 10, "completion_tokens": 5}),
            ])

            from apps.voice.services.ambient_light_service import AmbientLightPipeline
            chunks = await _collect(AmbientLightPipeline.stream(1, "reqL", "帮我开灯"))

        # content 两块 + done
        assert [c.type for c in chunks] == ["content", "content", "done"]
        # 首块带 request_id
        assert chunks[0].request_id == "reqL"
        assert chunks[0].message_id == 7
        assert chunks[1].request_id is None
        assert chunks[-1].message_id == 7
        # 建消息一次
        cft.assert_awaited_once()
        # 持久化：assistant 落库 + 计数 + tokens
        repo.update.assert_awaited_once()
        assert assistant_msg.content == "好的"
        assert assistant_msg.status == Message.STATUS_NORMAL
        urepo.add_message_count.assert_awaited_once_with(1, 2)
        urepo.add_tokens.assert_awaited_once_with(1, 15)

    async def test_no_content_no_messages_created(self):
        with (
            patch(f"{_ALS}.model_service") as ms,
            patch(f"{_ALS}.message_repo") as repo,
            patch(f"{_ALS}.user_repo") as urepo,
            patch(f"{_ALS}.create_first_token_messages", new=AsyncMock()) as cft,
            patch(f"{_ALS}.AmbientLightPipeline._build_messages",
                  new=AsyncMock(return_value=[])),
            patch(f"{_ALS}.AmbientLightPipeline._call_gateway") as cg,
        ):
            ms.get_active_model = MagicMock(return_value=_MODEL_CONFIG)
            repo.get_max_sequence = AsyncMock(return_value=0)
            urepo.add_message_count = AsyncMock()
            cg.return_value = _agen_from([])

            from apps.voice.services.ambient_light_service import AmbientLightPipeline
            chunks = await _collect(AmbientLightPipeline.stream(1, "req", "你好"))

        assert [c.type for c in chunks] == ["done"]
        assert chunks[0].message_id is None
        cft.assert_not_awaited()
        urepo.add_message_count.assert_not_awaited()

    async def test_llm_exception_mapped_to_error_chunk(self):
        with (
            patch(f"{_ALS}.model_service") as ms,
            patch(f"{_ALS}.message_repo") as repo,
            patch(f"{_ALS}.AmbientLightPipeline._build_messages",
                  new=AsyncMock(return_value=[])),
            patch(f"{_ALS}.AmbientLightPipeline._call_gateway") as cg,
        ):
            ms.get_active_model = MagicMock(return_value=_MODEL_CONFIG)
            repo.get_max_sequence = AsyncMock(return_value=0)
            cg.return_value = _agen_raises(httpx.TimeoutException("timeout"))

            from apps.voice.services.ambient_light_service import AmbientLightPipeline
            chunks = await _collect(AmbientLightPipeline.stream(1, "req", "你好"))

        assert [c.type for c in chunks] == ["error"]
        # map_llm_exception 把 httpx.TimeoutException 归类为 LLMTimeoutError
        assert "超时" in chunks[0].content

    async def test_no_active_model_yields_error(self):
        with patch(f"{_ALS}.model_service") as ms:
            ms.get_active_model = MagicMock(return_value=None)

            from apps.voice.services.ambient_light_service import AmbientLightPipeline
            chunks = await _collect(AmbientLightPipeline.stream(1, "req", "你好"))

        assert len(chunks) == 1
        assert chunks[0].type == "error"
        assert isinstance(chunks[0], StreamChunk)
