# Feature 004: 上下文记忆

> 状态: **已完成** — 已合并到 main

## 规范文件

| 文件 | 内容 |
|------|------|
| `spec.md` | 功能需求规范 |
| `plan.md` | 实施计划 |
| `tasks.md` | 任务清单 |
| `data-model.md` | 数据模型设计 |
| `behavior-model.md` | 行为模型 |
| `process-model.md` | 流程模型 |
| `rule-model.md` | 规则模型 |
| `research.md` | 技术调研 |
| `quickstart.md` | 快速入门 |
| `mem0-prompt-reference.md` | Mem0 Prompt 参考 |

## 实现模块

`apps/context/`（Prompt 构建 + Token 管理）+ `apps/memory/`（记忆 CRUD + 向量搜索）
