import logging
from typing import Optional

from django.conf import settings
from django.utils import timezone

from apps.chat.services.types import InferenceTask
from apps.common.event_service import EventService, EventType
from core.redis import get_redis

logger = logging.getLogger(__name__)


def _task_key(user_id: int) -> str:
    return f"user:{user_id}:inference_task"


class InferenceService:
    @staticmethod
    async def get_active_task(user_id: int) -> Optional[InferenceTask]:
        try:
            client = await get_redis()
            data = await client.get(_task_key(user_id))
            return InferenceTask.from_json(data) if data else None
        except Exception as e:
            logger.error("获取推理任务失败: user_id=%d, error=%s", user_id, e)
            return None

    @staticmethod
    async def register_task(user_id: int, request_id: str, model: str,
                            media_types: Optional[list[str]] = None) -> bool:
        try:
            client = await get_redis()
            task = InferenceTask(
                request_id=request_id, model=model,
                started_at=timezone.now(), media_types=media_types or [],
            )
            ttl = getattr(settings, "INFERENCE_TASK_TTL", 300)
            success = await client.set(_task_key(user_id), task.to_json(), nx=True, ex=ttl)
            if success:
                logger.info("注册推理任务: user_id=%d, request_id=%s", user_id, request_id)
            return bool(success)
        except Exception as e:
            logger.error("注册推理任务失败: user_id=%d, error=%s", user_id, e)
            return False

    @staticmethod
    async def complete_task(user_id: int, request_id: str) -> bool:
        try:
            client = await get_redis()
            key = _task_key(user_id)
            data = await client.get(key)
            if data:
                task = InferenceTask.from_json(data)
                if task.request_id == request_id:
                    await client.delete(key)
                    logger.info("完成推理任务: user_id=%d, request_id=%s", user_id, request_id)
                    return True
            return False
        except Exception as e:
            logger.error("完成推理任务失败: user_id=%d, error=%s", user_id, e)
            return False

    @staticmethod
    async def cancel_task(user_id: int, request_id: Optional[str] = None) -> tuple[bool, Optional[str]]:
        try:
            client = await get_redis()
            key = _task_key(user_id)
            data = await client.get(key)
            if not data:
                return False, None
            task = InferenceTask.from_json(data)
            if request_id and task.request_id != request_id:
                return False, None
            await client.delete(key)
            from apps.chat.services.generation import signal_stop
            signal_stop(task.request_id)
            await EventService.publish_event(
                user_id=user_id,
                event_type=EventType.INFERENCE_CANCEL.value,
                data={"type": EventType.INFERENCE_CANCEL.value,
                      "request_id": task.request_id, "reason": "user_requested"},
            )
            logger.info("取消推理任务: user_id=%d, request_id=%s", user_id, task.request_id)
            return True, task.request_id
        except Exception as e:
            logger.error("取消推理任务失败: user_id=%d, error=%s", user_id, e)
            return False, None

    @staticmethod
    async def refresh_task_ttl(user_id: int) -> bool:
        try:
            client = await get_redis()
            ttl = getattr(settings, "INFERENCE_TASK_TTL", 300)
            return await client.expire(_task_key(user_id), ttl)
        except Exception as e:
            logger.error("刷新任务TTL失败: user_id=%d, error=%s", user_id, e)
            return False


inference_service = InferenceService()
