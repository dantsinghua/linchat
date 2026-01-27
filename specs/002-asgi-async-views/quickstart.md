# Quickstart: ASGI 原生异步视图改造

## 快速验证

### 1. 启动后端服务

```bash
# 激活虚拟环境
source /home/dantsinghua/work/linchat/linchat/bin/activate

# 使用 uvicorn ASGI 模式启动（必须）
cd /home/dantsinghua/work/linchat/backend
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload
```

### 2. 测试 SSE 端点

```bash
# 测试聊天 SSE（需要先登录获取 token）
curl -N -X POST http://localhost:8002/linchat/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -H "Cookie: auth_token=<your_token>" \
  -d '{"content": "你好"}'

# 预期响应（流式）
# data: {"type": "content", "content": "你", "message_id": 123}
# data: {"type": "content", "content": "好", "message_id": 123}
# data: {"type": "done", "content": "", "message_id": 123}
```

### 3. 验证资源释放

```bash
# 监控 Redis 订阅数
redis-cli INFO clients | grep connected_clients

# 在另一个终端发起 SSE 请求，然后 Ctrl+C 中断
# 等待 5 秒后再次检查，订阅数应恢复
```

## 改造后的代码示例

### 异步视图 (chat/views.py)

```python
async def chat(request: HttpRequest) -> StreamingHttpResponse:
    """
    发送消息并获取流式响应

    POST /api/v1/chat/
    """
    # 验证请求
    body = json.loads(request.body.decode("utf-8"))
    serializer = ChatRequestSerializer(data=body)
    if not serializer.is_valid():
        return JsonResponse({"code": "VALIDATION_ERROR", ...}, status=400)

    user_id = request.user_id
    content = serializer.validated_data["content"]

    async def event_generator():
        try:
            async for chunk in ChatService.send_message(
                user_id=user_id, content=content
            ):
                data = {
                    "type": chunk.type,
                    "content": chunk.content,
                }
                if chunk.message_id:
                    data["message_id"] = chunk.message_id
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("Chat error")
            error_data = {"type": "error", "content": str(e)}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

    response = StreamingHttpResponse(
        event_generator(), content_type="text/event-stream"
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
```

### 事件推送视图 (common/views.py)

```python
class EventsView(View):
    async def get(self, request: HttpRequest) -> StreamingHttpResponse:
        """SSE 事件订阅"""
        user_id = getattr(request, "user_id", None)
        if not user_id:
            return JsonResponse({"code": "UNAUTHORIZED", ...}, status=401)

        async def event_generator():
            async for event in EventService.subscribe_user_events(user_id):
                yield event

        response = StreamingHttpResponse(
            event_generator(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
```

## 测试命令

```bash
# 运行异步测试
cd /home/dantsinghua/work/linchat/backend
pytest tests/chat/test_views.py -v --asyncio-mode=auto

# 检查是否还有 new_event_loop 调用
grep -r "new_event_loop" backend/apps/
# 预期: 无输出（已全部移除）
```

## 常见问题

### Q: 启动后报错 "You cannot use AsyncToSync in the same thread"

**A**: 确保使用 uvicorn 启动，不要使用 `python manage.py runserver`

### Q: SSE 响应没有流式效果，一次性返回

**A**: 检查 Nginx 配置，确保设置了 `proxy_buffering off;`

### Q: 资源没有正确释放

**A**: 检查异步生成器的 `finally` 块是否包含清理逻辑
