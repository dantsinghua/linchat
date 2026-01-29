# 实施计划：模型配置管理

**分支**：`003-model-config` | **日期**：2026-01-29 | **规范**：[spec.md](spec.md)
**输入**：特性规范 `/specs/003-model-config/spec.md`

## 摘要

将模型配置从环境变量迁移到 PostgreSQL 数据库，构建后端查看和修改 API 和前端独立设置页面，实现模型参数在线管理、即时生效。API Key 使用 SM4 加密存储，前端脱敏展示。现有聊天功能无缝切换到数据库配置源。**无缓存设计**：模型配置不使用 Redis 或内存缓存，每次请求直接从数据库读取最新配置，确保修改后即时生效（M1a 阶段仅 2 条记录，直接读库性能充裕）。

## 技术背景

**语言/版本**：Python 3.11+（后端）/ TypeScript 5.0+（前端）
**主要依赖**：Django 4.2+、DRF 3.14+、uvicorn 0.30+、Next.js 14+、React 18+、Zustand、Axios
**存储**：PostgreSQL（主存储）。注：Redis 为项目基础设施，本特性不使用缓存
**测试**：pytest（后端）、Jest（前端）
**目标平台**：Linux 服务器（ASGI 模式）
**项目类型**：Web 应用（前后端分离）
**性能目标**：API GET（简单查询）p95 < 100ms，GET（带数据库查询）p95 < 200ms，PUT p95 < 300ms
**约束条件**：SM4 加密 API Key、httpOnly Cookie 认证、单一生产环境
**规模/范围**：2 条模型记录（language + embedding），管理员使用

## 宪法检查

*关卡：必须在阶段 0 研究前通过。阶段 1 设计后复查。*

| 宪法条款 | 要求 | 合规状态 |
|----------|------|----------|
| 1.1 关注点分离 | views → services → repositories 分层 | ✅ 按分层架构设计 |
| 1.2 接口设计标准 | RESTful /api/v1/ 路径，统一响应格式 | ✅ 遵循统一响应格式 |
| 1.3 数据库策略 | PostgreSQL 为唯一可信来源，事务保护 | ✅ 仅涉及 PostgreSQL |
| 2.1 Python 后端规范 | Black/isort/类型注解/Google 文档字符串 | ✅ 遵循 |
| 2.2 TypeScript 前端规范 | ESLint/Prettier/严格模式/interface Props | ✅ 遵循 |
| 3.1 测试覆盖率 | 服务层 95%，总体 80% | ✅ 规划测试 |
| 4.1 身份认证与授权 | 权限控制，验证资源所有权 | ✅ 模型配置 API 仅限 admin 用户访问（FR-016），前端页面仅管理员可见（FR-017），复用 User.type 字段判定角色 |
| 4.2 数据保护 | API 密钥 SM4 加密存储 | ✅ SM4 加密 + 前端脱敏 |
| 5.1 响应时间 | 简单 GET p95 < 100ms，带 DB 查询 GET p95 < 200ms，PUT p95 < 300ms | ✅ 2 条记录简单查询，性能充裕 |

**关卡结论**：全部通过，无违规项。

## 项目结构

### 文档（本特性）

```text
specs/003-model-config/
├── plan.md              # 本文件
├── spec.md              # 特性规范
├── research.md          # 阶段 0 研究产物
├── data-model.md        # 阶段 1 数据模型
├── quickstart.md        # 阶段 1 快速启动指南
├── contracts/           # 阶段 1 API 合约
│   └── api.yaml         # OpenAPI 3.0 规范
└── tasks.md             # 阶段 2 任务清单（/speckit.tasks）
```

### 源代码（仓库根目录）

```text
backend/
├── apps/
│   ├── models/                    # 新增：模型配置模块
│   │   ├── __init__.py
│   │   ├── models.py              # Model 数据模型
│   │   ├── serializers.py         # 请求/响应序列化器
│   │   ├── views.py               # API 视图
│   │   ├── permissions.py         # 自定义权限类（IsAdminUser）
│   │   ├── services.py            # 业务逻辑层
│   │   ├── repositories.py        # 数据访问层
│   │   └── urls.py                # URL 路由
│   ├── chat/
│   │   ├── agent.py               # 改造：从数据库读取模型配置
│   │   └── services.py            # 改造：注入模型配置
│   └── common/
│       └── exceptions.py          # 可能新增：模型配置异常
├── core/
│   ├── settings.py                # 改造：移除 LLM_* 环境变量
│   └── urls.py                    # 改造：注册模型配置路由
└── tests/
    └── apps/
        ├── models/                # 新增：模型配置测试
        │   ├── test_models.py
        │   ├── test_services.py
        │   ├── test_repositories.py
        │   ├── test_serializers.py
        │   └── test_views.py
        └── chat/
            └── test_agent.py      # 新增：聊天集成测试

frontend/
├── src/
│   ├── app/
│   │   └── settings/              # 新增：设置页面
│   │       └── page.tsx           # 模型配置页面
│   ├── components/
│   │   └── settings/              # 新增：设置组件
│   │       ├── ModelConfigCard.tsx # 模型配置卡片（按类型分为 language 卡片和 embedding 卡片）
│   │       └── ModelConfigForm.tsx # 模型配置表单
│   ├── services/
│   │   └── modelService.ts        # 新增：模型配置 API 服务
│   ├── stores/
│   │   └── modelStore.ts          # 新增：模型配置状态
│   ├── types/
│   │   └── model.ts               # 新增：模型配置类型
│   └── app/
│       └── chat/
│           └── page.tsx            # 改造：header 区域新增"模型配置"入口（仅管理员可见）
└── tests/
    └── settings/                   # 新增：设置页面测试
```

**结构决策**：Web 应用结构，后端新增 `apps/models` Django 模块，前端新增 `app/settings` 路由页面和相关组件。遵循现有的分层架构模式。前端按模型类型以两张配置卡片形式分组展示（language 卡片和 embedding 卡片）。

## 复杂度追踪

无宪法违规项，不需要复杂性追踪。
