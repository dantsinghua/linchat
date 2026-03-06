# Implementation Plan: 文档解析进度展示与状态透传

**Branch**: `012-doc-parse-progress` | **Date**: 2026-03-06 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/012-doc-parse-progress/spec.md`

## Summary

用户上传 PDF 文档后 AI 解析需 3-5 分钟，期间无进度反馈。本特性在 SubAgent 轮询循环中推送 SSE 进度事件，前端消费后在聊天区域底部展示实时页级进度条。同时完善 Gateway 5 种状态的透传（新增 `incomplete` 处理），并为 frpc 隧道抖动添加网络层重试。

## Technical Context

**Language/Version**: Python 3.11+ (后端) / TypeScript 5.0+ (前端)
**Primary Dependencies**: Django 4.2+, LangGraph, httpx (后端) / Next.js 14+, React 18+, Zustand (前端)
**Storage**: Redis (SSE Pub/Sub 通道，已有)
**Testing**: pytest (后端) / npm run build 编译检查 (前端)
**Target Platform**: Linux 服务器 + 现代浏览器
**Project Type**: Web application (前后端分离)
**Performance Goals**: SSE 事件推送延迟 < 1s；进度条渲染不阻塞主线程
**Constraints**: 不新增 API 端点、不新增数据模型、不引入新依赖
**Scale/Scope**: 家庭场景单用户系统；改动 5 个文件，总计约 ~120 行

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 宪法条款 | 状态 | 说明 |
|----------|------|------|
| 1.1 关注点分离 | ✅ 通过 | 后端改动在 services 层和 subagent 工具层；前端改动在 stores/hooks/components 层 |
| 1.2 接口设计标准 | ✅ 通过 | 复用现有 SSE 推送管道（EventService → Redis Pub/Sub → EventSource），无新 API 端点 |
| 1.3 数据一致性 | ✅ 通过 | 无新数据模型写入；进度事件为瞬态推送，不持久化 |
| 2.1 Python 规范 | ✅ 通过 | 遵循 PEP 8 + Black + 类型注解 |
| 2.2 TypeScript 规范 | ✅ 通过 | 遵循 ESLint + Prettier + interface 定义 Props |
| 3.1 测试覆盖率 | ✅ 通过 | 为后端新增逻辑编写单元测试 |
| 4.1 认证与隔离 | ✅ 通过 | SSE 事件通过 `user_id` 粒度频道推送，符合隔离要求 |
| 4.3 LLM 异常处理 | ✅ 通过 | 本特性不直接调用 LLM；Gateway 轮询错误通过 DocumentParseError 体系处理 |
| 9.2 单用户模型 | ✅ 通过 | 无并发控制新增 |

**结论**: 零违规，无需 Complexity Tracking。

## Project Structure

### Documentation (this feature)

```text
specs/012-doc-parse-progress/
├── spec.md              # 功能规范
├── plan.md              # 本文件（实施计划）
├── research.md          # Phase 0（无未知项，记录确认）
├── data-model.md        # Phase 1（SSE 事件载荷定义）
├── quickstart.md        # Phase 1（快速验证指南）
├── contracts/           # Phase 1（SSE 事件契约）
│   └── sse-events.md
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── graph/
│   │   └── subagents/
│   │       └── document_agent.py    # ★ 改动：轮询循环 SSE 推送 + incomplete 处理
│   └── media/
│       └── services/
│           └── document.py          # ★ 改动：poll_task_status 网络重试
└── tests/
    └── apps/graph/
        └── test_document_agent.py   # ★ 改动：新增进度推送 + incomplete 测试

frontend/
└── src/
    ├── stores/
    │   └── chatStore.ts             # ★ 改动：新增 docParseProgress 状态
    ├── hooks/
    │   └── useAuth.tsx              # ★ 改动：SSE 事件写入 chatStore
    └── components/
        └── chat/
            └── MessageList.tsx      # ★ 改动：新增 DocParseProgressBar 内联组件
```

**Structure Decision**: Web application 模式，全部改动在已有目录结构内，不新增目录或文件（测试文件已存在）。
