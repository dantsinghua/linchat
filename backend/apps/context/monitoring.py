"""上下文窗口监控服务

提供 TokenBreakdown 告警评估、MonitorData 组装和结构化日志输出。
"""

import logging
from enum import Enum
from typing import Any, Optional

from apps.context.types import TokenBreakdown

logger = logging.getLogger("apps.context.monitoring")

# 告警阈值
_WARNING_THRESHOLD = 0.70
_CRITICAL_THRESHOLD = 0.90


class AlertLevel(str, Enum):
    """上下文告警级别"""

    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class ContextMonitor:
    """上下文监控服务

    提供告警评估、MonitorData 组装和结构化日志输出。
    """

    @staticmethod
    def evaluate(
        breakdown: TokenBreakdown, max_tokens: int,
    ) -> tuple[AlertLevel, float]:
        """评估上下文使用率和告警级别

        Args:
            breakdown: Token 分部计数
            max_tokens: 模型最大上下文窗口

        Returns:
            (告警级别, 使用百分比 0.0-100.0)
        """
        ratio = breakdown.usage_ratio(max_tokens)
        pct = round(ratio * 100, 1)

        if ratio >= _CRITICAL_THRESHOLD:
            level = AlertLevel.CRITICAL
        elif ratio >= _WARNING_THRESHOLD:
            level = AlertLevel.WARNING
        else:
            level = AlertLevel.NORMAL

        return level, pct

    @staticmethod
    def build_monitor_data(
        breakdown: TokenBreakdown,
        max_tokens: int,
        model_name: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        memory_results: Optional[list] = None,
        tool_processes: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """组装完整 MonitorData payload

        Args:
            breakdown: Token 分部计数
            max_tokens: 模型最大上下文窗口
            model_name: 当前模型名称
            input_tokens: 累计输入 token 数
            output_tokens: 累计输出 token 数
            memory_results: 记忆召回结果列表
            tool_processes: 工具调用记录列表
        """
        alert, pct = ContextMonitor.evaluate(breakdown, max_tokens)

        # 组装 memory_types（按 tags[0] 语义标签分组）
        memory_types: list[dict[str, Any]] = []
        memory_records: list[dict[str, Any]] = []
        memory_count = 0

        if memory_results:
            from apps.common.tokenizer import count_tokens

            tag_tokens: dict[str, int] = {}
            for r in memory_results:
                mem = r["memory"]
                memory_count += 1
                tag = "未分类"
                if mem.tags and isinstance(mem.tags, list) and len(mem.tags) > 0:
                    tag = mem.tags[0]
                tokens = count_tokens(mem.content) if mem.content else 0
                tag_tokens[tag] = tag_tokens.get(tag, 0) + tokens

                if len(memory_records) < 4:
                    memory_records.append({
                        "id": mem.id,
                        "content": mem.content[:100] if mem.content else "",
                        "tag": tag,
                        "updated_at": mem.updated_at.isoformat() if mem.updated_at else "",
                        "token_count": tokens,
                    })

            memory_types = [
                {"tag": tag, "tokens": tokens}
                for tag, tokens in sorted(tag_tokens.items(), key=lambda x: x[1], reverse=True)
            ]

        data = {
            "type": "context_status",
            "model_name": model_name,
            "total_tokens": input_tokens + output_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "max_context_tokens": max_tokens,
            "pct": pct,
            "alert": alert.value,
            "breakdown": breakdown.to_dict(),
            "memory_types": memory_types,
            "memory_count": memory_count,
            "memory_records": memory_records,
            "tool_processes": tool_processes or [],
        }

        # 结构化日志
        ContextMonitor._log(alert, pct, breakdown, max_tokens)

        return data

    @staticmethod
    def _log(
        alert: AlertLevel, pct: float,
        breakdown: TokenBreakdown, max_tokens: int,
    ) -> None:
        """输出结构化监控日志"""
        log_data = {
            "max_tokens": max_tokens,
            "pct": pct,
            "alert": alert.value,
            "breakdown": breakdown.to_dict(),
        }

        if alert == AlertLevel.CRITICAL:
            logger.error("Context monitor: %s", log_data)
        elif alert == AlertLevel.WARNING:
            logger.warning("Context monitor: %s", log_data)
        else:
            logger.debug("Context monitor: %s", log_data)
