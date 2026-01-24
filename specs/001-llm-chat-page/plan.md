# Implementation Plan: 大模型聊天页面

**Branch**: `001-llm-chat-page` | **Date**: 2026-01-25 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-llm-chat-page/spec.md`

---

## Summary

构建企业级大模型聊天页面，包含：
- **用户认证**：国密算法加密、图形验证码、双重Token过期机制
- **流式聊天**：LangGraph ReAct Agent + Redis Checkpoint + SSE流式响应
- **数据持久化**：PostgreSQL消息存储 + Redis缓存 + Langfuse监控
- **前端渲染**：Next.js + Markdown/Mermaid实时渲染

---

## Technical Context

**Language/Version**: Python 3.11+ (后端) / TypeScript 5.0+ (前端)
**Primary Dependencies**:
- 后端: Django REST Framework 4.2+, LangGraph, langgraph-checkpoint-redis, gmssl (国密), Langfuse
- 前端: Next.js 14+, React 18+, Zustand, react-markdown, mermaid

**Storage**:
- PostgreSQL: 主存储（sys_user, message, langgraph_execution）
- Redis: Token缓存、验证码、LangGraph Checkpoint
- Elasticsearch: 预留（暂不使用）

**Testing**: pytest + pytest-django (后端) / Jest + Playwright (前端)
**Target Platform**: Linux服务器 + 现代浏览器（Chrome/Firefox/Edge/Safari）
**Project Type**: Web应用（前后端分离Monorepo）
**Performance Goals**:
- API响应 p95 < 200ms
- 大模型首令牌 < 2秒（符合宪法5.1要求）
- 流式字符延迟 < 100ms
- 并发用户 ≥ 100

**Constraints**:
- Token存储在httpOnly Cookie（禁止localStorage）
- 密码使用国密SM3哈希
- 所有用户操作需刷新Token有效期
- 数据隔离：用户只能访问自己的消息

**Scale/Scope**: 初期 < 100并发用户，单机部署

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 宪法条款 | 检查项 | 状态 | 说明 |
|----------|--------|------|------|
| 1.1 关注点分离 | 视图层禁止业务逻辑 | ✅ PASS | 服务层封装所有业务逻辑（见behavior-model.md） |
| 1.1 关注点分离 | 数据仓库层封装数据访问 | ✅ PASS | Repository模式隔离ORM/Redis操作 |
| 1.2 接口设计 | RESTful API规范 | ✅ PASS | /api/v1/ 路径，统一响应格式 |
| 1.2 接口设计 | WebSocket流式响应 | ⚠️ DEVIATION | 使用SSE替代WebSocket（见偏离说明） |
| 1.3 数据一致性 | PostgreSQL为主存储 | ✅ PASS | 消息持久化到PostgreSQL |
| 1.3 数据一致性 | 写操作原子性 | ✅ PASS | 事务保护，失败回滚（见behavior-model.md） |
| 2.1 Python规范 | 类型注解 | ✅ PASS | 所有公共函数添加类型注解 |
| 2.2 前端规范 | TypeScript严格模式 | ✅ PASS | Next.js + strict mode |
| 3.1 测试覆盖 | 服务层 95% | ⏳ PENDING | 实施阶段验证 |
| 4.1 认证授权 | Token存储httpOnly Cookie | ✅ PASS | 见rule-model.md R_TOKEN_003 |
| 4.2 数据保护 | 密码国密SM3哈希 | ✅ PASS | 符合宪法国密算法要求 |
| 4.3 大模型异常 | 统一异常处理 | ✅ PASS | 见behavior-model.md B_CHAT_002 |
| 5.1 响应时间 | API p95 < 200ms | ⏳ PENDING | 实施阶段验证 |

**偏离说明**:
- **流式响应协议**: 宪法规定WebSocket端点，本特性使用SSE (Server-Sent Events)。理由：SSE为单向流式、HTTP原生、配置更简单，符合AI响应场景需求（见research.md#4）

---

## Project Structure

### Documentation (this feature)

```text
specs/001-llm-chat-page/
├── spec.md              # 功能规范（已完成）
├── plan.md              # 本文件
├── research.md          # Phase 0 技术研究
├── data-model.md        # 数据模型（已完成）
├── process-model.md     # 流程模型（已完成）
├── behavior-model.md    # 行为模型（已完成）
├── rule-model.md        # 规则模型（已完成）
├── quickstart.md        # 快速启动指南
├── contracts/           # API契约
│   └── openapi.yaml     # OpenAPI规范
└── tasks.md             # Phase 2 任务清单（由/speckit.tasks生成）
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── chat/                     # 聊天核心模块
│   │   ├── models.py             # Message, LangGraphExecution
│   │   ├── serializers.py        # DRF序列化器
│   │   ├── views.py              # API视图（SSE流式响应）
│   │   ├── services.py           # 业务逻辑（Agent执行、消息处理）
│   │   ├── repositories.py       # 数据访问层
│   │   └── agent.py              # LangGraph Agent定义
│   ├── users/                    # 用户认证模块
│   │   ├── models.py             # SysUser
│   │   ├── serializers.py        # 登录请求/响应
│   │   ├── views.py              # 登录/验证码API
│   │   ├── services.py           # 认证逻辑（Token、验证码）
│   │   ├── repositories.py       # 用户数据访问
│   │   └── crypto.py             # 国密算法封装
│   └── common/
│       ├── exceptions.py         # 自定义异常类
│       ├── middleware.py         # Token鉴权中间件
│       └── responses.py          # 统一响应格式
├── core/
│   ├── settings.py               # Django配置
│   └── redis.py                  # Redis连接管理
└── tests/
    ├── chat/                     # 聊天模块测试
    └── users/                    # 用户模块测试

frontend/
├── src/
│   ├── app/
│   │   ├── login/page.tsx        # 登录页面
│   │   ├── chat/page.tsx         # 聊天页面
│   │   └── 401/page.tsx          # 401错误页面
│   ├── components/
│   │   ├── ui/                   # 基础UI组件
│   │   ├── chat/                 # 聊天相关组件
│   │   │   ├── MessageList.tsx   # 消息列表
│   │   │   ├── MessageInput.tsx  # 输入框
│   │   │   └── MarkdownRenderer.tsx # Markdown渲染
│   │   └── auth/                 # 认证相关组件
│   │       ├── LoginForm.tsx     # 登录表单
│   │       └── CaptchaImage.tsx  # 验证码图片
│   ├── hooks/
│   │   ├── useChatStream.ts      # SSE流式聊天Hook
│   │   └── useAuth.ts            # 认证Hook
│   ├── services/
│   │   ├── api.ts                # Axios配置（401拦截）
│   │   ├── authService.ts        # 认证API
│   │   └── chatService.ts        # 聊天API
│   ├── stores/
│   │   └── chatStore.ts          # Zustand状态管理
│   └── types/
│       └── index.ts              # TypeScript类型定义
└── tests/
    ├── components/               # 组件测试
    └── e2e/                      # Playwright端到端测试
```

**Structure Decision**: 采用Web应用结构（Option 2），前后端分离Monorepo，符合宪法1.1分层架构要求。

---

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Redis Checkpoint而非PostgreSQL | LangGraph官方推荐，高性能、支持TTL | PostgreSQL Checkpoint配置复杂，性能较差 |

---

## Phase 0: Research Summary

> 详见 [research.md](./research.md)

| 研究主题 | 决策 | 理由 |
|----------|------|------|
| 验证码方案 | captcha库 | 稳定、易集成、支持自定义样式 |
| 国密算法库 | gmssl | Python官方国密库，支持SM2/SM3/SM4 |
| LangGraph Checkpoint | langgraph-checkpoint-redis | 官方推荐，24小时TTL + refresh_on_read |
| 流式响应 | SSE (Server-Sent Events) | 单向流式，比WebSocket更简单 |
| Markdown渲染 | react-markdown + rehype-highlight | 支持GFM、代码高亮 |
| Mermaid渲染 | mermaid + useEffect | 流式完成后渲染图表 |

---

## Phase 1: Design Artifacts

> 详细设计已在以下文档中完成：

| 文档 | 内容 | 状态 |
|------|------|------|
| [data-model.md](./data-model.md) | PostgreSQL表结构、Redis缓存设计 | ✅ 已完成 |
| [process-model.md](./process-model.md) | 业务流程时序图、代码示例 | ✅ 已完成 |
| [behavior-model.md](./behavior-model.md) | 6个原子行为的完整实现 | ✅ 已完成 |
| [rule-model.md](./rule-model.md) | 12条业务规则、配置参数 | ✅ 已完成 |
| [contracts/openapi.yaml](./contracts/openapi.yaml) | API契约 | ✅ 已完成 |
| [quickstart.md](./quickstart.md) | 快速启动指南 | ✅ 已完成 |

---

## Next Steps

1. 运行 `/speckit.tasks` 生成 `tasks.md` 任务清单
2. 按任务清单实施开发
3. 实施阶段验证测试覆盖率和性能指标
