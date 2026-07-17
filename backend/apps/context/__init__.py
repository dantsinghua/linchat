"""Prompt 与上下文管理模块

公共 API 重新导出，兼容现有 import 路径。
"""

# 数据结构
from apps.context.types import (MessageRole, PromptConfig, PromptMessage,
                                PromptModule, RetrievedMemory, ToolDefinition)

# 构建器
from apps.context.builder import (BASE_SYSTEM_PROMPT,
                                  BEHAVIOR_GUIDELINES,
                                  COMPACTION_CONTEXT_HEADER,
                                  COMPACTION_PROMPT_TEMPLATE,
                                  CRONMEM_PROMPT_TEMPLATE,
                                  DAILY_SUMMARY_PROMPT_TEMPLATE,
                                  MEMORY_CONTEXT_EMPTY,
                                  MEMORY_CONTEXT_HEADER,
                                  MONTHLY_SUMMARY_PROMPT_TEMPLATE,
                                  PromptBuilder, REASONING_GUIDELINES,
                                  TOOL_CONTEXT_HEADER,
                                  TOOL_USAGE_GUIDELINES,
                                  get_custom_module_prompt,
                                  get_module_prompt, register_custom_module)

# 裁剪
from apps.context.trimmer import TaggedMessage, TrimLevel, trim_messages_to_budget

# Token 计数
from apps.common.tokenizer import count_messages_tokens, count_tokens

# 模板渲染
from apps.context.loader import render as render_template

__all__ = [
    # types
    "MessageRole",
    "PromptMessage",
    "PromptConfig",
    "RetrievedMemory",
    "ToolDefinition",
    "PromptModule",
    # builder
    "PromptBuilder",
    "register_custom_module",
    "get_module_prompt",
    "get_custom_module_prompt",
    # prompt templates & constants (compat)
    "BASE_SYSTEM_PROMPT",
    "BEHAVIOR_GUIDELINES",
    "REASONING_GUIDELINES",
    "TOOL_USAGE_GUIDELINES",
    "MEMORY_CONTEXT_HEADER",
    "MEMORY_CONTEXT_EMPTY",
    "TOOL_CONTEXT_HEADER",
    "COMPACTION_CONTEXT_HEADER",
    "COMPACTION_PROMPT_TEMPLATE",
    "DAILY_SUMMARY_PROMPT_TEMPLATE",
    "MONTHLY_SUMMARY_PROMPT_TEMPLATE",
    "CRONMEM_PROMPT_TEMPLATE",
    # trimmer
    "TrimLevel",
    "TaggedMessage",
    "trim_messages_to_budget",
    # tokenizer
    "count_tokens",
    "count_messages_tokens",
    # loader
    "render_template",
]
