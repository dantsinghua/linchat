# Claude 开发指南

> 本文件为 Claude AI 代理在本项目中的开发行为提供明确指导。
> 所有代码生成、修改和审查必须遵循本指南。

---

## 项目概述

**项目名称**: 大模型聊天平台 (LinChat)
**项目类型**: 企业级多租户 AI 聊天应用
**开发模式**: 规范驱动开发 (Speckit)

---

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 后端框架 | Django REST Framework 4.2+ |
| 前端框架 | Next.js 14+ / React 18+ / TypeScript 5.0+ |
| 主数据库 | PostgreSQL (唯一可信来源) |
| 搜索引擎 | Elasticsearch (只读副本) |
| 缓存层 | Redis (会话/缓存/实时通信) |
| 任务队列 | Celery 5.3+ |
| AI Agent | LangGraph + Langfuse |
| 状态管理 | Zustand (前端) |

---

## 强制参考文档

在进行任何开发工作前，**必须**阅读以下文档：

### 核心治理文档

| 文档 | 路径 | 用途 |
|------|------|------|
| 项目宪法 | [.specify/memory/constitution.md](.specify/memory/constitution.md) | 不可违背的原则和约束 |
| 代码示例 | [docs/constitution-examples.md](docs/constitution-examples.md) | 编码时强制参考的示例代码 |

### 特性规范文档

| 文档类型 | 路径模式 | 用途 |
|----------|----------|------|
| 特性规范 | `specs/<feature>/spec.md` | 功能需求和验收标准 |
| 实施计划 | `specs/<feature>/plan.md` | 技术方案和实施步骤 |
| 任务清单 | `specs/<feature>/tasks.md` | 具体开发任务 |
| 质量检查 | `specs/<feature>/checklists/*.md` | 各阶段质量检查清单 |

---

## 开发工作流

### Speckit 命令参考

```bash
# 特性规范阶段
/speckit.specify    # 创建/更新特性规范
/speckit.clarify    # 澄清规范中的歧义

# 规划阶段
/speckit.plan       # 生成实施计划
/speckit.tasks      # 生成任务清单

# 验证阶段
/speckit.analyze    # 跨文档一致性分析
/speckit.checklist  # 生成质量检查清单

# 实施阶段
/speckit.implement  # 按任务清单实施

# 治理阶段
/speckit.constitution  # 更新项目宪法
```

### 开发流程

```
1. 阅读宪法 → 2. 阅读规范 → 3. 阅读计划 → 4. 执行任务 → 5. 验证合规
```

---

## 编码规范速查

### Python/Django 后端

| 规范项 | 要求 | 参考 |
|--------|------|------|
| 代码风格 | PEP 8 + Black (88字符) | 宪法 2.1 |
| 导入排序 | isort | 宪法 2.1 |
| 类型注解 | 所有公共函数必须添加 | 宪法 2.1 |
| 文档字符串 | Google 风格 | 宪法 2.1 |
| 数据一致性 | 事务保护，失败回滚 | 代码示例 1-2节 |
| 异常处理 | 自定义异常类层级 | 代码示例 3节 |
| 测试覆盖 | 服务层 95%，总体 80%+ | 代码示例 4-6节 |

### TypeScript/Next.js 前端

| 规范项 | 要求 | 参考 |
|--------|------|------|
| 代码风格 | ESLint + Prettier | 宪法 2.2 |
| 类型模式 | 严格模式 | 宪法 2.2 |
| 组件规范 | 函数式组件 + Hooks | 宪法 2.2 |
| Props 定义 | 必须使用 interface | 宪法 2.2 |
| 状态管理 | Zustand + React Query | 宪法 2.2 |

---

## 架构约束 (不可违背)

### 分层架构

```
视图层 (views.py)      → 仅处理 HTTP 请求响应，禁止业务逻辑
服务层 (services.py)   → 封装所有业务逻辑 ★核心
数据层 (repositories.py) → 封装 ORM/ES/Redis 操作
```

### 数据一致性

| 原则 | 说明 |
|------|------|
| PostgreSQL 为主 | 唯一可信数据来源 |
| 写操作原子性 | 失败必须回滚 |
| 同步机制 | ES/Redis 通过 Celery 异步同步 |
| 补偿机制 | 必须实现数据一致性检查 |

> **参考**: 代码示例文档 1-2 节

### 大模型异常处理

必须统一处理以下异常类型：

| 异常 | 策略 |
|------|------|
| LLMConnectionError | 重试3次 |
| LLMTimeoutError | 重试3次 |
| LLMRateLimitError | 不重试，返回等待时间 |
| LLMContentFilterError | 不重试，允许用户修改 |

> **参考**: 代码示例文档 3 节

---

## 安全要求 (不可违背)

| 类别 | 要求 |
|------|------|
| 令牌存储 | httpOnly Cookie (禁止 localStorage) |
| 密码哈希 | 国密SM3算法 |
| API 密钥 | 国密SM4加密存储 |
| 频率限制 | 匿名100次/时，认证1000次/时，LLM 60次/分 |

---

## 测试要求

| 测试类型 | 说明 | 工具 |
|----------|------|------|
| 单元测试 | 隔离执行，mock 外部依赖 | pytest / Jest |
| 集成测试 | 真实数据库，mock 外部服务 | pytest-django / MSW |
| 端到端 | 完整用户流程 | Playwright |

**覆盖率要求**:
- 总体 ≥ 80%
- 关键路径 ≥ 95%
- 服务层 ≥ 95%

> **参考**: 代码示例文档 4-6 节

---

## 性能指标

| 场景 | 指标 |
|------|------|
| API GET 请求 | p95 < 200ms |
| API POST 请求 | p95 < 300ms |
| 大模型首令牌 | < 2秒 |
| 前端 FCP | < 1.5秒 |
| 前端打包 | < 200KB (gzip) |

---

## 提交规范

```
<类型>(<范围>): <描述>

类型: feat / fix / docs / style / refactor / perf / test / chore
示例: feat(chat): 添加流式响应支持
```

---

## 禁止事项

1. **禁止**在视图层编写业务逻辑
2. **禁止**直接写原生 SQL (必须使用 ORM)
3. **禁止**将 Token 存储在 localStorage（必须使用 httpOnly Cookie）
4. **禁止**提交敏感信息到版本控制
5. **禁止**合并违反"不可违背"条款的代码
6. **禁止**跳过测试直接部署
7. **禁止**忽略数据一致性检查

---

## 当前特性

| 特性分支 | 规范路径 | 状态 |
|----------|----------|------|
| 001-llm-chat-page | [specs/001-llm-chat-page/spec.md](specs/001-llm-chat-page/spec.md) | 规范已完成 |

---

## 快速参考链接

- 宪法文件: [.specify/memory/constitution.md](.specify/memory/constitution.md)
- 代码示例: [docs/constitution-examples.md](docs/constitution-examples.md)
- 当前特性规范: [specs/001-llm-chat-page/spec.md](specs/001-llm-chat-page/spec.md)
- 规范质量检查: [specs/001-llm-chat-page/checklists/requirements.md](specs/001-llm-chat-page/checklists/requirements.md)

---

*本文件随项目演进持续更新，版本与宪法文件同步。*

## Active Technologies
- Python 3.11+ (后端) / TypeScript 5.0+ (前端) (001-llm-chat-page)

## Recent Changes
- 001-llm-chat-page: Added Python 3.11+ (后端) / TypeScript 5.0+ (前端)
