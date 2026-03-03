import asyncio
import json
import logging

logger = logging.getLogger(__name__)


async def monitor_cancel_signal(user_id: int, request_id: str, stop_event: asyncio.Event) -> None:
    from core.redis import get_redis, get_user_events_channel

    try:
        client = await get_redis()
        pubsub = client.pubsub()
        channel = get_user_events_channel(user_id)
        await pubsub.subscribe(channel)
        try:
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True), timeout=1.0,
                    )
                    if message and message["type"] == "message":
                        data_str = message["data"]
                        if isinstance(data_str, bytes):
                            data_str = data_str.decode("utf-8")
                        for line in data_str.split("\n"):
                            if line.startswith("data: "):
                                try:
                                    event = json.loads(line[6:])
                                    if event.get("type") == "inference_cancel":
                                        rid = event.get("request_id")
                                        if not rid or rid == request_id:
                                            logger.info("收到推理取消信号 (Pub/Sub): user_id=%d", user_id)
                                            stop_event.set()
                                            return
                                except json.JSONDecodeError:
                                    pass
                except asyncio.TimeoutError:
                    continue
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
    except Exception as e:
        logger.warning("Pub/Sub 订阅失败，降级为轮询: user_id=%d, error=%s", user_id, e)
        await poll_cancel_signal(user_id, request_id, stop_event)


async def poll_cancel_signal(user_id: int, request_id: str, stop_event: asyncio.Event) -> None:
    from core.redis import get_redis

    try:
        client = await get_redis()
        while not stop_event.is_set():
            if await client.get(f"user:{user_id}:inference_task") is None:
                logger.info("收到推理取消信号 (轮询): user_id=%d", user_id)
                stop_event.set()
                return
            await asyncio.sleep(1.0)
    except Exception as e:
        logger.error("取消信号轮询失败: user_id=%d, error=%s", user_id, e)
