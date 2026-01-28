# Implementation Plan: ASGI 原生异步视图改造

**Branch**: `002-asgi-async-views` | **Date**: 2026-01-26 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-asgi-async-views/spec.md`

## Summary

将现有 SSE 视图从手动创建临时事件循环（`asyncio.new_event_loop()`）改造为 ASGI 原生异步视图（`async def`），解决资源泄漏问题，简化代码，提升性能。

**核心技术方案**：
- 视图函数从 `def` 改为 `async def`
- 使用异步生成器 (`async def event_generator()`) 直接 yield SSE 事件
- 在 `finally` 块中正确清理异步资源（Redis pubsub 等）
- 服务器使用 uvicorn ASGI 模式运行

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Django 4.2+, DRF 3.14+, uvicorn 0.30+, redis-py (async)
**Storage**: PostgreSQL (主存储), Redis (缓存/Pubsub)
**Testing**: pytest, pytest-asyncio, pytest-django
**Target Platform**: Linux server (Ubuntu)
**Project Type**: Web application (前后端分离)
**Performance Goals**: 1000 并发 SSE 连接, TTFB < 2秒
**Constraints**: 资源释放延迟 < 5秒，内存稳定（波动 < 10%）
**Scale/Scope**: 改造 4 个视图函数 + 1 个服务函数

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 条款 | 要求 | 状态 | 说明 |
|------|------|------|------|
| 1.1 关注点分离 | 视图层仅处理 HTTP 请求响应 | ✅ PASS | 视图改造不涉及业务逻辑变更 |
| 1.2 SSE 视图实现规范 | 必须使用 ASGI 原生异步视图 | ✅ PASS | 本改造正是为了满足此要求 |
| 1.2 SSE 视图实现规范 | 禁止手动创建临时事件循环 | ✅ PASS | 改造后将消除所有 `new_event_loop()` |
| 2.1 Python 后端规范 | 类型注解、Google 风格文档 | ✅ PASS | 改造代码将遵循 |
| 3.1 测试覆盖率 | 视图层 80%，服务层 95% | ⏳ PENDING | 需更新测试用例 |
| 8.2 ASGI 服务器配置 | 必须使用 uvicorn | ✅ PASS | 已配置 |

**Gate 结论**: 无违规，可以继续。

## Project Structure

### Documentation (this feature)

```text
specs/002-asgi-async-views/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (N/A - 无数据模型变更)
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output (N/A - API 保持兼容)
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── chat/
│   │   └── views.py           # 改造: chat(), resume_generation(), reconnect_stream()
│   └── common/
│       ├── views.py           # 改造: EventsView.get()
│       └── event_service.py   # 重构: 删除 create_sse_response(), 改造 subscribe_user_events()
├── core/
│   └── asgi.py                # 验证配置正确
└── tests/
    ├── chat/
    │   └── test_views.py      # 更新异步测试
    └── common/
        └── test_event_service.py  # 更新异步测试
```

**Structure Decision**: 遵循现有的 Django apps 结构，仅修改视图层和服务层代码，不新增文件。

## Complexity Tracking

> 无宪法违规需要解释。本改造为代码简化，不增加复杂性。

## Implementation Phases

### Phase 1: 聊天视图改造 (P1 - 核心功能)

**目标**: 改造 `chat/views.py` 中的 3 个 SSE 视图

| 视图 | 当前实现 | 改造后 |
|------|----------|--------|
| `chat()` | 同步 + 线程 + 临时事件循环 | `async def chat()` + 异步生成器 |
| `resume_generation()` | 同步 + 线程 + 临时事件循环 | `async def resume_generation()` + 异步生成器 |
| `reconnect_stream()` | 同步 + 线程 + 临时事件循环 | `async def reconnect_stream()` + 异步生成器 |

**改造模式**:
```python
# 改造前
def chat(request):
    def event_generator():
        loop = asyncio.new_event_loop()
        # ... 复杂的线程+队列逻辑
    return StreamingHttpResponse(event_generator(), ...)

# 改造后
async def chat(request):
    async def event_generator():
        try:
            async for chunk in ChatService.send_message(...):
                yield f"data: {json.dumps(...)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', ...})}\n\n"

    return StreamingHttpResponse(event_generator(), ...)
```

### Phase 2: 事件推送视图改造 (P2 - 辅助功能)

**目标**: 改造 `common/views.py` 和 `common/event_service.py`

| 组件 | 当前实现 | 改造后 |
|------|----------|--------|
| `EventsView.get()` | 调用 `create_sse_response()` | `async def get()` + 直接调用异步生成器 |
| `create_sse_response()` | 同步包装 + 临时事件循环 | **删除** |
| `subscribe_user_events()` | 异步生成器 (已正确) | 保持，确保 `finally` 正确执行 |

### Phase 3: 测试更新

**目标**: 更新测试用例以支持异步视图测试

- 使用 `pytest-asyncio` 和 `@pytest.mark.asyncio`
- 使用 `async_client` 替代同步 `client`
- 验证资源释放（Redis 订阅数量）

### Phase 4: 验证与上线

**目标**: 验证改造效果

- 手动测试 SSE 连接断开时的资源释放
- 并发测试（1000 连接）
- 监控 Redis 订阅数量、内存使用

## Dependencies

| 依赖 | 版本 | 用途 | 状态 |
|------|------|------|------|
| Django | 4.2+ | ASGI 异步视图支持 | ✅ 已有 |
| uvicorn | 0.30+ | ASGI 服务器 | ✅ 已有 |
| pytest-asyncio | 0.21+ | 异步测试 | ✅ 已有 |

## Risks & Mitigations

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 异步中间件兼容性 | Django 中间件可能不兼容异步 | 使用 Django 官方中间件，避免同步中间件 |
| URL 路由配置 | 异步视图需要特殊路由配置 | 使用 `path()` 直接绑定异步函数 |
| 现有测试失败 | 同步测试无法测试异步视图 | 更新为 `pytest-asyncio` 异步测试 |

## Success Metrics

| 指标 | 目标 | 验证方法 |
|------|------|----------|
| 代码消除 | 0 个 `asyncio.new_event_loop()` 调用 | grep 搜索 |
| 资源释放 | SSE 断开后 5 秒内 Redis 订阅数恢复 | Redis INFO 监控 |
| 并发连接 | 支持 1000 并发 SSE | 压测工具 |
| 功能回归 | 所有现有测试通过 | pytest 执行 |
