"""tiktoken 工具模块 — token 计数"""

from __future__ import annotations

import logging
from typing import Sequence

import tiktoken

logger = logging.getLogger(__name__)

_encoder: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def count_tokens(text: str) -> int:
    """计算文本的 token 数"""
    if not text:
        return 0
    try:
        return len(_get_encoder().encode(text))
    except Exception:
        logger.warning("tiktoken encode failed, falling back to char estimate")
        return len(text) // 4


def count_messages_tokens(messages: Sequence[dict[str, str]]) -> int:
    """计算消息列表的总 token 数（含 per-message 开销）"""
    if not messages:
        return 0
    try:
        encoder = _get_encoder()
        total = 0
        for msg in messages:
            total += 4
            content = msg.get("content", "")
            if content:
                total += len(encoder.encode(content))
            role = msg.get("role", "")
            if role:
                total += len(encoder.encode(role))
        return total + 2
    except Exception:
        logger.warning("tiktoken encode failed, falling back to char estimate")
        return sum(len(msg.get("content", "")) // 4 + 4 for msg in messages) + 2
