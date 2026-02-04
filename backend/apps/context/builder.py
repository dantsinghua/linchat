"""PromptBuilder 组装引擎"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from apps.context.loader import render
from apps.context.types import (MessageRole, PromptConfig, PromptMessage,
                                PromptModule, RetrievedMemory, ToolDefinition)

logger = logging.getLogger(__name__)

# 模块 → 模板文件映射
_MODULE_TEMPLATES: dict[PromptModule, str] = {
    PromptModule.BASE: "behavior.j2",
    PromptModule.REASONING: "reasoning.j2",
    PromptModule.TOOL_USAGE: "tool_usage.j2",
    PromptModule.CODE_ASSIST: "code_assist.j2",
    PromptModule.CREATIVE_WRITING: "creative_writing.j2",
    PromptModule.DATA_ANALYSIS: "data_analysis.j2",
}

# 记忆类型标签映射
_MEMORY_TYPE_LABELS: dict[str, str] = {
    "memory": "记忆",
    "compaction": "对话摘要",
    "daily-summary": "每日摘要",
    "monthly-summary": "月度摘要",
}

# 自定义模块注册表
_custom_module_prompts: dict[str, str] = {}


def register_custom_module(name: str, prompt_text: str) -> None:
    """注册自定义 prompt 模块"""
    _custom_module_prompts[name] = prompt_text
    logger.info("Registered custom prompt module: %s", name)


def get_module_prompt(module: PromptModule) -> str:
    """获取模块 prompt 文本（通过 Jinja2 渲染）"""
    tpl = _MODULE_TEMPLATES.get(module)
    return render(tpl) if tpl else ""


def get_custom_module_prompt(name: str) -> str:
    """获取自定义模块 prompt 文本"""
    return _custom_module_prompts.get(name, "")


class PromptBuilder:
    """动态 Prompt 组装引擎

    组装顺序与优先级（token 裁剪时从低优先级开始丢弃）：
        P0 (不可丢弃): 基础 system prompt + 当前用户输入
        P1 (最后丢弃): 最近 N 轮对话历史
        P2 (优先丢弃): 召回记忆 + 压缩摘要
        P3 (可选):     工具定义 + 功能模块 prompt
    """

    def __init__(self, config: Optional[PromptConfig] = None) -> None:
        self.config = config or PromptConfig()
        self._enabled_modules: list[PromptModule] = [
            PromptModule.BASE,
            PromptModule.REASONING,
            PromptModule.TOOL_USAGE,
        ]
        self._custom_modules: list[str] = []
        self._extra_system_instructions: list[str] = []

    def enable_module(self, module: PromptModule) -> "PromptBuilder":
        if module not in self._enabled_modules:
            self._enabled_modules.append(module)
        return self

    def disable_module(self, module: PromptModule) -> "PromptBuilder":
        if module in self._enabled_modules:
            self._enabled_modules.remove(module)
        return self

    def enable_custom_module(self, name: str) -> "PromptBuilder":
        if name not in self._custom_modules:
            self._custom_modules.append(name)
        return self

    def add_system_instruction(self, instruction: str) -> "PromptBuilder":
        self._extra_system_instructions.append(instruction)
        return self

    # ------ 组件构建 ------

    def build_system_prompt(self) -> str:
        parts: list[str] = [
            render(
                "system_base.j2",
                today_date=datetime.now().strftime("%Y-%m-%d"),
                user_timezone=self.config.user_timezone,
            )
        ]

        for module in self._enabled_modules:
            text = get_module_prompt(module)
            if text:
                parts.append(text)

        for name in self._custom_modules:
            text = get_custom_module_prompt(name)
            if text:
                parts.append(text)

        for instruction in self._extra_system_instructions:
            parts.append(f"\n\n# 附加指令\n\n{instruction}")

        return "".join(parts)

    def build_memory_block(
        self, retrieved_memories: Optional[list[RetrievedMemory]] = None
    ) -> Optional[str]:
        if not retrieved_memories:
            return None

        sorted_memories = sorted(
            retrieved_memories, key=lambda m: m.relevance_score, reverse=True
        )
        memories_to_inject = sorted_memories[: self.config.max_memory_items]
        if not memories_to_inject:
            return None

        entries: list[str] = []
        for i, mem in enumerate(memories_to_inject, 1):
            type_label = _MEMORY_TYPE_LABELS.get(mem.memory_type, "记忆")
            time_label = f" ({mem.created_at})" if mem.created_at else ""
            entries.append(f"{i}. [{type_label}]{time_label} {mem.content}")

        return render("memory_context.j2", memory_entries="\n".join(entries))

    def build_compaction_block(
        self, compaction_summary: Optional[str] = None
    ) -> Optional[str]:
        if not compaction_summary:
            return None
        return render("compaction_context.j2", compaction_summary=compaction_summary)

    def build_tool_context(
        self, available_tools: Optional[list[ToolDefinition]] = None
    ) -> Optional[str]:
        if not available_tools:
            return None
        active_tools = [t for t in available_tools if t.enabled]
        if not active_tools:
            return None

        tool_lines: list[str] = []
        for tool in active_tools:
            params_desc = ""
            if tool.parameters:
                param_items = []
                for pname, pinfo in tool.parameters.items():
                    ptype = pinfo.get("type", "any")
                    pdesc = pinfo.get("description", "")
                    required = pinfo.get("required", False)
                    marker = " (必填)" if required else ""
                    param_items.append(f"    - `{pname}` ({ptype}){marker}: {pdesc}")
                params_desc = "\n" + "\n".join(param_items)
            tool_lines.append(f"## {tool.name}\n{tool.description}{params_desc}")

        return render("tool_context.j2", tool_definitions="\n\n".join(tool_lines))

    def build_conversation_history(
        self, conversation_history: Optional[list[dict[str, str]]] = None
    ) -> list[PromptMessage]:
        if not conversation_history:
            return []
        max_messages = self.config.keep_recent_rounds * 2
        recent = conversation_history[-max_messages:]
        return [
            PromptMessage(role=MessageRole(msg["role"]), content=msg["content"])
            for msg in recent
            if msg.get("role") in ("user", "assistant") and msg.get("content")
        ]

    # ------ 主组装方法 ------

    def build_messages(
        self,
        user_input: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
        retrieved_memories: Optional[list[RetrievedMemory]] = None,
        compaction_summary: Optional[str] = None,
        available_tools: Optional[list[ToolDefinition]] = None,
    ) -> list[dict[str, str]]:
        messages: list[PromptMessage] = []

        messages.append(
            PromptMessage(role=MessageRole.SYSTEM, content=self.build_system_prompt())
        )

        compaction_text = self.build_compaction_block(compaction_summary)
        if compaction_text:
            messages.append(
                PromptMessage(
                    role=MessageRole.SYSTEM, content=compaction_text, name="compaction"
                )
            )

        memory_text = self.build_memory_block(retrieved_memories)
        if memory_text:
            messages.append(
                PromptMessage(
                    role=MessageRole.SYSTEM, content=memory_text, name="memory"
                )
            )

        tool_text = self.build_tool_context(available_tools)
        if tool_text:
            messages.append(
                PromptMessage(
                    role=MessageRole.SYSTEM, content=tool_text, name="tools"
                )
            )

        messages.extend(self.build_conversation_history(conversation_history))
        messages.append(PromptMessage(role=MessageRole.USER, content=user_input))

        return [m.to_dict() for m in messages]

    # ------ 主 preamble 方法 ------

    def build_preamble(
        self,
        retrieved_memories: Optional[list[RetrievedMemory]] = None,
        compaction_summary: Optional[str] = None,
        available_tools: Optional[list[ToolDefinition]] = None,
    ) -> list:
        from langchain_core.messages import SystemMessage

        preamble: list[SystemMessage] = []
        preamble.append(SystemMessage(content=self.build_system_prompt()))

        compaction_text = self.build_compaction_block(compaction_summary)
        if compaction_text:
            preamble.append(SystemMessage(content=compaction_text))

        memory_text = self.build_memory_block(retrieved_memories)
        if memory_text:
            preamble.append(SystemMessage(content=memory_text))

        tool_text = self.build_tool_context(available_tools)
        if tool_text:
            preamble.append(SystemMessage(content=tool_text))

        return preamble


# ============================================================================
# 兼容性常量 — 使用 str.format() 占位符，与原 graph/prompts.py 等价
# ============================================================================

COMPACTION_PROMPT_TEMPLATE = render("compaction_task.j2", conversation_text="{conversation_text}")

DAILY_SUMMARY_PROMPT_TEMPLATE = render("daily_summary.j2", conversation_text="{conversation_text}", date="{date}")

MONTHLY_SUMMARY_PROMPT_TEMPLATE = render("monthly_summary.j2", daily_summaries="{daily_summaries}", year_month="{year_month}")

CRONMEM_PROMPT_TEMPLATE = render("cronmem_extract.j2", existing_memories="{existing_memories}", conversation_text="{conversation_text}")

# 旧常量名兼容（供 test_prompts.py 等导入）
BASE_SYSTEM_PROMPT = render("system_base.j2")
BEHAVIOR_GUIDELINES = render("behavior.j2")
REASONING_GUIDELINES = render("reasoning.j2")
TOOL_USAGE_GUIDELINES = render("tool_usage.j2")
MEMORY_CONTEXT_HEADER = render("memory_context.j2")
MEMORY_CONTEXT_EMPTY = render("memory_empty.j2")
TOOL_CONTEXT_HEADER = render("tool_context.j2")
COMPACTION_CONTEXT_HEADER = render("compaction_context.j2")
