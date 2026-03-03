import logging
from enum import Enum
from typing import Any, Optional

from apps.context.types import TokenBreakdown

logger = logging.getLogger("apps.context.monitoring")
_WARNING_THRESHOLD = 0.70
_CRITICAL_THRESHOLD = 0.90


class AlertLevel(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class ContextMonitor:
    @staticmethod
    def evaluate(breakdown: TokenBreakdown, max_tokens: int) -> tuple[AlertLevel, float]:
        ratio = breakdown.usage_ratio(max_tokens)
        pct = round(ratio * 100, 1)
        if ratio >= _CRITICAL_THRESHOLD: level = AlertLevel.CRITICAL
        elif ratio >= _WARNING_THRESHOLD: level = AlertLevel.WARNING
        else: level = AlertLevel.NORMAL
        return level, pct

    @staticmethod
    def build_monitor_data(breakdown: TokenBreakdown, max_tokens: int, model_name: str,
                           input_tokens: int = 0, output_tokens: int = 0,
                           memory_results: Optional[list] = None,
                           tool_processes: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        alert, pct = ContextMonitor.evaluate(breakdown, max_tokens)
        memory_types: list[dict[str, Any]] = []; memory_records: list[dict[str, Any]] = []; memory_count = 0
        if memory_results:
            from apps.common.tokenizer import count_tokens
            tag_tokens: dict[str, int] = {}
            for r in memory_results:
                mem = r["memory"]; memory_count += 1
                tag = "未分类"
                if mem.tags and isinstance(mem.tags, list) and len(mem.tags) > 0: tag = mem.tags[0]
                tokens = count_tokens(mem.content) if mem.content else 0
                tag_tokens[tag] = tag_tokens.get(tag, 0) + tokens
                if len(memory_records) < 4:
                    memory_records.append({
                        "id": mem.id, "content": mem.content[:100] if mem.content else "",
                        "tag": tag, "updated_at": mem.updated_at.isoformat() if mem.updated_at else "",
                        "token_count": tokens})
            memory_types = [{"tag": tag, "tokens": tokens}
                            for tag, tokens in sorted(tag_tokens.items(), key=lambda x: x[1], reverse=True)]
        data = {
            "type": "context_status", "model_name": model_name,
            "total_tokens": input_tokens + output_tokens, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "max_context_tokens": max_tokens,
            "pct": pct, "alert": alert.value, "breakdown": breakdown.to_dict(),
            "memory_types": memory_types, "memory_count": memory_count,
            "memory_records": memory_records, "tool_processes": tool_processes or []}
        ContextMonitor._log(alert, pct, breakdown, max_tokens)
        return data

    @staticmethod
    def _log(alert: AlertLevel, pct: float, breakdown: TokenBreakdown, max_tokens: int) -> None:
        log_data = {"max_tokens": max_tokens, "pct": pct, "alert": alert.value, "breakdown": breakdown.to_dict()}
        if alert == AlertLevel.CRITICAL: logger.error("Context monitor: %s", log_data)
        elif alert == AlertLevel.WARNING: logger.warning("Context monitor: %s", log_data)
        else: logger.debug("Context monitor: %s", log_data)
