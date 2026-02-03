"""Token 裁剪优先级 [T042]"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import apps.common.tokenizer as _tok


class TrimLevel(IntEnum):
    """裁剪优先级：L 越小越先被裁剪"""

    PROTECTED = 0  # 不可丢弃
    FIRST = 1  # 对话历史 — 最先压缩
    SECOND = 2  # 工具内容 — 其次压缩
    LAST = 3  # 记忆内容 — 最后压缩


@dataclass
class TaggedMessage:
    """带裁剪优先级标签的消息"""

    message: dict[str, str]
    level: TrimLevel


def trim_messages_to_budget(
    messages: list[dict[str, str]],
    token_budget: int,
) -> list[dict[str, str]]:
    """按优先级裁剪消息列表，使总 token 数不超过预算 [T042]"""
    total = sum(_tok.count_tokens(m.get("content", "")) for m in messages)
    if total <= token_budget:
        return messages

    # 标记优先级
    tagged: list[TaggedMessage] = []
    for msg in messages:
        role = msg.get("role", "")
        name = msg.get("name", "")
        if role in ("user", "assistant") and name not in ("memory", "tools"):
            level = TrimLevel.FIRST
        elif name == "tools":
            level = TrimLevel.SECOND
        elif name in ("memory", "compaction"):
            level = TrimLevel.LAST
        else:
            level = TrimLevel.PROTECTED
        tagged.append(TaggedMessage(message=msg, level=level))

    # 最后一条 user 消息标记为 PROTECTED
    for i in range(len(tagged) - 1, -1, -1):
        if tagged[i].message.get("role") == "user":
            tagged[i].level = TrimLevel.PROTECTED
            break

    # 按 L1→L2→L3 顺序移除
    for trim_level in (TrimLevel.FIRST, TrimLevel.SECOND, TrimLevel.LAST):
        if total <= token_budget:
            break
        remaining: list[TaggedMessage] = []
        for t in tagged:
            if t.level == trim_level and total > token_budget:
                total -= _tok.count_tokens(t.message.get("content", ""))
            else:
                remaining.append(t)
        tagged = remaining

    return [t.message for t in tagged]
