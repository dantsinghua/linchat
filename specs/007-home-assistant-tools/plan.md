# Implementation Plan: Home Assistant SubAgent

**Branch**: `007-home-assistant-tools` | **Date**: 2026-02-05 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/007-home-assistant-tools/spec.md`

## Summary

将 Home Assistant 接入 LinChat SubAgent 体系，通过 `ha_subagent` 委派智能家居任务，subagent 内部管理 `ha_query`、`ha_control`、`ha_diagnose` 三个工具。遵循现有 SubAgent 架构（`run_subagent()` 工厂 + 条件注册），使用 httpx 异步 HTTP 客户端对接 HA REST API，实现设备控制、状态查询、诊断修复三大能力。

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Django 4.2+, LangGraph, LangChain, httpx, redis-py (async)
**Storage**: Redis（速率限制键）
**Testing**: pytest, pytest-django, pytest-asyncio
**Target Platform**: Linux server (ASGI/uvicorn)
**Project Type**: Web application (backend only, no frontend changes)
**Performance Goals**: 设备控制指令 3 秒内返回确认回复
**Constraints**: HA HTTP 请求 10 秒超时，SubAgent 整体 60 秒超时
**Scale/Scope**: 单用户，10/min 控制速率限制

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Status | Notes |
|------|--------|-------|
| 1.1 关注点分离 | PASS | HAClient 封装数据访问，工具函数封装业务逻辑，ha_subagent 是入口 |
| 1.3 数据一致性 | N/A | 本特性不涉及数据库写入，纯运行时 API 代理 |
| 2.1 Python 代码规范 | PASS | 类型注解、Google 文档字符串、Black 格式化 |
| 3.1 测试覆盖率 | PASS | 计划 HAClient mock 测试 + 工具单元测试 + 集成测试 |
| 4.1 安全要求 | PASS | user_id 粒度速率限制，敏感操作确认保护，设备黑名单 |
| 4.3 大模型异常处理 | PASS | run_subagent() 已统一处理 LLM 异常 |
| 5.1 性能要求 | PASS | HA 请求 10s 超时，SubAgent 60s 超时 |

**Post-Phase 1 Re-check**: 无违规，设计完全遵循宪法要求。

## Project Structure

### Documentation (this feature)

```text
specs/007-home-assistant-tools/
├── plan.md              # This file
├── research.md          # Phase 0: 研究决策
├── data-model.md        # Phase 1: 运行时数据模型
├── quickstart.md        # Phase 1: 开发快速上手
├── contracts/
│   └── ha-api-contract.md  # HA REST API 接口约定
└── tasks.md             # Phase 2 output (/speckit.tasks)
```

### Source Code (repository root)

```text
backend/
├── apps/graph/
│   ├── subagents/
│   │   ├── __init__.py        # 修改: 条件注册 ha_subagent
│   │   └── ha_agent.py        # 新增: HA SubAgent 定义
│   └── tools/
│       ├── __init__.py        # 修改: 添加 HA_TOOLS 导出
│       ├── ha_client.py       # 新增: HAClient HTTP 封装
│       └── homeassistant.py   # 新增: 3 个 HA @tool 函数
├── core/
│   └── settings.py            # 修改: HA 配置项
└── tests/apps/graph/
    ├── test_ha_client.py      # 新增: HAClient 单元测试
    ├── test_ha_tools.py       # 新增: HA 工具单元测试
    └── test_ha_subagent.py    # 新增: 集成测试
```

**Structure Decision**: 纯后端改动，遵循现有 SubAgent 架构。新增 3 个文件（ha_agent.py、ha_client.py、homeassistant.py），修改 3 个已有文件（settings.py、subagents/__init__.py、tools/__init__.py），其中 tools/__init__.py 仅添加 1 行导入。

## Complexity Tracking

> 无宪法违规，无需记录。
