# Research: ASGI 原生异步视图改造

**Feature**: 002-asgi-async-views
**Date**: 2026-01-26

## 1. Django ASGI 原生异步视图

### Decision
使用 Django 4.1+ 的原生异步视图支持，直接定义 `async def` 视图函数。

### Rationale
- Django 4.1+ 原生支持异步视图，无需第三方库
- ASGI 服务器（uvicorn）可以直接调用异步视图
- 异步生成器可以在 `StreamingHttpResponse` 中直接使用
- `finally` 块在异步上下文中正确执行，解决资源泄漏问题

### Alternatives Considered
1. **继续使用同步视图 + 线程**: 当前方案，存在资源泄漏问题，已否决
2. **使用 Django Channels**: 过于重量级，SSE 场景不需要 WebSocket 支持
3. **使用 aiohttp 独立服务**: 增加架构复杂性，需要维护两套服务

### Key Implementation Details
```python
# Django 异步视图 + 异步生成器
async def chat(request: HttpRequest) -> StreamingHttpResponse:
    async def event_generator():
        try:
            async for chunk in service.process():
                yield f"data: {chunk}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error'})}\n\n"
        # finally 块在 ASGI 环境中正确执行

    return StreamingHttpResponse(
        event_generator(),
        content_type="text/event-stream"
    )
```

## 2. StreamingHttpResponse 与异步生成器

### Decision
直接将异步生成器传递给 `StreamingHttpResponse`，Django 4.2+ 原生支持。

### Rationale
- Django 4.2 的 `StreamingHttpResponse` 支持接收异步迭代器
- 无需手动转换为同步生成器
- 代码更简洁，减少出错可能

### Key Notes
- 必须运行在 ASGI 模式下（uvicorn）
- WSGI 模式（runserver）不支持异步流式响应

## 3. 资源清理模式

### Decision
使用 `try/finally` 模式在异步生成器中清理资源。

### Rationale
- ASGI 环境中，异步生成器的 `finally` 块会正确执行
- 即使客户端断开连接，`finally` 也会被调用
- 无需依赖外部信号或回调

### Implementation Pattern
```python
async def subscribe_user_events(user_id: int):
    client = await get_redis()
    pubsub = client.pubsub()

    try:
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            yield message
    except asyncio.CancelledError:
        logger.info("Connection cancelled")
        raise
    finally:
        # 这里会正确执行，即使客户端断开
        await pubsub.unsubscribe(channel)
        await pubsub.close()
```

## 4. 中间件兼容性

### Decision
保持现有 Django 中间件，它们兼容异步视图。

### Rationale
- Django 官方中间件（如 `AuthenticationMiddleware`）支持异步
- 自定义中间件（如 `TokenAuthMiddleware`）需要检查是否使用 `async_to_sync`
- Django 会自动在同步和异步中间件之间切换

### Verification Needed
- 检查 `apps/common/middleware.py` 中的自定义中间件
- 确保不阻塞异步视图

## 5. URL 路由配置

### Decision
直接使用 `path()` 绑定异步视图函数，无需特殊配置。

### Rationale
- Django 的路由系统自动检测视图是同步还是异步
- 使用 `@csrf_exempt` 等装饰器仍然有效
- 类视图需要在方法级别定义 `async def`

### Example
```python
# urls.py
from apps.chat.views import chat  # async def chat(...)

urlpatterns = [
    path('api/v1/chat/', chat, name='chat'),  # 直接绑定
]
```

## 6. 测试策略

### Decision
使用 `pytest-asyncio` 和 Django 的 `async_client` 进行异步视图测试。

### Rationale
- `pytest-asyncio` 提供异步测试支持
- Django 4.1+ 的测试客户端支持异步请求
- 可以模拟 SSE 流式响应

### Testing Pattern
```python
import pytest
from django.test import AsyncClient

@pytest.mark.asyncio
async def test_chat_sse():
    client = AsyncClient()
    response = await client.post('/api/v1/chat/', data={'content': 'test'})

    # 读取 SSE 流
    chunks = []
    async for chunk in response.streaming_content:
        chunks.append(chunk.decode())

    assert 'data:' in chunks[0]
```

## 7. 当前代码分析

### 问题代码位置

| 文件 | 函数 | 问题 |
|------|------|------|
| `apps/chat/views.py:90` | `chat()` | `asyncio.new_event_loop()` |
| `apps/chat/views.py:294` | `resume_generation()` | `asyncio.new_event_loop()` |
| `apps/chat/views.py:375` | `reconnect_stream()` | `asyncio.new_event_loop()` |
| `apps/common/event_service.py:185` | `create_sse_response()` | `asyncio.new_event_loop()` |

### 改造优先级

1. **P1**: `chat()` - 核心聊天功能
2. **P1**: `resume_generation()` - 继续生成
3. **P2**: `reconnect_stream()` - 重连
4. **P2**: `EventsView.get()` + `create_sse_response()` - 事件推送

## References

- [Django Async Views](https://docs.djangoproject.com/en/4.2/topics/async/)
- [Django StreamingHttpResponse](https://docs.djangoproject.com/en/4.2/ref/request-response/#streaminghttpresponse-objects)
- [uvicorn ASGI Server](https://www.uvicorn.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
