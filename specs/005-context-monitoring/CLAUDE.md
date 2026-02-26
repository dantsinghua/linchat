# Feature 005: M1c 动态监控

> 状态: **已完成** — 已合并到 main

## 特性概述

为上下文与记忆管理（M1b）构建配套的实时监控系统。核心能力包括：Token 分部计数（9 字段 breakdown）、上下文使用率告警（70% warning / 90% critical）、前端监控侧边栏（四区块：大模型输入输出 / 当前上下文 / 当前记忆 / 当前进程）、500ms 实时推送（通过 SSE context_status 事件）。

设计风格参考 Windows 任务管理器/资源管理器的看板式监控布局。

## 规范文件

| 文件 | 内容 |
|------|------|
| `spec.md` | 功能需求规范（6 个用户故事，P1-P3） |
| `plan.md` | 实施计划 |
| `tasks.md` | 任务清单（按用户故事分组，US2->US3->US1 依赖链） |
| `data-model.md` | 数据模型设计（TokenBreakdown、MonitorData、AlertLevel） |
| `research.md` | 技术调研 |
| `quickstart.md` | 快速入门 |

## 契约文件

| 文件 | 内容 |
|------|------|
| `contracts/event-contract.md` | context_status SSE 事件契约（MonitorData 完整字段定义、Wire Format） |

## 检查清单

| 文件 | 内容 |
|------|------|
| `checklists/requirements.md` | 规范质量检查清单 |

## 相关代码位置

| 模块 | 路径 | 职责 |
|------|------|------|
| 监控核心 | `backend/apps/context/monitoring.py` | AlertLevel 告警级别 + ContextMonitor 数据组装 |
| 上下文类型 | `backend/apps/context/types.py` | TokenBreakdown、MonitorData 数据结构 |
| 前端监控面板 | `frontend/src/components/` | ContextMonitorPanel / MonitorSidebar 组件 |
| 前端类型 | `frontend/src/types/index.ts` | TokenBreakdown / MonitorData / AlertLevel 前端类型 |
