# Research: 语音模块迁移 — Gateway WebSocket → ASR 流式转录 + Agent Pipeline + TTS

**Date**: 2026-03-02 | **Status**: Complete

## 研究概述

本特性无 NEEDS CLARIFICATION 项。以下记录关键技术决策和研究发现。

---

## Decision 1: Gateway ASR WebSocket 连接模式

**Decision**: 使用 `auto_commit=true` 模式，Gateway 内置 VAD 自动触发转录

**Rationale**:
- Gateway 已内置 WebRTC VAD，无需 LinChat 侧实现 VAD 逻辑
- `auto_commit=true` 在 `speech_end` 后自动提交转录，减少一次 RTT
- 对比 `auto_commit=false`（需要手动 `commit` 触发转录），`auto_commit=true` 简化客户端实现

**Alternatives considered**:
- REST ASR (`POST /v1/audio/transcriptions`): 需要客户端缓存完整音频再上传，延迟高
- `auto_commit=false` + 手动 commit: 增加复杂度，仅在需要客户端侧后处理时有用
- 方法 A（完整 REST 流水线 VAD→ASR→LLM→TTS）: 延迟高，不适合实时交互

---

## Decision 2: Agent Pipeline 复用策略

**Decision**: 语音模式调用 `AgentService.execute()` — 与文字聊天完全一致

**Rationale**:
- `AgentService.execute()` 已封装完整的 LangGraph Pipeline（记忆召回 + 工具调用 + 上下文构建 + Langfuse 追踪 + 消息持久化）
- 复用现有代码避免功能分叉，语音/文字聊天的 AI 能力保持一致
- `AgentService.execute()` 返回 `AsyncGenerator[StreamChunk]`，支持逐块流式输出 → 适合逐句 TTS

**Alternatives considered**:
- 直接调用 Gateway `/v1/chat/completions`（旧方式）: 丢失所有 Agent 能力（工具调用、记忆、Langfuse）
- 创建独立的 VoiceAgent: 代码重复，功能分叉风险

---

## Decision 3: TTS 集成方式

**Decision**: 逐句切分 + REST TTS，每句独立合成

**Rationale**:
- Gateway TTS 为 REST 接口 (`POST /v1/audio/speech`)，非流式
- 按句子切分（中文：。！？；；；+ 英文：!? + 省略号：...）实现伪流式播放
- 每句独立合成，单句失败不影响其他句子（US4-AC3）
- 合成结果通过 WebSocket 二进制帧发送到前端

**Alternatives considered**:
- 等全部文字生成完再合成: 延迟过高，用户体验差
- 按字符定时切分: 可能在词中间切断，语音不自然

---

## Decision 4: 消息持久化时机

**Decision**: `AgentService.execute()` 内部创建 Message，VoicePipeline 事后补充 `is_voice=True` + 音频附件

**Rationale**:
- `AgentService.execute()` 在首 token 时自动创建 user+assistant Message（现有逻辑）
- 语音模式只需额外补充：(1) `is_voice=True` 标记，(2) 音频附件关联
- 避免修改 AgentService 核心逻辑，保持解耦

**Alternatives considered**:
- VoicePipeline 自行创建 Message: 与 AgentService 内部逻辑冲突（重复创建）
- 给 AgentService 传入 `is_voice` 参数: 侵入 AgentService 接口，增加耦合

---

## Decision 5: Gateway 连接断开处理

**Decision**: 立即终止语音会话，不自动重连（用户手动重新进入语音模式）

**Rationale**:
- 自动重连需要复杂的音频帧缓冲和状态同步
- 断线期间可能丢失音频帧，导致转录不完整
- 单用户部署场景下，手动重连的体验损失可接受
- 简化实现，降低 bug 风险

**Alternatives considered**:
- 自动重连 + 指数退避: 实现复杂，音频帧丢失问题无法完美解决
- 自动重连 + 音频缓冲: 缓冲区管理复杂，状态一致性难保证

---

## Decision 6: Enriched 模式处理

**Decision**: 删除 `voice_chat_enriched` 模式代码，合并为统一标准模式（FR-011）

**Rationale**:
- `voice_chat_enriched` 是为弥补旧 Gateway 无 Agent 能力而设计的临时方案
- 新架构下所有语音模式都走 Agent Pipeline，自带完整上下文构建
- 保留 enriched 代码增加维护负担且无实际价值

**Alternatives considered**:
- 保留为可选模式: 功能重复，维护负担
- 渐进废弃（标记 deprecated）: 增加过渡期复杂度，对单用户项目不值得

---

## 技术调研：现有代码分析

### VoiceConsumer 架构

- 采用 Mixin 模式拆分为 `SessionMixin` + `EventMixin` + `InferenceMixin`
- SessionMixin 管理 Gateway WebSocket 连接生命周期和音频帧转发
- EventMixin 处理 Gateway 事件分发（VAD、声纹、转录、推理响应）
- InferenceMixin 包含 enriched 推理和持续监听决策

**迁移影响**:
- SessionMixin: 替换 `GatewayClient` 为 `ASRStreamClient`
- EventMixin: 重写事件映射（Gateway ASR 事件 → 前端协议）
- InferenceMixin: 移除 enriched 逻辑，替换为 VoicePipeline 调用

### AgentService.execute() 调用链

```
AgentService.execute(user_id, thread_id, request_id, user_message)
  → _init_langfuse() — Langfuse 追踪初始化
  → build_prompt_preamble() — 构建系统提示
  → _build_context() — 上下文（记忆+历史+监控）
  → create_chat_agent() — LangGraph ReAct Agent
  → agent.astream(messages, config) — 流式执行
  → create_first_token_messages() — 首 token 时创建 user+assistant Message
  → yield StreamChunk(type, content) — 逐块输出
```

**关键点**:
- `thread_id` 格式: `f"user_{user_id}"` — 语音模式使用相同格式
- `request_id` 格式: `uuid.uuid4().hex` — 语音模式生成新的
- 返回 `AsyncGenerator[StreamChunk]`，支持 `content`/`done`/`error`/`interrupted`/`context_compacting`/`context_compacted` 类型（无 `tool_call` 类型，工具调用结果通过 `content` 类型输出）
