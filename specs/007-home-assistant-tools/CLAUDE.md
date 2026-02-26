# Feature 007: Home Assistant SubAgent

> 状态: **已完成** — 已合并到 main

## 特性概述

将 Home Assistant 接入 LinChat SubAgent 体系，实现自然语言控制智能家居设备。覆盖三大场景：日常控制（开灯/调温度）、状态查询（设备列表/历史记录）、诊断修复（设备离线排查）。支持条件启用：仅当配置 HA_URL + HA_TOKEN 环境变量时才注册 ha_subagent，未配置时完全不暴露 HA 能力。敏感设备（如门锁）操作需二次确认。

## 规范文件

| 文件 | 内容 |
|------|------|
| `spec.md` | 功能需求规范（4 个用户故事：设备控制/状态查询/诊断修复/条件启用） |
| `plan.md` | 实施计划 |
| `tasks.md` | 任务清单（按 US1-US4 分组，含测试任务） |
| `data-model.md` | 数据模型 |
| `research.md` | 技术调研 |
| `quickstart.md` | 快速入门 |

## 契约文件

| 文件 | 内容 |
|------|------|
| `contracts/ha-api-contract.md` | HA REST API 契约（认证方式、状态查询/服务调用端点、错误映射表） |

## 检查清单

| 文件 | 内容 |
|------|------|
| `checklists/requirements.md` | 规范质量检查清单 |

## 相关代码位置

| 模块 | 路径 | 职责 |
|------|------|------|
| HA SubAgent | `backend/apps/graph/subagents/ha_agent.py` | HA SubAgent 定义和注册 |
| HA 工具函数 | `backend/apps/graph/tools/homeassistant.py` | ha_query/ha_control/ha_diagnose 三个 @tool 函数 |
| HA 客户端 | `backend/apps/graph/tools/ha_client.py` | HAClient（httpx 异步 HTTP 客户端）+ 自定义异常类 |
| HA 配置 | `backend/core/settings.py` | HA_URL/HA_TOKEN/HA_ENABLED/HA_BLOCKED_ENTITIES 配置项 |
