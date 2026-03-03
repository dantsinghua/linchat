from __future__ import annotations

from typing import Optional

from apps.context.loader import render
from apps.context.types import RetrievedMemory, ToolDefinition

_MEMORY_TYPE_LABELS: dict[str, str] = {
    "memory": "记忆", "compaction": "对话摘要", "daily-summary": "每日摘要", "monthly-summary": "月度摘要",
}


def format_memory_block(memories: list[RetrievedMemory], max_items: int) -> Optional[str]:
    sorted_mems = sorted(memories, key=lambda m: m.relevance_score, reverse=True)[:max_items]
    if not sorted_mems: return None
    entries: list[str] = []
    for i, mem in enumerate(sorted_mems, 1):
        label = _MEMORY_TYPE_LABELS.get(mem.memory_type, "记忆")
        time = f" ({mem.created_at})" if mem.created_at else ""
        entries.append(f"{i}. [{label}]{time} {mem.content}")
    return render("memory_context.j2", memory_entries="\n".join(entries))


def format_tool_context(tools: list[ToolDefinition]) -> Optional[str]:
    active = [t for t in tools if t.enabled]
    if not active: return None
    lines: list[str] = []
    for tool in active:
        params_desc = ""
        if tool.parameters:
            items = []
            for pname, pinfo in tool.parameters.items():
                ptype = pinfo.get("type", "any"); pdesc = pinfo.get("description", "")
                marker = " (必填)" if pinfo.get("required", False) else ""
                items.append(f"    - `{pname}` ({ptype}){marker}: {pdesc}")
            params_desc = "\n" + "\n".join(items)
        lines.append(f"## {tool.name}\n{tool.description}{params_desc}")
    return render("tool_context.j2", tool_definitions="\n\n".join(lines))


def pair_conversation_turns(history: list[dict[str, str]]) -> Optional[str]:
    turns: list[dict[str, str]] = []
    i = 0
    while i < len(history):
        msg = history[i]
        if msg.get("role") == "user" and msg.get("content"):
            user_text = msg["content"]; assistant_text = ""
            if i + 1 < len(history):
                nxt = history[i + 1]
                if nxt.get("role") == "assistant" and nxt.get("content"):
                    assistant_text = nxt["content"]; i += 1
            turns.append({"user": user_text, "assistant": assistant_text})
        i += 1
    if not turns: return None
    return render("conversation_history.j2", turns=turns)
