# Feature 006: 主对话流程 SubAgent 化重构

> 状态: **已完成** — 已合并到 main

## 特性概述

将 chat 主对话流程中直接调用 tools 的模式改为调用 subagent，每个 subagent 内部自行管理工具链。解决工具列表膨胀、prompt 臃肿、扩展困难三大问题。核心改动：主 agent 不再直接绑定具体工具，而是通过 `run_subagent()` 将任务委派给专属 subagent（搜索/记忆/代码执行），subagent 内部通过 `create_react_agent` 独立管理工具调用循环。

## 规范文件

| 文件 | 内容 |
|------|------|
| `spec.md` | 功能需求规范（5 个用户故事：普通对话/搜索/代码执行/记忆/复合任务） |
| `plan.md` | 实施计划 |
| `tasks.md` | 任务清单（按 Phase 分组，含 tags 过滤 Spike 验证） |
| `data-model.md` | 数据模型 |
| `research.md` | 技术调研 |
| `quickstart.md` | 快速入门 |

## 契约文件

| 文件 | 内容 |
|------|------|
| `contracts/ha-tools-contract.md` | Home Assistant 工具输入/输出契约（ha_query/ha_control/ha_diagnose） |

## 检查清单

| 文件 | 内容 |
|------|------|
| `checklists/requirements.md` | 规范质量检查清单 |

## 相关代码位置

| 模块 | 路径 | 职责 |
|------|------|------|
| SubAgent 基座 | `backend/apps/graph/subagents/base.py` | `run_subagent()` 工厂函数 + 异常处理 + 公共工具合并 |
| SubAgent 注册 | `backend/apps/graph/subagents/__init__.py` | `get_subagent_tools()` 注册表 |
| 搜索 SubAgent | `backend/apps/graph/subagents/search_agent.py` | 搜索任务委派 |
| 记忆 SubAgent | `backend/apps/graph/subagents/memory_agent.py` | 记忆读写委派 |
| 代码 SubAgent | `backend/apps/graph/subagents/code_agent.py` | Python REPL 执行委派 |
| 多模态 SubAgent | `backend/apps/graph/subagents/multimodal_agent.py` | 多模态推理委派 |
| 主 Agent | `backend/apps/graph/agent.py` | 主对话 agent（绑定 subagent 工具） |
| 工具集 | `backend/apps/graph/tools/` | search/memory/python_repl/context/homeassistant 等工具 |
