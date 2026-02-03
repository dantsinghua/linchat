"""Prompt 系统数据结构"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MessageRole(str, Enum):
    """消息角色枚举"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class PromptMessage:
    """Prompt 消息单元"""

    role: MessageRole
    content: str
    name: Optional[str] = None

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {"role": self.role.value, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class RetrievedMemory:
    """召回的记忆条目"""

    content: str
    memory_type: str = "memory"
    relevance_score: float = 0.0
    created_at: Optional[str] = None


@dataclass
class ToolDefinition:
    """工具定义"""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class PromptConfig:
    """Prompt 构建配置"""

    model_name: str = ""
    max_context_window: int = 128000
    effective_window_ratio: float = 0.9
    keep_recent_rounds: int = 2
    max_memory_items: int = 5
    memory_token_budget: int = 2000
    user_id: int = 0
    user_display_name: str = ""
    user_timezone: str = "Asia/Shanghai"

    @property
    def effective_window(self) -> int:
        return int(self.max_context_window * self.effective_window_ratio)


class PromptModule(str, Enum):
    """可注册的功能模块"""

    BASE = "base"
    REASONING = "reasoning"
    TOOL_USAGE = "tool_usage"
    CODE_ASSIST = "code_assist"
    CREATIVE_WRITING = "creative_writing"
    DATA_ANALYSIS = "data_analysis"
    CUSTOM = "custom"
