import json as _json
import logging
from typing import Optional

logger = logging.getLogger(__name__)
_FILTERED_CONTENT = "内容已被安全策略过滤"


def extract_usage(output) -> tuple[int, int]:
    if hasattr(output, "usage_metadata") and output.usage_metadata:
        usage = output.usage_metadata
        return usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    if hasattr(output, "response_metadata") and output.response_metadata:
        usage = output.response_metadata.get("token_usage", {})
        return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    return 0, 0


def _match_gateway_error(code, s, info=None):
    if "E3001" in (code or s):
        return "E3001", "请求的模型不存在", None
    if "E3002" in (code or s):
        retry_after = info.get("details", {}).get("retry_after") if info else None
        return "E3002", "多模态服务暂时不可用，请稍后重试", retry_after
    return None


def extract_gateway_error(e) -> Optional[tuple[str, str, Optional[int]]]:
    error_body = None
    try:
        if hasattr(e, "response") and hasattr(e.response, "text"):
            error_body = _json.loads(e.response.text)
        elif hasattr(e, "body") and isinstance(e.body, dict):
            error_body = e.body
    except Exception:
        pass
    if not error_body:
        return _match_gateway_error("", str(e))
    info = error_body.get("error", {})
    return _match_gateway_error(info.get("code", ""), "", info)


def extract_content_control(e) -> Optional[str]:
    s = str(e)
    if "content_control" in s:
        try:
            for part in s.split("data:"):
                part = part.strip()
                if part.startswith("{") and "content_control" in part:
                    return _json.loads(part.split("\n")[0]).get("replacement", _FILTERED_CONTENT)
        except Exception:
            pass
        return _FILTERED_CONTENT
    if hasattr(e, "body") and isinstance(e.body, dict) and e.body.get("type") == "clear_previous":
        return e.body.get("replacement", _FILTERED_CONTENT)
    try:
        if hasattr(e, "response") and hasattr(e.response, "text"):
            text = e.response.text
            if "content_control" in text or "clear_previous" in text:
                return _json.loads(text).get("replacement", _FILTERED_CONTENT)
    except Exception:
        pass
    return None
