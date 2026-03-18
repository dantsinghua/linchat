import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="users.expire_guests")
def expire_guests() -> None:
    """扫描过期访客并设 status=0"""
    from apps.users.models import SysUser
    from django.utils import timezone

    count = SysUser.objects.filter(
        member_type="guest",
        guest_expires_at__lte=timezone.now(),
        status=1,
    ).update(status=0)

    if count:
        logger.info("Expired %d guest accounts", count)
