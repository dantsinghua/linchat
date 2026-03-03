import asyncio
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def has_active_users() -> bool:
    import redis as redis_lib
    r = redis_lib.from_url(settings.REDIS_URL)
    _, keys = r.scan(cursor=0, match="auth:token:*", count=10)
    return len(keys) > 0


def warmup_language_model() -> None:
    import httpx
    from apps.models.services import model_service
    try:
        config = model_service.get_active_model("tool")
        if not config: return
        httpx.Client(timeout=60.0).post(
            f"{config['url']}/chat/completions",
            headers={"Authorization": f"Bearer {config['api_key'] or 'not-needed'}"},
            json={"model": config["name"], "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
        ).raise_for_status()
        logger.info("Language model warmup completed")
    except Exception as e:
        logger.warning("Language model warmup failed: %s", e)


def collect_content(user_id: int, primary_type: str, start, end, msg_limit: int) -> tuple[str, str]:
    from apps.memory.models import UserMemory
    records = UserMemory.objects.filter(user_id=user_id, type=primary_type, created_at__gte=start, created_at__lt=end)
    content = "\n\n".join(r.content for r in records)
    source = primary_type
    if not content:
        source = "message"
        try:
            from apps.chat.models import Message
            msgs = Message.objects.filter(user_id=user_id, created_time__gte=start, created_time__lt=end).order_by("created_time")[:msg_limit]
            content = "\n".join(f"{m.role}: {m.content}" for m in msgs)
        except Exception as e:
            logger.warning("Failed to get messages for user %d: %s", user_id, e)
    try:
        from apps.chat.models import Message
        from apps.users.models import SysUser
        unknown_user = SysUser.objects.filter(username="unknown").first()
        if unknown_user:
            bg_msgs = Message.objects.filter(
                user_id=unknown_user.user_id, is_voice=True, created_time__gte=start, created_time__lt=end,
            ).order_by("created_time")[:50]
            bg = "\n".join(f"背景对话（未识别说话人）: {m.content}" for m in bg_msgs if m.content)
            if bg: content = (content + "\n\n" + bg) if content else bg
    except Exception as e:
        logger.warning("Failed to append unknown user voice messages for user %d: %s", user_id, e)
    return content, source


def run_summary(primary_type: str, start, end, summary_type: str, summary_name: str, msg_limit: int) -> None:
    from apps.memory.models import UserMemory
    from apps.memory.services import MemoryService
    active = set(
        UserMemory.objects.filter(type=primary_type, created_at__gte=start, created_at__lt=end)
        .values_list("user_id", flat=True).distinct()
    )
    try:
        from apps.chat.models import Message
        active.update(Message.objects.filter(created_time__gte=start, created_time__lt=end).values_list("user_id", flat=True).distinct())
    except Exception as e:
        logger.warning("Failed to query message users: %s", e)
    if not active:
        logger.info("No active users for %s", summary_name); return
    logger.info("%s: %d active users", summary_type, len(active))
    loop = asyncio.new_event_loop()
    try:
        for uid in active:
            try:
                content, source = collect_content(uid, primary_type, start, end, msg_limit)
                if not content:
                    logger.debug("%s: no content for user %d (source=%s, range=%s~%s)", summary_type, uid, source, start, end)
                    continue
                logger.info("%s: processing user %d (source=%s, content_len=%d)", summary_type, uid, source, len(content))
                result = loop.run_until_complete(MemoryService.summarize_and_store(
                    user_id=uid, content=content, summary_type=summary_type, summary_name=summary_name))
                if result:
                    logger.info("%s: stored for user %d (memory_id=%d, content_len=%d)", summary_type, uid, result.id, len(result.content))
                else:
                    logger.warning("%s: summarize returned None for user %d (content_len=%d)", summary_type, uid, len(content))
            except Exception as e:
                logger.warning("%s failed for user %d: %s: %s", summary_type, uid, type(e).__name__, e)
    finally:
        loop.close()
