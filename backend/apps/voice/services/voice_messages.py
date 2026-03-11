from typing import Any


def error_msg(code: str, message: str, recoverable: bool = True) -> dict:
    return {"type": "error", "data": {"code": code, "message": message, "recoverable": recoverable}}


def response_event(event_type: str, response_id: str, segment_id: str, **extra: Any) -> dict:
    return {"type": event_type, "data": {"response_id": response_id, "segment_id": segment_id, **extra}}


def delta_msg(content: str, response_id: str) -> dict:
    return {"type": "response.delta", "data": {"delta": {"content": content}, "response_id": response_id}}


def build_agent_error(chunk: Any) -> dict:
    err: dict[str, Any] = {"code": "AGENT_ERROR", "message": chunk.content or "Agent 推理出错", "recoverable": True}
    if chunk.data:
        if chunk.data.get("gateway_error"):
            err["code"] = chunk.data["gateway_error"]
        if chunk.data.get("content_control"):
            err["code"] = "CONTENT_FILTER"
            err["message"] = chunk.data.get("replacement", err["message"])
        if chunk.data.get("retry_after"):
            err["retry_after"] = chunk.data["retry_after"]
            err["recoverable"] = False
    return err
