import asyncio
from typing import Optional

# 兼容层：map_llm_exception 已迁移到 apps.common.exceptions
from apps.common.exceptions import map_llm_exception  # noqa: F401

_active_generations: dict[str, asyncio.Event] = {}


def register_generation(request_id: str) -> asyncio.Event:
    stop_event = asyncio.Event()
    _active_generations[request_id] = stop_event
    return stop_event


def unregister_generation(request_id: str) -> None:
    _active_generations.pop(request_id, None)


def get_stop_event(request_id: str) -> Optional[asyncio.Event]:
    return _active_generations.get(request_id)


def signal_stop(request_id: str) -> bool:
    stop_event = _active_generations.get(request_id)
    if stop_event:
        stop_event.set()
        return True
    return False
