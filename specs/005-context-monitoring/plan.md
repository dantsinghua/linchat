# Implementation Plan: M1c 动态监控

**Branch**: `005-context-monitoring` | **Date**: 2026-02-04 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/005-context-monitoring/spec.md`

## Summary

为 LinChat 聊天平台实现上下文窗口动态监控系统。后端新增 TokenBreakdown 分部计数数据结构和 ContextMonitor 告警评估服务，在 Agent 流式响应期间通过已有 Event 流（Redis PubSub → SSE）每 500ms 推送完整 MonitorData 事件（含四个区块数据）；空闲时仅在用户发消息和告警级别变化时推送。前端新增 ContextStatusBar（输入框下方告警条）和 ContextMonitorPanel（右侧四区块监控侧边栏：大模型输入输出/当前上下文/当前记忆/当前进程）。附带工具结果 token 截断保护和 Embedding 健康检查定时任务。核心设计原则：Chat 流零改动，监控事件复用 Event 流。

## Technical Context

**Language/Version**: Python 3.11+ (后端) / TypeScript 5.0+ (前端)
**Primary Dependencies**: Django 4.2+, DRF 3.14+, uvicorn 0.30+, LangGraph, tiktoken, redis-py (async), Next.js 14+, React 18+, Zustand
**Storage**: PostgreSQL 15 (主存储), Redis (缓存/PubSub/Celery Broker DB2)
**Testing**: pytest + pytest-django (后端), Jest (前端)
**Target Platform**: Linux server (后端), Web browser (前端)
**Project Type**: Web application (前后端分离 Monorepo)
**Performance Goals**: 监控埋点额外延迟 < 100ms (p95)；500ms 推送间隔期间刷新抖动 < 16ms
**Constraints**: Chat 流 StreamChunk 6 种类型不得改变；监控失败不影响聊天主流程；所有隔离操作按 user_id 粒度
**Scale/Scope**: 单租户多用户，当前规模 < 100 并发用户

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 宪法条款 | 要求 | 本特性合规性 | 状态 |
|----------|------|-------------|------|
| 1.1 关注点分离 | 视图层禁止业务逻辑 | ContextMonitor 在服务层 (`apps/context/monitoring.py`)，不在视图层 | PASS |
| 1.1 分层架构 | 服务层封装业务逻辑 | TokenBreakdown 在类型层 (`types.py`)，监控评估在服务层 | PASS |
| 1.2 接口设计 | SSE 视图使用 ASGI 原生异步 | 复用现有 EventService 的 async 实现，不新建 SSE 端点 | PASS |
| 1.2 接口设计 | Chat 流消息类型 | FR-010 明确不改动 Chat 流的 6 种 type | PASS |
| 1.3 数据一致性 | PostgreSQL 为唯一可信来源 | TokenBreakdown 为运行时计算值，不持久化，无一致性问题 | PASS |
| 2.1 Python 规范 | 类型注解 + Google 文档字符串 | 所有新建类/方法将添加类型注解和文档字符串 | PASS |
| 2.2 TypeScript 规范 | Props 定义 interface | ContextStatusBar / MonitorSidebar 将使用 interface 定义 Props | PASS |
| 3.1 测试覆盖 | 服务层 95% | ContextMonitor 需 95% 覆盖率 | PASS |
| 4.1 数据隔离 | user_id 粒度 | EventService.publish_event 按 user_id 隔离频道 | PASS |
| 5.1 性能要求 | POST p95 < 300ms | 监控额外延迟 < 100ms，不影响 POST 性能目标 | PASS |
| 6.2 日志规范 | DEBUG/INFO/WARNING/ERROR 使用场景 | normal=DEBUG, warning=WARNING, critical=ERROR | PASS |
| 8.2 ASGI 配置 | 必须使用 uvicorn | 不影响现有 ASGI 配置 | PASS |

**GATE 结果: PASS** — 无宪法违规。

## Project Structure

### Documentation (this feature)

```text
specs/005-context-monitoring/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
backend/
├── apps/
│   ├── context/
│   │   ├── types.py              # [修改] 新增 TokenBreakdown dataclass
│   │   ├── builder.py            # [已修改] build_preamble() 已新增 conversation_history 参数 + build_conversation_history_block()；待新增 build_preamble_with_breakdown()
│   │   ├── templates/conversation_history.j2  # [已新建] 对话历史 Jinja2 模板
│   │   └── monitoring.py         # [新建] ContextMonitor + AlertLevel
│   ├── graph/
│   │   ├── agent.py              # [已修改] _wrap_prompt 在 tool loop 中跳过 name="conversation_history" 的 SystemMessage
│   │   ├── services/
│   │   │   └── agent_service.py  # [已修改] _build_prompt_preamble 历史改为 dict 列表传入 build_preamble(conversation_history=)；待扩展返回值 + execute 埋点 + 500ms 定时推送
│   │   └── tools/
│   │       ├── __init__.py       # [修改] 新增 cap_tool_result() 公共函数
│   │       ├── memory.py         # [修改] 调用 cap_tool_result() + 新增 tag 参数（激活语义标签）
│   │       ├── context.py        # [修改] 调用 cap_tool_result()
│   │       └── search.py         # [修改] 调用 cap_tool_result()
│   ├── common/
│   │   └── event_service.py      # [修改] 新增 publish_event() 通用方法
│   └── memory/
│       ├── services.py           # [修改] create_memory/update_memory 新增 tag 参数，保存到 tags 字段
│       └── tasks.py              # [修改] 新增 embedding_health_check 任务
├── core/
│   ├── settings.py               # [修改] LOGGING + MAX_TOOL_RESULT_TOKENS 常量
│   └── celery.py                 # [修改] beat_schedule 新增 embedding-health-check
└── tests/
    └── context/
        └── test_monitoring.py    # [新建] ContextMonitor 单元测试

frontend/
├── src/
│   ├── hooks/
│   │   └── useAuth.tsx           # [修改] handleSSEEvent 扩展 context_status 事件分发
│   ├── components/
│   │   └── chat/
│   │       ├── ContextStatusBar.tsx       # [新建] 上下文状态提示条组件
│   │       ├── ContextMonitorPanel.tsx    # [新建] 监控侧边栏（四区块，参照 design.tsx）
│   │       └── ContextMonitorPanel.design.tsx  # [参考设计稿] UI 设计参考
│   ├── types/
│   │   └── index.ts              # [修改] 新增 MonitorData / MemoryRecord / ToolProcess 等类型
│   └── app/
│       └── chat/
│           └── page.tsx          # [修改] 集成 MonitorSidebar + MonitorToggleButton + ContextStatusBar
```

**Structure Decision**: 遵循现有 Monorepo 结构，后端新增文件放在 `apps/context/` 模块下（与现有 types.py / builder.py 同级），前端新增组件放在 `components/chat/` 下。不新建模块目录。

## Complexity Tracking

> 无宪法违规，本节无内容。
