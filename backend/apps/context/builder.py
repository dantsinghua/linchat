from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from apps.context.builder_helpers import (_MEMORY_TYPE_LABELS,  # noqa: F401
                                          format_memory_block, format_tool_context, pair_conversation_turns)
from apps.context.loader import render
from apps.context.types import (MessageRole, PromptConfig, PromptMessage, PromptModule,
                                RetrievedMemory, TokenBreakdown, ToolDefinition)

logger = logging.getLogger(__name__)

_PM = PromptModule
_MODULE_TEMPLATES: dict[PromptModule, str] = {
    _PM.BASE: "behavior.j2", _PM.REASONING: "reasoning.j2", _PM.TOOL_USAGE: "tool_usage.j2",
    _PM.CODE_ASSIST: "code_assist.j2", _PM.CREATIVE_WRITING: "creative_writing.j2", _PM.DATA_ANALYSIS: "data_analysis.j2",
}
_custom_module_prompts: dict[str, str] = {}

def register_custom_module(name: str, prompt_text: str) -> None:
    _custom_module_prompts[name] = prompt_text; logger.info("Registered custom prompt module: %s", name)

def get_module_prompt(module: PromptModule) -> str:
    tpl = _MODULE_TEMPLATES.get(module); return render(tpl) if tpl else ""

def get_custom_module_prompt(name: str) -> str: return _custom_module_prompts.get(name, "")


class PromptBuilder:
    def __init__(self, config: Optional[PromptConfig] = None) -> None:
        self.config = config or PromptConfig()
        self._enabled_modules: list[PromptModule] = [_PM.BASE, _PM.REASONING, _PM.TOOL_USAGE]
        self._custom_modules: list[str] = []; self._extra_system_instructions: list[str] = []

    def enable_module(self, module: PromptModule) -> "PromptBuilder":
        if module not in self._enabled_modules: self._enabled_modules.append(module)
        return self

    def disable_module(self, module: PromptModule) -> "PromptBuilder":
        if module in self._enabled_modules: self._enabled_modules.remove(module)
        return self

    def enable_custom_module(self, name: str) -> "PromptBuilder":
        if name not in self._custom_modules: self._custom_modules.append(name)
        return self

    def add_system_instruction(self, instruction: str) -> "PromptBuilder":
        self._extra_system_instructions.append(instruction); return self

    def build_system_prompt(self) -> str:
        parts: list[str] = [render("system_base.j2", today_date=datetime.now().strftime("%Y-%m-%d"), user_timezone=self.config.user_timezone)]
        for module in self._enabled_modules:
            text = get_module_prompt(module)
            if text: parts.append(text)
        for name in self._custom_modules:
            text = get_custom_module_prompt(name)
            if text: parts.append(text)
        for inst in self._extra_system_instructions:
            parts.append(f"\n\n# 附加指令\n\n{inst}")
        return "".join(parts)

    def build_memory_block(self, memories: Optional[list[RetrievedMemory]] = None) -> Optional[str]:
        return format_memory_block(memories, self.config.max_memory_items) if memories else None

    def build_compaction_block(self, summary: Optional[str] = None) -> Optional[str]:
        return render("compaction_context.j2", compaction_summary=summary) if summary else None

    def build_tool_context(self, tools: Optional[list[ToolDefinition]] = None) -> Optional[str]:
        return format_tool_context(tools) if tools else None

    def build_conversation_history(self, history: Optional[list[dict[str, str]]] = None) -> list[PromptMessage]:
        if not history: return []
        recent = history[-(self.config.keep_recent_rounds * 2):]
        return [PromptMessage(role=MessageRole(m["role"]), content=m["content"])
                for m in recent if m.get("role") in ("user", "assistant") and m.get("content")]

    def build_conversation_history_block(self, history: Optional[list[dict[str, str]]] = None) -> Optional[str]:
        return pair_conversation_turns(history) if history else None

    def _append_sys(self, lst: list, content: Optional[str], **kwargs) -> None:
        if not content: return
        from langchain_core.messages import SystemMessage
        lst.append(SystemMessage(content=content, **kwargs))

    def build_messages(self, user_input: str, conversation_history: Optional[list[dict[str, str]]] = None,
                       retrieved_memories: Optional[list[RetrievedMemory]] = None,
                       compaction_summary: Optional[str] = None,
                       available_tools: Optional[list[ToolDefinition]] = None) -> list[dict[str, str]]:
        msgs: list[PromptMessage] = [PromptMessage(role=MessageRole.SYSTEM, content=self.build_system_prompt())]
        for text, name in [(self.build_compaction_block(compaction_summary), "compaction"),
                           (self.build_memory_block(retrieved_memories), "memory"),
                           (self.build_tool_context(available_tools), "tools")]:
            if text: msgs.append(PromptMessage(role=MessageRole.SYSTEM, content=text, name=name))
        msgs.extend(self.build_conversation_history(conversation_history))
        msgs.append(PromptMessage(role=MessageRole.USER, content=user_input))
        return [m.to_dict() for m in msgs]

    def build_preamble(self, retrieved_memories: Optional[list[RetrievedMemory]] = None,
                       compaction_summary: Optional[str] = None,
                       available_tools: Optional[list[ToolDefinition]] = None,
                       conversation_history: Optional[list[dict[str, str]]] = None) -> list:
        from langchain_core.messages import SystemMessage
        preamble: list[SystemMessage] = [SystemMessage(content=self.build_system_prompt())]
        self._append_sys(preamble, self.build_compaction_block(compaction_summary))
        self._append_sys(preamble, self.build_memory_block(retrieved_memories))
        self._append_sys(preamble, self.build_tool_context(available_tools))
        self._append_sys(preamble, self.build_conversation_history_block(conversation_history), name="conversation_history")
        return preamble

    def build_preamble_with_breakdown(self, user_input: str,
                                      retrieved_memories: Optional[list[RetrievedMemory]] = None,
                                      compaction_summary: Optional[str] = None,
                                      available_tools: Optional[list[ToolDefinition]] = None,
                                      conversation_history: Optional[list[dict[str, str]]] = None) -> tuple[list, "TokenBreakdown"]:
        from langchain_core.messages import SystemMessage
        from apps.common.tokenizer import count_tokens
        bd = TokenBreakdown(); preamble: list[SystemMessage] = []
        sys_text = self.build_system_prompt()
        bd.system_prompt = count_tokens(sys_text); preamble.append(SystemMessage(content=sys_text))
        for text, attr in [(self.build_compaction_block(compaction_summary), "compaction_summary"),
                           (self.build_memory_block(retrieved_memories), "retrieved_memories"),
                           (self.build_tool_context(available_tools), "tool_definitions")]:
            if text:
                setattr(bd, attr, count_tokens(text)); preamble.append(SystemMessage(content=text))
        hist_text = self.build_conversation_history_block(conversation_history)
        if hist_text:
            bd.history_messages = count_tokens(hist_text)
            preamble.append(SystemMessage(content=hist_text, name="conversation_history"))
        bd.user_input = count_tokens(user_input)
        return preamble, bd


COMPACTION_PROMPT_TEMPLATE = render("compaction_task.j2", conversation_text="{conversation_text}")
DAILY_SUMMARY_PROMPT_TEMPLATE = render("daily_summary.j2", conversation_text="{conversation_text}", date="{date}")
MONTHLY_SUMMARY_PROMPT_TEMPLATE = render("monthly_summary.j2", daily_summaries="{daily_summaries}", year_month="{year_month}")
CRONMEM_PROMPT_TEMPLATE = render("cronmem_extract.j2", existing_memories="{existing_memories}", conversation_text="{conversation_text}")
BASE_SYSTEM_PROMPT = render("system_base.j2")
BEHAVIOR_GUIDELINES = render("behavior.j2")
REASONING_GUIDELINES = render("reasoning.j2")
TOOL_USAGE_GUIDELINES = render("tool_usage.j2")
MEMORY_CONTEXT_HEADER = render("memory_context.j2")
MEMORY_CONTEXT_EMPTY = render("memory_empty.j2")
TOOL_CONTEXT_HEADER = render("tool_context.j2")
COMPACTION_CONTEXT_HEADER = render("compaction_context.j2")
