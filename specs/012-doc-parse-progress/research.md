# Research: 012-doc-parse-progress

**Date**: 2026-03-06
**Status**: Complete — 无未知项，全部技术方案基于已有基础设施

## 已确认技术决策

### 1. SSE 事件推送管道

- **Decision**: 复用现有 `EventService.publish_event()` → Redis Pub/Sub → 前端 EventSource
- **Rationale**: 已在 011-document-subagent-rag 中实现 `DOC_PARSE_PROGRESS` 事件类型和完整推送管道，SubAgent 路径只是缺少调用
- **Alternatives considered**: WebSocket 推送（不必要，SSE 单向推送已满足需求）

### 2. Gateway 5 种状态映射

- **Decision**: 后端透明传递 Gateway 的 pending/processing/completed/incomplete/failed，不做任何状态转换
- **Rationale**: 用户明确指令"只需要准确反应 llmgateway 的推理服务状态和进程"；Gateway 内部已有引擎恢复、重试、心跳机制
- **Alternatives considered**: 后端添加引擎恢复重试（明确拒绝 — 避免与 Gateway 守护机制重复）
- **Reference**: `docs/document parse.md` 第 3-4 节：任务状态机 + 重试机制

### 3. frpc 网络重试策略

- **Decision**: 仅对 `GATEWAY_ERROR`（连接断开/Server disconnected）重试，最多 3 次，间隔 2 秒
- **Rationale**: frpc STCP 隧道偶发 1-3 秒抖动，但 Gateway 端任务实际正常；不对 `GATEWAY_TIMEOUT` 或业务错误重试
- **Alternatives considered**: tenacity 装饰器（过重，此处仅一个方法需要重试）

### 4. 前端状态管理

- **Decision**: 将进度状态提升到 Zustand chatStore 全局状态，保留 CustomEvent 分发兼容现有 `useDocParse` Hook
- **Rationale**: MessageList 组件需要消费进度状态，通过 chatStore 是最直接的 Zustand 模式；保留 CustomEvent 避免破坏已有 Hook
- **Alternatives considered**: 仅使用 CustomEvent + useDocParse Hook（需要在 MessageList 中引入该 Hook，增加耦合）

### 5. 进度条 UI 模式

- **Decision**: 在 MessageList 底部添加内联 banner，与现有 `showVideoHint`（amber）和 `isCompacting`（blue）同级
- **Rationale**: 复用已有 UI 模式，用户已熟悉此位置的系统提示；使用 indigo 色系区分
- **Alternatives considered**: 弹窗/Toast 通知（打断用户阅读体验）；在上传按钮区域显示（位置不够醒目）
