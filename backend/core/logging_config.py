"""LinChat 统一日志配置 — batch-04 可观测性基础设施。"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from apps.common import trace_id_var


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get() or "-"
        return True


class JSONFormatter(logging.Formatter):
    _RESERVED = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "trace_id",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": dt.datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "-"),
            "msg": record.getMessage(),
            "module": record.module,
            "lineno": record.lineno,
        }
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def build_logging_dict(debug: bool, log_level: str = "INFO") -> dict[str, Any]:
    _flt = ["trace_id"]
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"trace_id": {"()": "core.logging_config.TraceIdFilter"}},
        "formatters": {
            "json": {"()": "core.logging_config.JSONFormatter"},
            "verbose": {"format": "{levelname} {asctime} [{trace_id}] {module} {message}", "style": "{"},
            "simple": {"format": "{levelname} {asctime} [{trace_id}] {message}", "style": "{"},
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "json", "filters": _flt},
        },
        "root": {"handlers": ["console"], "level": log_level},
        "loggers": {
            "django": {"handlers": ["console"], "level": log_level, "propagate": False, "filters": _flt},
            "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False, "filters": _flt},
            "uvicorn": {"handlers": ["console"], "level": log_level, "propagate": False, "filters": _flt},
            "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False, "filters": _flt},
            "uvicorn.error": {"handlers": ["console"], "level": log_level, "propagate": False, "filters": _flt},
            "apps": {"handlers": ["console"], "level": "DEBUG" if debug else log_level, "propagate": False, "filters": _flt},
            "apps.context.monitoring": {"handlers": ["console"], "level": "DEBUG", "propagate": False, "filters": _flt},
        },
    }
