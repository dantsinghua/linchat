"""
推理任务管理服务

参考:
- specs/008-multimodal-minicpm/data-model.md#2.3 InferenceTask
- specs/008-multimodal-minicpm/research.md#3 推理任务状态管理
- specs/008-multimodal-minicpm/research.md#4 并发控制方案
"""

import logging
import time
from typing import Optional

import httpx
from django.conf import settings
from django.utils import timezone

from apps.common.event_service import EventService, EventType
from apps.common.gateway_utils import build_gateway_headers, record_gateway_span
from apps.chat.services.types import InferenceTask
from core.redis import get_redis

logger = logging.getLogger(__name__)


def _get_inference_task_key(user_id: int) -> str:
    """获取推理任务 Redis 键"""
    return f"user:{user_id}:inference_task"


class InferenceService:
    """推理任务管理服务

    负责推理任务的注册、查询、取消和并发控制。
    """

    @staticmethod
    async def get_active_task(user_id: int) -> Optional[InferenceTask]:
        """获取用户当前进行中的推理任务

        Args:
            user_id: 用户 ID

        Returns:
            进行中的任务，无则返回 None
        """
        try:
            client = await get_redis()
            key = _get_inference_task_key(user_id)
            task_data = await client.get(key)
            if task_data:
                return InferenceTask.from_json(task_data)
            return None
        except Exception as e:
            logger.error(f"获取推理任务失败: user_id={user_id}, error={e}")
            return None

    @staticmethod
    async def register_task(
        user_id: int,
        request_id: str,
        model: str,
        media_types: Optional[list[str]] = None,
    ) -> bool:
        """注册新推理任务（原子性，防止并发冲突）

        Args:
            user_id: 用户 ID
            request_id: 请求 ID
            model: 模型名称
            media_types: 媒体类型列表

        Returns:
            是否注册成功（False 表示已有进行中任务）
        """
        try:
            client = await get_redis()
            key = _get_inference_task_key(user_id)
            task = InferenceTask(
                request_id=request_id,
                model=model,
                started_at=timezone.now(),
                media_types=media_types or [],
            )
            # 使用 SETNX 保证原子性
            ttl = getattr(settings, "INFERENCE_TASK_TTL", 300)
            success = await client.set(key, task.to_json(), nx=True, ex=ttl)
            if success:
                logger.info(f"注册推理任务: user_id={user_id}, request_id={request_id}")
                return True
            else:
                logger.warning(f"推理任务已存在: user_id={user_id}")
                return False
        except Exception as e:
            logger.error(f"注册推理任务失败: user_id={user_id}, error={e}")
            return False

    @staticmethod
    async def complete_task(user_id: int, request_id: str) -> bool:
        """完成推理任务（清理 Redis）

        Args:
            user_id: 用户 ID
            request_id: 请求 ID

        Returns:
            是否成功清理
        """
        try:
            client = await get_redis()
            key = _get_inference_task_key(user_id)
            # 先检查是否是当前任务
            task_data = await client.get(key)
            if task_data:
                task = InferenceTask.from_json(task_data)
                if task.request_id == request_id:
                    await client.delete(key)
                    logger.info(f"完成推理任务: user_id={user_id}, request_id={request_id}")
                    return True
            return False
        except Exception as e:
            logger.error(f"完成推理任务失败: user_id={user_id}, error={e}")
            return False

    @staticmethod
    async def cancel_task(user_id: int, request_id: Optional[str] = None) -> tuple[bool, Optional[str]]:
        """取消推理任务

        Args:
            user_id: 用户 ID
            request_id: 要取消的请求 ID（可选，不提供则取消当前任务）

        Returns:
            (是否成功, 被取消的 request_id)
        """
        try:
            client = await get_redis()
            key = _get_inference_task_key(user_id)
            task_data = await client.get(key)

            if not task_data:
                logger.info(f"无进行中的推理任务: user_id={user_id}")
                return False, None

            task = InferenceTask.from_json(task_data)

            # 如果指定了 request_id，检查是否匹配
            if request_id and task.request_id != request_id:
                logger.warning(
                    f"请求ID不匹配: user_id={user_id}, "
                    f"expected={request_id}, actual={task.request_id}"
                )
                return False, None

            # T032 状态清理时序（严格按顺序执行）:
            # 1. 删除 Redis 推理任务键（确保 AgentService 收到中断信号时
            #    InferenceTask 已不存在，新请求可立即创建新任务）
            await client.delete(key)

            # 2. 设置进程内停止信号（即时生效）
            from apps.chat.services.generation import signal_stop

            signal_stop(task.request_id)

            # 3. 发布取消事件到 Redis Pub/Sub（跨进程通知）
            await EventService.publish_event(
                user_id=user_id,
                event_type=EventType.INFERENCE_CANCEL.value,
                data={
                    "type": EventType.INFERENCE_CANCEL.value,
                    "request_id": task.request_id,
                    "reason": "user_requested",
                },
            )

            # 4. 调用网关取消接口（可选，超时降级）
            await InferenceService._call_gateway_cancel(task.request_id)

            logger.info(f"取消推理任务: user_id={user_id}, request_id={task.request_id}")
            return True, task.request_id

        except Exception as e:
            logger.error(f"取消推理任务失败: user_id={user_id}, error={e}")
            return False, None

    @staticmethod
    async def _call_gateway_cancel(request_id: str) -> bool:
        """调用网关取消接口

        Args:
            request_id: 请求 ID

        Returns:
            是否成功
        """
        gateway_url = getattr(settings, "LLM_GATEWAY_URL", "")
        if not gateway_url:
            logger.debug("未配置 LLM_GATEWAY_URL，跳过网关取消调用")
            return True

        cancel_url = f"{gateway_url}/v1/chat/cancel"
        headers = build_gateway_headers(request_id=request_id)
        start_time = time.monotonic()

        try:
            cancel_timeout = getattr(settings, "LLM_GATEWAY_CANCEL_TIMEOUT", 5)
            async with httpx.AsyncClient(timeout=cancel_timeout) as client:
                response = await client.post(
                    cancel_url,
                    json={"request_id": request_id},
                    headers=headers,
                )
                duration = time.monotonic() - start_time
                record_gateway_span(
                    request_type="inference_cancel",
                    model="",
                    duration=duration,
                    status_code=response.status_code,
                    request_id=request_id,
                )
                if response.status_code == 200:
                    logger.info(f"网关取消成功: request_id={request_id}")
                    return True
                else:
                    logger.warning(
                        f"网关取消失败: request_id={request_id}, "
                        f"status={response.status_code}"
                    )
                    return False
        except httpx.TimeoutException:
            duration = time.monotonic() - start_time
            logger.warning(f"网关取消超时: request_id={request_id}")
            record_gateway_span(
                request_type="inference_cancel",
                model="",
                duration=duration,
                status_code=504,
                request_id=request_id,
                error="timeout",
            )
            return False
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.warning(f"网关取消异常: request_id={request_id}, error={e}")
            record_gateway_span(
                request_type="inference_cancel",
                model="",
                duration=duration,
                status_code=503,
                request_id=request_id,
                error=str(e),
            )
            return False

    @staticmethod
    async def refresh_task_ttl(user_id: int) -> bool:
        """刷新任务 TTL（防止长时间推理超时）

        Args:
            user_id: 用户 ID

        Returns:
            是否成功
        """
        try:
            client = await get_redis()
            key = _get_inference_task_key(user_id)
            ttl = getattr(settings, "INFERENCE_TASK_TTL", 300)
            return await client.expire(key, ttl)
        except Exception as e:
            logger.error(f"刷新任务TTL失败: user_id={user_id}, error={e}")
            return False


# 单例实例
inference_service = InferenceService()
