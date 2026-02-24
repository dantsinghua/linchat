# Feature 004: 上下文与记忆管理 (M1b)

> 状态: **已完成** — 已合并到 main

## 特性概述

构建动态上下文窗口管理和数据库化的长期记忆系统。核心能力包括：分层上下文组装（systemPrompt + userPrompt 五段式）、优先级驱动的压缩策略（d->c->b 顺序）、记忆 CRUD、向量混合检索（pgvector + pg_jieba）、记忆总结、LangGraph 多流程编排（chat/context/memory/cronMem 四个流程）。

前置依赖：M1a 模型配置表、PostgreSQL + pgvector + pg_jieba、OpenAI 兼容 Embedding 服务。

## 规范文件

| 文件 | 内容 |
|------|------|
| `spec.md` | 功能需求规范（7 个用户故事，P0-P2） |
| `plan.md` | 实施计划 |
| `tasks.md` | 任务清单（按用户故事分组） |
| `data-model.md` | 数据模型设计（UserMemory 表、向量索引） |
| `behavior-model.md` | 行为模型（压缩触发、记忆召回等行为规则） |
| `process-model.md` | 流程模型（chat/context/memory/cronMem 四流程编排） |
| `rule-model.md` | 规则模型（Token 计算、压缩优先级等规则定义） |
| `research.md` | 技术调研（tiktoken、pgvector、混合检索方案） |
| `quickstart.md` | 快速入门 |
| `mem0-prompt-reference.md` | Mem0 Prompt 参考（记忆提取 prompt 设计参考） |

## 契约文件

| 文件 | 内容 |
|------|------|
| `contracts/memory-api.yaml` | 记忆 CRUD REST API 契约（OpenAPI 3.1，基础路径 /api/v1/memories/） |

## 检查清单

| 文件 | 内容 |
|------|------|
| `checklists/requirements.md` | 需求验收检查清单（功能完整性、数据一致性、安全性验证） |

## 相关代码位置

| 模块 | 路径 | 职责 |
|------|------|------|
| 上下文构建 | `backend/apps/context/builder.py` | PromptBuilder 分层组装 |
| Token 计数 | `backend/apps/context/tokenizer.py` | tiktoken 精确计数 |
| 上下文裁剪 | `backend/apps/context/trimmer.py` | 按优先级压缩上下文 |
| 上下文加载 | `backend/apps/context/loader.py` | 加载各层上下文内容 |
| 上下文类型 | `backend/apps/context/types.py` | 数据结构定义 |
| 记忆模型 | `backend/apps/memory/models.py` | UserMemory 数据模型 |
| 记忆仓储 | `backend/apps/memory/repositories.py` | 向量检索 + 关键词匹配 |
| 记忆服务 | `backend/apps/memory/services.py` | 记忆 CRUD 业务逻辑 |
| 记忆定时任务 | `backend/apps/memory/tasks.py` | Celery 定时记忆总结 |
| 记忆视图 | `backend/apps/memory/views.py` | REST API 端点 |
