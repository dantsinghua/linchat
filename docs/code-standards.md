# LinChat 编码规范

> **版本**: 1.0.0 | **基于**: 项目宪法 v1.10.0 | **最后更新**: 2026-03-31
>
> 本文档是 LinChat 项目编码规范的完整参考手册。
> 宪法原文见 [.specify/memory/constitution.md](../.specify/memory/constitution.md)，
> 代码示例见 [constitution-examples.md](constitution-examples.md)。

---

## 目录

1. [概述](#1-概述)
2. [Python/Django 规范](#2-pythondjango-规范)
3. [TypeScript/Next.js 规范](#3-typescriptnextjs-规范)
4. [架构规范](#4-架构规范)
5. [安全规范](#5-安全规范)
6. [测试规范](#6-测试规范)
7. [Git 提交规范](#7-git-提交规范)
8. [禁止事项](#8-禁止事项)
9. [参考文档](#9-参考文档)

---

## 1. 概述

LinChat 是家庭场景 AI 聊天平台，采用 Django REST Framework + Next.js 前后端分离架构。

### 1.1 技术栈

| 层级 | 技术选型 | 版本 |
|------|----------|------|
| 后端框架 | Django REST Framework | Django 4.2+ / DRF 3.14+ |
| ASGI 服务器 | uvicorn（**强制**） | 0.30+ |
| 前端框架 | Next.js / React / TypeScript | 14+ / 18+ / 5.0+ |
| 主数据库 | PostgreSQL（唯一可信来源） | 15+ |
| 缓存层 | Redis（会话/缓存/实时通信） | - |
| 任务队列 | Celery | 5.3+ |
| AI Agent | LangGraph + LangChain + Langfuse | - |
| 状态管理 | Zustand（前端） | - |
| 运行时 | Python 3.11+ / Node.js 18+ | - |

### 1.2 核心原则

- **简单设计优先**：选择最简单直接的方案，禁止静默 fallback 或隐式降级
- **显式失败**：业务失败必须向用户展示具体原因，禁止静默吞没错误
- **关注点分离**：严格三层架构 — 视图层 / 服务层 / 数据层
- **数据一致性**：PostgreSQL 为唯一可信来源，写操作原子性，失败回滚
- **单用户单会话**：所有隔离按 `user_id` 粒度，不存在会话粒度概念

---

## 2. Python/Django 规范

### 2.1 代码风格

| 规范项 | 要求 | 工具 |
|--------|------|------|
| 格式化 | PEP 8 | Black（行宽 88 字符） |
| 导入排序 | 标准库 → 第三方 → 本地 | isort |
| 类型检查 | 所有公共函数必须添加类型注解 | mypy |
| 文档字符串 | Google 风格，公共接口必须编写 | - |

### 2.2 命名规范

| 类型 | 风格 | 示例 |
|------|------|------|
| 类名 | PascalCase | `ChatService`, `MessageRepository` |
| 函数/方法 | snake_case | `create_message()`, `get_user_by_id()` |
| 常量 | UPPER_SNAKE | `MAX_CONTEXT_ROUNDS`, `DEFAULT_MODEL_NAME` |
| 私有成员 | 单下划线前缀 | `_init_langfuse()`, `_build_prompt()` |

### 2.3 类型注解与文档字符串

```python
def create_message(
    self,
    user_id: int,
    content: str,
    role: str,
    attachments: list[dict] | None = None,
) -> Message:
    """创建消息。

    Args:
        user_id: 用户 ID。
        content: 消息内容。
        role: 消息角色（user/assistant/system）。
        attachments: 可选的附件列表。

    Returns:
        创建的 Message 实例。

    Raises:
        ValidationError: 参数校验失败。
    """
    ...
```

### 2.4 导入排序

```python
# 标准库
import logging
from datetime import datetime

# 第三方库
from django.db import transaction
from rest_framework import status

# 本地模块
from apps.chat.models import Message
from apps.common.exceptions import ValidationError
```

### 2.5 Django 模型规范

- 状态字段使用 `SmallIntegerField` + 类内常量（如 `STATUS_ACTIVE = 1`），**禁止赋值字符串**
- 必须定义 `Meta`（ordering、indexes、verbose_name）
- 必须实现 `__str__()` 方法
- Message 模型**没有** `conversation_id` 字段，只有 `user_id`

```python
class Message(models.Model):
    """聊天消息模型。"""
    STATUS_ACTIVE = 1
    STATUS_FAILED = 0

    user = models.ForeignKey("users.SysUser", on_delete=models.CASCADE)
    content = models.TextField(verbose_name="消息内容")
    role = models.CharField(max_length=20)
    status = models.SmallIntegerField(default=STATUS_ACTIVE)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]
```

### 2.6 DRF 视图规范

视图层**仅**负责 HTTP 请求解析和响应格式化，**禁止**包含业务逻辑。

```python
class MessageViewSet(viewsets.ViewSet):
    """仅处理 HTTP，业务逻辑委托给 ChatService。"""

    def create(self, request: Request) -> Response:
        serializer = MessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = self.service.create_message(
            user_id=request.user.id, **serializer.validated_data
        )
        return Response(
            {"code": "SUCCESS", "data": MessageSerializer(message).data},
            status=status.HTTP_201_CREATED,
        )
```

### 2.7 SSE 异步视图规范

**必须**使用 ASGI 原生异步视图，**禁止**手动创建临时事件循环。

```python
async def chat_stream_view(request):
    """SSE 流式聊天 — 必须运行在 uvicorn ASGI 模式。"""

    async def event_generator():
        try:
            async for chunk in service.stream_response(user_id=request.user.id):
                yield f"data: {chunk}\n\n"
            yield 'data: {"type": "done"}\n\n'
        except Exception as e:
            yield f'data: {{"type": "error", "message": "{str(e)}"}}\n\n'

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    return response
```

SSE 消息类型：`content`（增量文本）、`done`（完成）、`error`（错误）、`interrupted`（用户中断）。

### 2.8 API 响应格式

```json
{ "code": "SUCCESS", "data": { ... }, "message": "操作成功" }
{ "code": "<错误码>", "data": null, "message": "<错误信息>" }
```

基础路径：`/api/v1/`，使用 URL 路径版本化。

---

## 3. TypeScript/Next.js 规范

### 3.1 代码风格

| 规范项 | 要求 | 配置 |
|--------|------|------|
| Linting | ESLint | `next/core-web-vitals` + `next/typescript` |
| 格式化 | Prettier | 单引号、行宽 100、尾随逗号 |
| 类型模式 | 严格模式 | 所有 strict 选项启用 |

### 3.2 命名规范

| 类型 | 风格 | 示例 |
|------|------|------|
| 组件 | PascalCase | `ChatPanel`, `MessageList` |
| Hooks | use + PascalCase | `useChat`, `useMessageStore` |
| 工具函数 | camelCase | `formatDate`, `encryptApiKey` |
| 常量 | UPPER_SNAKE | `MAX_MESSAGE_LENGTH` |
| 类型/接口 | PascalCase | `MessageProps`, `ChatState` |
| 文件名 | 组件 PascalCase，其余 kebab-case | `ChatPanel.tsx`, `use-chat.ts` |

### 3.3 组件规范

**必须使用函数式组件 + Hooks**，Props **必须**通过 `interface` 定义（禁止 `type`）。

```tsx
interface MessageBubbleProps {
  content: string;
  role: 'user' | 'assistant';
  timestamp: string;
  isStreaming?: boolean;
}

export function MessageBubble({
  content,
  role,
  timestamp,
  isStreaming = false,
}: MessageBubbleProps) {
  return (
    <div className={`message-bubble message-bubble--${role}`}>
      <p>{content}</p>
      {isStreaming && <span className="typing-indicator" />}
    </div>
  );
}
```

### 3.4 状态管理

| 状态类型 | 工具 | 使用场景 |
|----------|------|----------|
| 全局状态 | Zustand | 用户信息、主题设置、聊天列表 |
| 服务端状态 | React Query / SWR | API 数据获取、缓存与同步 |
| 组件局部状态 | useState / useReducer | 表单输入、UI 交互 |

```tsx
import { create } from 'zustand';

interface ChatState {
  messages: Message[];
  isStreaming: boolean;
  addMessage: (message: Message) => void;
  setStreaming: (streaming: boolean) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isStreaming: false,
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),
  setStreaming: (streaming) => set({ isStreaming: streaming }),
}));
```

### 3.5 API 调用

使用 Axios + httpOnly Cookie 自动认证（`withCredentials: true`），**禁止**手动管理 Token。

```tsx
const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL,
  withCredentials: true,
});

// SSE 流式请求使用 fetch
async function streamChat(content: string, onChunk: (text: string) => void) {
  const response = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk(decoder.decode(value));
  }
}
```

### 3.6 WebSocket

通过 Django Channels 实现，用于双向实时通信（如语音交互）。端点格式：`/ws/chat/{thread_id}/`。

---

## 4. 架构规范

### 4.1 三层架构（不可违背）

```
视图层 (views.py)           → 仅处理 HTTP 请求响应，禁止业务逻辑
服务层 (services.py|services/) → 封装所有业务逻辑 ★ 核心层
数据层 (repositories.py)    → 封装 ORM / ES / Redis 操作
```

**层间调用规则**：

- 视图层 → 服务层（可以），视图层 → 数据层（**禁止**）
- 服务层 → 数据层（可以），服务层 → 其他服务层（可以）
- 数据层 → 服务层/视图层（**禁止**，不可反向依赖）

**服务层组织**：简单模块用 `services.py` 单文件；复杂模块（3+ 服务类）拆为 `services/` 目录，通过 `__init__.py` 统一导出。

### 4.2 项目目录结构

```
backend/
├── apps/
│   ├── chat/          # 聊天核心（models/serializers/views/services/repositories/consumers/tasks）
│   ├── users/         # 用户认证
│   ├── memory/        # 记忆管理
│   ├── graph/         # LangGraph Agent
│   └── common/        # 共享组件（异常、工具函数）
├── core/              # Django 配置（settings/urls/asgi）
└── tests/             # 镜像 apps 结构

frontend/src/
├── app/               # Next.js App Router
├── components/        # React 组件（ui/chat/layout）
├── hooks/             # 自定义 Hooks
├── services/          # API 调用层
├── stores/            # Zustand 状态
├── types/             # TypeScript 类型
└── utils/             # 工具函数
```

### 4.3 数据一致性（不可违背）

| 存储 | 职责 | 规范 |
|------|------|------|
| PostgreSQL | 主存储（唯一可信来源） | ORM、禁止原生 SQL、事务保护 |
| Elasticsearch | 搜索引擎（只读副本，可选） | 通过 Celery 异步同步 |
| Redis | 缓存与实时通信 | 所有键设 TTL、可重建 |

**核心原则**：

1. 写操作使用 `transaction.atomic()` 保护，失败回滚
2. 强一致性：同步写入 PG → ES → Redis，任一失败全部回滚
3. 最终一致性：PG 先写，ES/Redis 通过 Celery 异步同步
4. 必须实现数据一致性检查与补偿任务

> 详细代码示例见 [constitution-examples.md 第 1-2 节](constitution-examples.md#1-数据一致性保障示例)

### 4.4 用户隔离模型（不可违背）

**术语定义**：

| 术语 | 定义 |
|------|------|
| 1 轮对话 | 1 条 user 消息 + 1 条 assistant 消息 |
| 保留最近 N 轮 | 保留最后 N x 2 条 user/assistant 消息 |
| 隔离粒度 | 永远按 `user_id`，无"会话粒度" |

**单用户单会话约束**：

- 一个用户永远对应一个会话，Message 只有 `user_id`，没有 `conversation_id`
- 系统支持多用户档案（家庭成员/访客），但任何时刻仅一人使用
- **禁止**多用户并发控制（分布式锁、冲突弹窗、请求排队）
- **禁止**推理并发检测（HTTP 409、前置状态查询接口）

### 4.5 ASGI 服务器（不可违背）

```bash
# 唯一启动方式
uvicorn core.asgi:application --host 0.0.0.0 --port 8002
# 开发时可加 --reload
uvicorn core.asgi:application --host 0.0.0.0 --port 8002 --reload
```

**禁止** `python manage.py runserver`（WSGI 不支持原生异步 SSE）。

---

## 5. 安全规范

### 5.1 身份认证

| 机制 | 要求 |
|------|------|
| 认证方式 | Token（24h 绝对过期 + 1h 无操作过期） |
| 令牌存储 | httpOnly Cookie（**禁止** localStorage） |
| 令牌轮换 | 刷新后旧令牌立即失效 |
| 权限控制 | 对象级权限，验证资源所有权 |
| 设备 Token | IoT 设备长效 Token（SM4 加密、可撤销、明文仅注册时返回一次） |

### 5.2 加密标准

| 用途 | 算法 |
|------|------|
| 密码哈希 | 国密 SM3（不可逆） |
| API 密钥加密 | 国密 SM4（对称，可解密） |
| 传输安全 | HTTPS / TLS |

### 5.3 频率限制

| 用户类型 | 限制 |
|----------|------|
| 匿名用户 | 100 次/小时 |
| 认证用户 | 1000 次/小时 |
| LLM 调用 | 60 次/分钟 |

通过 Redis 滑动窗口算法实现。

### 5.4 输入验证与防护

- **输入验证**：DRF 序列化器
- **SQL 注入**：Django ORM 自动参数化（**禁止**原生 SQL）
- **XSS**：React 自动转义 + CSP
- **CSRF**：Django CSRF 中间件
- **密钥管理**：环境变量（**禁止**提交版本控制）

### 5.5 LLM 异常处理（不可违背）

| 异常类型 | 用户提示 | 策略 |
|----------|----------|------|
| `LLMConnectionError` | AI 服务暂时无法连接 | 重试 3 次 |
| `LLMTimeoutError` | AI 响应超时 | 重试 3 次 |
| `LLMInvalidResponseError` | AI 响应异常 | 重试 3 次 |
| `LLMRateLimitError` | 请求过于频繁 | 不重试，返回等待时间 |
| `LLMContentFilterError` | 消息包含敏感内容 | 不重试，允许修改 |
| `LLMContextLengthError` | 消息内容过长 | 不重试，提示缩短 |
| `LLMQuotaExceededError` | 服务配额用尽 | 不重试，联系管理员 |

异常层级：`AppException` → `LLMException` → 具体异常类，业务异常继承 `BusinessException`。

> 详细代码示例见 [constitution-examples.md 第 3 节](constitution-examples.md#3-大模型服务异常处理示例)

### 5.6 HTTP 异常映射

| 异常类 | HTTP 状态码 |
|--------|------------|
| `ValidationError` | 400 |
| `AuthenticationError` | 401 |
| `PermissionDeniedError` | 403 |
| `NotFoundError` | 404 |
| `RateLimitError` | 429 |
| `DataSyncError` | 500 |
| `ExternalServiceError` | 503 |

### 5.7 日志规范

| 级别 | 使用场景 |
|------|----------|
| DEBUG | 详细调试信息（默认关闭） |
| INFO | 业务流程关键节点 |
| WARNING | 可恢复的异常 |
| ERROR | 需关注的错误 |
| CRITICAL | 系统级故障 |

**必须记录**：API 请求/响应、认证事件、关键业务操作、外部服务调用、所有异常（含 traceback）。

> 运维提醒：后端启动必须 `PYTHONUNBUFFERED=1`，否则 nohup 日志缓冲导致 traceback 丢失。

---

## 6. 测试规范

### 6.1 测试分类

| 类型 | 说明 | 后端工具 | 前端工具 |
|------|------|----------|----------|
| 单元测试 | 隔离执行，mock 外部依赖 | pytest + pytest-asyncio | Jest + Testing Library |
| 集成测试 | 真实数据库，mock 外部服务 | pytest-django | MSW |
| E2E 测试 | 完整用户流程 | - | Playwright |

### 6.2 覆盖率要求

| 层级 | 要求 |
|------|------|
| **总体** | **>= 80%** |
| 关键路径（认证、核心聊天） | >= 95% |
| 服务层 | >= 95% |
| 数据模型层 | >= 90% |
| 工具函数 | >= 90% |
| 数据仓库层 | >= 85% |
| 前端 Hooks | >= 85% |
| API 视图层 | >= 80% |
| 前端组件 | >= 75% |

### 6.3 后端测试示例

```python
class TestChatService:
    """聊天服务单元测试 — 外部依赖全部 mock。"""

    @pytest.fixture
    def chat_service(self) -> ChatService:
        return ChatService()

    @pytest.mark.django_db
    def test_create_message_success(self, chat_service, user_factory) -> None:
        user = user_factory()
        message = chat_service.create_message(user_id=user.id, content="你好", role="user")
        assert message.user_id == user.id
        assert message.content == "你好"
        assert message.status == Message.STATUS_ACTIVE

    @pytest.mark.django_db
    def test_create_message_rollback_on_failure(self, chat_service, user_factory) -> None:
        user = user_factory()
        with patch.object(chat_service, "_invalidate_cache", side_effect=RedisError):
            with pytest.raises(DataSyncError):
                chat_service.create_message(user_id=user.id, content="测试", role="user")
        assert Message.objects.filter(user_id=user.id).count() == 0
```

### 6.4 前端测试示例

```tsx
describe('MessageBubble', () => {
  it('renders message content', () => {
    render(<MessageBubble content="你好" role="assistant" timestamp="2026-03-31" />);
    expect(screen.getByText('你好')).toBeInTheDocument();
  });

  it('shows typing indicator when streaming', () => {
    render(<MessageBubble content="" role="assistant" timestamp="" isStreaming />);
    expect(document.querySelector('.typing-indicator')).toBeInTheDocument();
  });
});
```

### 6.5 测试命令

```bash
# 后端（先激活虚拟环境）
source /home/dantsinghua/work/linchat/linchat/bin/activate
cd /home/dantsinghua/work/linchat/backend
pytest                    # 全部测试
pytest --cov=apps         # 带覆盖率
pytest tests/chat/ -v     # 指定模块

# 前端
cd /home/dantsinghua/work/linchat/frontend
npm test                  # 单元测试
npm run lint              # 代码检查
```

> 完整测试模式参见 [constitution-examples.md 第 4-6 节](constitution-examples.md#4-后端测试代码示例)

---

## 7. Git 提交规范

### 7.1 格式

```
<类型>(<范围>): <描述>
```

### 7.2 类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(chat): 添加流式响应支持` |
| `fix` | Bug 修复 | `fix(auth): 修复令牌刷新竞态条件` |
| `docs` | 文档变更 | `docs(api): 更新 SSE 接口文档` |
| `style` | 代码风格 | `style(backend): Black 格式化` |
| `refactor` | 重构 | `refactor(chat): 拆分服务层为目录模式` |
| `perf` | 性能优化 | `perf(query): 添加消息查询索引` |
| `test` | 测试 | `test(chat): 补充服务层单元测试` |
| `chore` | 构建/工具 | `chore(deps): 升级 langfuse 至 3.12` |

### 7.3 范围

`chat` / `auth` / `users` / `memory` / `graph` / `voice` / `frontend` / `backend` / `deps` / `ci`

### 7.4 规则

- 每个提交是独立的、可工作的变更
- 描述用中文，动词开头（添加、修复、优化、重构...）
- **禁止**提交敏感信息（.env、密钥、凭据）

---

## 8. 禁止事项

以下行为**绝对禁止**，违反的代码**禁止合并**：

| # | 禁止事项 | 原因 |
|---|----------|------|
| 1 | 在视图层编写业务逻辑 | 违反三层架构，降低可测试性 |
| 2 | 直接写原生 SQL | 必须用 ORM，防 SQL 注入 |
| 3 | Token 存储在 localStorage | 必须用 httpOnly Cookie，防 XSS |
| 4 | 提交敏感信息到版本控制 | API 密钥/密码必须通过环境变量管理 |
| 5 | 合并违反"不可违背"条款的代码 | 宪法标记的条款无例外 |
| 6 | 跳过测试直接部署 | 必须通过测试验证 |
| 7 | 忽略数据一致性检查 | 写操作必须事务保护 |
| 8 | SSE 视图中手动创建临时事件循环 | 必须用 ASGI 原生异步视图 |
| 9 | 使用 `manage.py runserver` | 必须用 uvicorn ASGI 模式 |
| 10 | 使用"会话粒度"隔离 | 所有隔离永远按 `user_id` |
| 11 | 引入 `conversation_id` / `session_id` | 单用户单会话，Message 只有 `user_id` |

---

## 9. 参考文档

### 核心文档

| 文档 | 路径 | 说明 |
|------|------|------|
| 项目宪法 | [.specify/memory/constitution.md](../.specify/memory/constitution.md) | 不可违背的原则（权威来源） |
| 代码示例 | [constitution-examples.md](constitution-examples.md) | 编码时强制参考的实现模式 |
| Gateway 集成指南 | [linchat-integration-guide.md](linchat-integration-guide.md) | LLM Gateway 对接说明 |
| 上游 API 指南 | [upstream-integration-guide.md](upstream-integration-guide.md) | Gateway 上游 API 规范 |
| 多模态 API 指南 | [multimodal-api-guide.md](multimodal-api-guide.md) | 文档解析 API 对接 |

### 性能指标

| 场景 | 指标 |
|------|------|
| 简单 GET | p95 < 100ms |
| 带 DB 查询 GET | p95 < 200ms |
| POST 创建 | p95 < 300ms |
| ES 搜索 | p95 < 500ms |
| WebSocket 连接 | < 500ms |
| LLM 首令牌 (TTFT) | < 2s |
| 多模态首字节 | < 5s（豁免） |
| 前端 FCP | < 1.5s |
| 前端打包 | < 200KB (gzip) |

### SLA 监控

| 类别 | 告警阈值 |
|------|----------|
| 整体可用性 | < 99.9% |
| API 请求成功率 | < 99% |
| 5xx 错误率 | > 1% |
| 平均响应时间 | > 500ms |
| LLM 响应成功率 | < 95% |

---

*本文档随项目宪法同步更新。如有疑问，以宪法原文为准。*
