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


@dataclass
class TokenBreakdown:
    """上下文 token 分部计数

    静态部分（构建上下文时填充）：前 6 个字段
    动态部分（Agent 执行中累加）：tool_calls / tool_results / tool_call_count
    """

    system_prompt: int = 0
    history_messages: int = 0
    retrieved_memories: int = 0
    compaction_summary: int = 0
    tool_definitions: int = 0
    user_input: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    tool_call_count: int = 0

    @property
    def total(self) -> int:
        """所有字段之和"""
        return (
            self.system_prompt
            + self.history_messages
            + self.retrieved_memories
            + self.compaction_summary
            + self.tool_definitions
            + self.user_input
            + self.tool_calls
            + self.tool_results
        )

    def usage_ratio(self, max_tokens: int) -> float:
        """上下文使用率，max_tokens <= 0 时返回 0.0"""
        if max_tokens <= 0:
            return 0.0
        return self.total / max_tokens

    def to_dict(self) -> dict[str, int]:
        """序列化为扁平字典，键名使用简短别名"""
        return {
            "system_prompt": self.system_prompt,
            "history": self.history_messages,
            "memories": self.retrieved_memories,
            "compaction": self.compaction_summary,
            "tool_defs": self.tool_definitions,
            "user_input": self.user_input,
            "tool_calls": self.tool_calls,
            "tool_results": self.tool_results,
            "tool_count": self.tool_call_count,
            "total": self.total,
        }


class PromptModule(str, Enum):
    """可注册的功能模块"""

    BASE = "base"
    REASONING = "reasoning"
    TOOL_USAGE = "tool_usage"
    CODE_ASSIST = "code_assist"
    CREATIVE_WRITING = "creative_writing"
    DATA_ANALYSIS = "data_analysis"
    CUSTOM = "custom"
