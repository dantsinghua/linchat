"""语音模式富上下文构建服务

职责：
- 获取用户记忆（skip_vector=True 避免 GPU 互斥）
- 获取最近 10 轮对话历史
- 组装 system_prompt + user_prompt 供 HTTP 多模态推理使用
"""

import logging
from datetime import datetime
from typing import Any

from django.utils import timezone

from apps.chat.repositories import message_repo
from apps.memory.services import MemoryService

logger = logging.getLogger(__name__)


class VoiceContextService:
    """语音交互富上下文构建"""

    async def build_enriched_context(
        self,
        user_id: int,
        query: str,
        username: str,
    ) -> dict[str, str]:
        """构建富上下文 prompt

        Args:
            user_id: 目标用户 ID（声纹识别后的真实用户）
            query: STT 转写文本，用于记忆搜索
            username: 用户名

        Returns:
            {"system_prompt": "...", "user_prompt": "..."}
        """
        # 并行获取记忆和对话历史
        memories = await self._fetch_memories(user_id, query)
        history = await self._fetch_history(user_id)

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(username, memories, history)

        logger.info(
            "Enriched context built: user_id=%s, memories=%d, "
            "history_msgs=%d, query_len=%d",
            user_id,
            len(memories),
            len(history),
            len(query),
        )

        return {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        }

    async def _fetch_memories(
        self, user_id: int, query: str
    ) -> list[dict[str, Any]]:
        """获取用户相关记忆（skip_vector=True 避免 GPU 互斥）"""
        try:
            results = await MemoryService.search_memory(
                user_id=user_id,
                query=query,
                limit=5,
                skip_vector=True,
            )
            return results
        except Exception:
            logger.exception(
                "Failed to fetch memories: user_id=%s", user_id
            )
            return []

    async def _fetch_history(self, user_id: int) -> list[Any]:
        """获取最近 10 轮对话（20 条消息），返回时间正序"""
        try:
            messages = await message_repo.find_latest_by_user(
                user_id=user_id, limit=20
            )
            # find_latest_by_user 返回倒序，reverse 得到正序
            messages.reverse()
            return messages
        except Exception:
            logger.exception(
                "Failed to fetch history: user_id=%s", user_id
            )
            return []

    def _build_system_prompt(self) -> str:
        """构建基础 system prompt"""
        now = timezone.localtime()
        date_str = now.strftime("%Y年%m月%d日")
        time_str = now.strftime("%H:%M")
        weekday_map = {
            0: "周一", 1: "周二", 2: "周三",
            3: "周四", 4: "周五", 5: "周六", 6: "周日",
        }
        weekday = weekday_map[now.weekday()]

        return (
            f"当前时间：{date_str} {weekday} {time_str}（北京时间）\n"
            "你是一个智能语音助手。请用简洁自然的口语化方式回复，"
            "避免过长的文字描述。"
        )

    def _build_user_prompt(
        self,
        username: str,
        memories: list[dict[str, Any]],
        history: list[Any],
    ) -> str:
        """组装 user prompt（上下文 + 指令）"""
        parts = [f"以下为用户 {username} 的语音输入。"]

        # 记忆部分
        parts.append("\n## 用户相关记忆")
        if memories:
            for i, item in enumerate(memories, 1):
                memory = item.get("memory")
                if memory:
                    content = getattr(memory, "content", str(memory))
                    parts.append(f"{i}. {content}")
        else:
            parts.append("无相关记忆。")

        # 对话历史部分
        parts.append("\n## 最近对话")
        if history:
            for msg in history:
                role_label = (
                    "[用户]" if msg.role == "user" else "[助手]"
                )
                content = msg.content or ""
                # 截断过长的单条消息
                if len(content) > 200:
                    content = content[:200] + "..."
                parts.append(f"{role_label}: {content}")
        else:
            parts.append("无历史对话。")

        parts.append(
            "\n请结合以上上下文，用自然口语化方式回复用户的语音输入。"
        )

        return "\n".join(parts)


# 全局实例
voice_context_service = VoiceContextService()
