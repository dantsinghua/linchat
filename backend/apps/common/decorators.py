"""
通用装饰器

提供异步兼容的装饰器，用于 ASGI 原生异步视图
"""
import asyncio
from functools import wraps


def async_csrf_exempt(view_func):
    """
    异步兼容的 CSRF 豁免装饰器

    自动检测视图函数类型，返回对应的同步/异步 wrapper
    """
    if asyncio.iscoroutinefunction(view_func):
        # 异步视图函数 - 使用异步 wrapper
        @wraps(view_func)
        async def async_wrapper(*args, **kwargs):
            return await view_func(*args, **kwargs)

        async_wrapper.csrf_exempt = True
        return async_wrapper
    else:
        # 同步视图函数 - 使用同步 wrapper
        @wraps(view_func)
        def sync_wrapper(*args, **kwargs):
            return view_func(*args, **kwargs)

        sync_wrapper.csrf_exempt = True
        return sync_wrapper
