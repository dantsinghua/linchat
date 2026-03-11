import asyncio
from typing import Optional


async def cancel_task(task: Optional[asyncio.Task]) -> None:
    if not task or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


def cancel_task_sync(task: Optional[asyncio.Task]) -> None:
    if task and not task.done():
        task.cancel()
