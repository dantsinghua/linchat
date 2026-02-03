"""测试辅助函数"""

import asyncio


def run_async(coro):
    """运行异步函数"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def collect_stream(async_gen):
    """收集异步生成器的所有结果"""
    results = []
    async for item in async_gen:
        results.append(item)
    return results
