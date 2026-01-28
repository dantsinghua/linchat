# Feature Specification: ASGI 原生异步视图改造

**Feature Branch**: `002-asgi-async-views`
**Created**: 2026-01-26
**Status**: Draft
**Input**: ASGI 原生异步视图改造方案 - 将 SSE 视图从手动创建临时事件循环改为 ASGI 原生异步视图

## 背景与问题

当前系统的 SSE（Server-Sent Events）视图存在架构问题：在同步视图函数中手动创建临时事件循环（`asyncio.new_event_loop()`），导致以下问题：

1. **资源泄漏**：当 SSE 连接断开时，事件循环被关闭，但异步资源（如 Redis pubsub）的 `finally` 块无法正确执行
2. **代码复杂性**：需要手动管理事件循环生命周期，增加维护成本
3. **性能瓶颈**：无法充分利用 ASGI 的异步非阻塞特性

## User Scenarios & Testing

### User Story 1 - 聊天流式响应 (Priority: P1)

用户在聊天界面发送消息后，系统通过 SSE 流式返回 AI 响应内容，用户可以实时看到内容逐字生成。

**Why this priority**: 这是核心聊天功能，直接影响用户体验。流式响应的稳定性决定了产品是否可用。

**Independent Test**: 可通过发送一条聊天消息并观察响应是否完整流式返回来独立测试。

**Acceptance Scenarios**:

1. **Given** 用户已登录并进入聊天界面，**When** 用户发送消息，**Then** 系统通过 SSE 流式返回 AI 响应，内容逐字显示
2. **Given** SSE 连接已建立，**When** 用户主动关闭页面或断开连接，**Then** 服务端正确释放所有资源（Redis pubsub 订阅、数据库连接等）
3. **Given** SSE 流式响应进行中，**When** 网络异常导致连接中断，**Then** 服务端在超时后自动清理资源，无资源泄漏

---

### User Story 2 - 继续生成响应 (Priority: P1)

用户在 AI 响应中断后（如网络问题），可以点击"继续生成"按钮恢复响应。

**Why this priority**: 这是聊天体验的关键补充功能，确保用户不会因网络问题丢失 AI 响应。

**Independent Test**: 可通过模拟响应中断，然后点击继续生成按钮来独立测试。

**Acceptance Scenarios**:

1. **Given** AI 响应因异常中断，**When** 用户点击"继续生成"，**Then** 系统从断点继续生成并通过 SSE 流式返回
2. **Given** 继续生成请求已发送，**When** 用户再次断开连接，**Then** 服务端正确释放资源

---

### User Story 3 - 重连流式响应 (Priority: P2)

用户在页面刷新或重新打开后，如果有未完成的 AI 响应，系统自动重连并继续接收剩余内容。

**Why this priority**: 提升用户体验，避免因页面刷新丢失正在生成的内容。

**Independent Test**: 可通过在响应过程中刷新页面，观察是否自动恢复来独立测试。

**Acceptance Scenarios**:

1. **Given** 存在未完成的 AI 响应，**When** 用户刷新页面，**Then** 系统自动重连 SSE 并继续接收剩余内容
2. **Given** 重连 SSE 流，**When** 连接正常关闭，**Then** 服务端正确释放所有资源

---

### User Story 4 - 实时事件推送 (Priority: P2)

系统通过 SSE 向用户推送实时通知（如会话过期提醒、系统公告等）。

**Why this priority**: 用户实时通知是辅助功能，但对用户体验有重要影响。

**Independent Test**: 可通过触发一个通知事件并观察用户端是否收到来独立测试。

**Acceptance Scenarios**:

1. **Given** 用户已登录，**When** 系统触发通知事件，**Then** 用户通过 SSE 实时收到通知
2. **Given** 用户正在接收事件，**When** 用户登出或关闭页面，**Then** 服务端正确关闭 SSE 连接并释放 Redis 订阅资源

---

### Edge Cases

- 当多个 SSE 连接同时存在时，每个连接的资源应独立管理和释放
- 当 Redis 服务暂时不可用时，SSE 连接应优雅降级，不影响其他功能
- 当服务器重启时，所有现有 SSE 连接应被正确关闭
- 当客户端发送非法请求时，服务端应返回适当错误而不是崩溃

## Requirements

### Functional Requirements

- **FR-001**: 系统 MUST 使用 ASGI 原生异步视图处理所有 SSE 请求
- **FR-002**: 系统 MUST 在 SSE 连接结束时（正常或异常）正确释放所有异步资源
- **FR-003**: 系统 MUST 支持聊天消息的流式响应，包括首次响应和继续生成
- **FR-004**: 系统 MUST 支持客户端重连后继续接收未完成的流式响应
- **FR-005**: 系统 MUST 支持实时事件推送（通知、系统消息等）
- **FR-006**: 系统 MUST 在异步生成器的 `finally` 块中正确执行清理逻辑
- **FR-007**: 系统 MUST 使用 uvicorn ASGI 服务器运行（禁止使用 `runserver`）
- **FR-008**: 系统 MUST 设置正确的 SSE 响应头（Content-Type、Cache-Control、X-Accel-Buffering）

### Non-Functional Requirements

- **NFR-001**: SSE 连接的资源释放延迟不超过 5 秒
- **NFR-002**: 单个服务实例支持至少 1000 个并发 SSE 连接
- **NFR-003**: 改造后的代码复杂度应低于改造前（使用 radon cc 测量平均圈复杂度，消除手动事件循环管理）

### Key Entities

- **SSE Connection**: 代表一个 SSE 长连接，包含用户身份、连接状态、关联的异步资源
- **Async Resource**: 需要在连接结束时清理的异步资源（Redis pubsub 订阅）

## Success Criteria

### Measurable Outcomes

- **SC-001**: 所有 SSE 视图改造为 `async def`，消除所有 `asyncio.new_event_loop()` 调用
- **SC-002**: SSE 连接断开后，Redis 订阅数量在 5 秒内恢复到连接前水平（无泄漏）
- **SC-003**: 在 30 分钟 1000 并发 SSE 连接压测期间，系统内存使用稳定（相对基线波动 < 10%，基线为压测开始时的内存使用量）
- **SC-004**: 聊天流式响应的首字节时间（TTFB）不超过 2 秒
- **SC-005**: 代码行数减少（消除事件循环管理相关的样板代码）
- **SC-006**: 所有现有 SSE 功能测试通过，无功能回归

## Scope

### In Scope

- `apps/chat/views.py` 中的 `chat()`、`resume_generation()`、`reconnect_stream()` 视图改造
- `apps/common/views.py` 中的 `EventsView.get()` 视图改造
- `apps/common/event_service.py` 中删除 `create_sse_response()` 函数并验证 `subscribe_user_events()` 的 finally 块资源清理
- `requirements.txt` 添加/确认 uvicorn 依赖
- 更新相关单元测试和集成测试

### Out of Scope

- WebSocket 相关功能改造（当前聚焦 SSE）
- 前端代码修改（后端 API 保持兼容）
- 性能优化（本次聚焦功能正确性）

## Assumptions

- uvicorn 已在 requirements.txt 中声明（如未声明需添加）
- Django 4.2+ 已支持 ASGI 原生异步视图
- 现有的 `ChatService.send_message()` 等服务层方法已经是异步的
- 部署环境使用 uvicorn 而非 gunicorn 的 WSGI 模式

## Dependencies

- Django 4.2+ ASGI 支持
- uvicorn 0.30+ ASGI 服务器
- pytest-asyncio 用于异步测试
