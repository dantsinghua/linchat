# docs 目录指南

> 项目文档目录，包含项目架构、编码规范、部署指南、API 参考、Gateway 集成指南等。

## 文档概览

### 项目全景文档（autoresearch:learn 自动生成）

| 文件 | 用途 |
|------|------|
| `project-overview-pdr.md` | 项目概述与 PDR — 核心功能、技术栈、里程碑、设计决策 |
| `system-architecture.md` | 系统架构 — Mermaid 架构图、分层设计、数据流、安全机制、外部集成 |
| `codebase-summary.md` | 代码库摘要 — 目录结构、模块详解、数据模型、依赖清单 |
| `code-standards.md` | 编码规范 — Python/TS 风格、架构约束、安全要求、禁止事项 |
| `deployment-guide.md` | 部署指南 — Docker/systemd/services.sh、Nginx、frpc、故障排查 |
| `api-reference.md` | API 参考 — REST/SSE/WebSocket 端点、请求/响应格式 |
| `testing-guide.md` | 测试指南 — pytest/Jest/Playwright 体系、运行命令、Mock 模式 |
| `configuration-guide.md` | 配置指南 — settings.py 全量配置、环境变量、Redis DB 分配 |
| `changelog.md` | 变更日志 — 从 git log 自动生成 |

### 核心参考文档（开发时必读）

| 文件 | 用途 |
|------|------|
| `constitution-examples.md` | 宪法代码示例参考 — 包含数据一致性、缓存策略、异常处理、测试代码等 6 类实现模式，编码时**强制参考** |
| `upstream-integration-guide.md` | LLM Gateway 上游对接指南 v2.0 — 所有 API 端点、错误码体系、安全护栏行为的**权威来源** |
| `multimodal-api-guide.md` | LLM Gateway 多模态文档解析 API 对接指引 — PDF/DOCX 智能解析为 Markdown |
| `linchat-integration-guide.md` | LLM Gateway × LinChat 对接指南 v3.0.0 — Gateway API 端点、错误码体系 |
| `tts-websocket-api.md` | TTS 流式 WebSocket API — Gateway TTS 接口文档 |
| `langfuse-trace-peek.md` | Langfuse 追踪文档 — LLM 监控平台集成 |

### 里程碑需求文档

| 文件 | 用途 | 对应特性 |
|------|------|----------|
| `M1a-model-config-requirements.md` | 模型配置管理需求 — 模型注册迁移到 PostgreSQL、配置页面 | 003-model-config |
| `M1b-context-memory-requirements.md` | 上下文与记忆管理需求 — 动态上下文窗口、长期记忆 CRUD | 004-context-memory |
| `M1c-monitoring-requirements.md` | 动态监控需求 — Token 计数、使用率告警、Embedding 健康检查 | 005-context-monitoring |
| `M1c-implementation-detail.md` | 动态监控实施细化方案 — 基于现有代码分析的详细实现设计 | 005-context-monitoring |
| `M2-tools-and-monitoring-requirements.md` | 工具调用与实时监控需求 — 可扩展工具框架、3 个核心工具 | 006-subagent-tools |
| `M2b-home-assistant-requirements.md` | Home Assistant SubAgent 需求 — 自然语言控制智能家居 | 007-home-assistant-tools |
| `M4-voice-interaction-requirements.md` | 语音交互需求 v1.0 — 语音对话、全双工打断、成员识别 | 待开发 |
| `M4-voice-implementation-plan.md` | 语音交互实施方案 v1.0 — 基于 CleanS2S 框架的实现设计 | 待开发 |

### 已归档文档

| 文件 | 说明 |
|------|------|
| `M1-agent-framework-requirements.md.archived` | 已归档的 Agent 框架需求（被拆分为 M1a/M1b/M1c） |

## 关键参考

- **项目架构**: `system-architecture.md`（Mermaid 图 + 数据流）
- **编码时**: 必读 `constitution-examples.md`（宪法要求的实现模式）+ `code-standards.md`
- **部署运维**: `deployment-guide.md`（启动流程 + 故障排查）
- **Gateway 集成**: 以 `upstream-integration-guide.md` 为权威契约
- **多模态开发**: 参考 `multimodal-api-guide.md`
- **配置变更**: `configuration-guide.md`（全量配置参考）

<claude-mem-context>

</claude-mem-context>